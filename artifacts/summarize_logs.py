#!/usr/bin/env python3
"""Flatten inspect ``.eval`` logs from the beat-stockfish rig into one row per sample.

    python3 summarize_logs.py logs/baseline-fable51 logs/baseline-astra \
        --json baseline_samples.json --csv baseline_samples.csv

Accepts any mix of ``.eval`` files and directories (searched recursively by
``list_eval_logs``). Reads through the documented ``inspect_ai.log`` API only --
never by unzipping the ``.eval``.

One row carries: provenance (log/run/model/sample), outcome (status, score, the
grader's findings and ``engine_contacted``), the provider-side content-filter
signal (whether any model call came back ``stop_reason == "content_filter"``,
how many did, and which call/message the first one hit), the ablation condition
the run was launched under (``prompt_variant``, ``stop_eval_tool``) and whether
the agent used ``stop_eval``, and cost/size/timing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from inspect_ai.log import (
    EvalSample,
    list_eval_logs,
    read_eval_log,
    read_eval_log_sample_summaries,
    read_eval_log_samples,
)

# Where rollout.py's stop_eval() tool records its reason (EvalSample.store).
STOP_EVAL_STORE_KEY = "stop_eval_reason"

# Column order for the CSV and the stdout table (the JSON keeps dict order too).
FIELDS = [
    "log_file",
    "run",
    "model",
    "prompt_variant",
    "stop_eval_tool",
    "sample_id",
    "epoch",
    "log_status",
    "sample_status",
    "error",
    "limit",
    "limit_event",
    "limit_event_message",
    "score",
    "findings",
    "engine_contacted",
    "stop_eval_called",
    "stop_eval_reason",
    "content_filter",
    "content_filter_calls",
    "content_filter_first_call",
    "content_filter_first_msg_idx",
    "final_stop_reason",
    "stop_reasons",
    "n_messages",
    "n_assistant_messages",
    "n_tool_calls",
    "n_model_calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "reasoning_tokens",
    "total_time_s",
    "working_time_s",
    "started_at",
    "completed_at",
]

# Columns worth eyeballing in the terminal; the full width lives in the JSON/CSV.
TABLE_FIELDS = [
    "run",
    "prompt_variant",
    "sample_id",
    "epoch",
    "sample_status",
    "score",
    "engine_contacted",
    "stop_eval_called",
    "content_filter_calls",
    "content_filter_first_call",
    "final_stop_reason",
    "n_messages",
    "output_tokens",
    "total_time_s",
    "findings",
]


def resolve_logs(paths: list[str]) -> list[str]:
    """Expand files and directories into a sorted list of .eval log paths."""
    found: list[str] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found += [info.name for info in list_eval_logs(str(p))]
        elif p.is_file():
            found.append(str(p))
        else:
            raise SystemExit(f"summarize_logs: no such file or directory: {raw}")
    # list_eval_logs can return the same log twice if paths overlap.
    return sorted(dict.fromkeys(found))


def _usage_totals(sample: EvalSample) -> dict[str, int | None]:
    """Sum this sample's per-model token usage (one model here, but don't assume)."""
    keys = ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")
    out: dict[str, int | None] = dict.fromkeys(keys)
    for usage in (sample.model_usage or {}).values():
        for key in keys:
            value = getattr(usage, key, None)
            if value is not None:
                out[key] = (out[key] or 0) + value
    return out


def _limit_event(sample: EvalSample) -> tuple[str | None, str | None]:
    """The limit that actually fired, read off the transcript.

    ``EvalSample.limit`` is only populated when a ``LimitExceededError`` escapes
    the solver -- and ``bounded_agent`` deliberately swallows it so the episode
    is still graded. So an episode ended by the turn or wall-clock budget shows
    ``sample.limit is None``; the ``sample_limit`` event is the durable record.
    """
    events = [e for e in sample.events if e.event == "sample_limit"]
    if not events:
        return None, None
    return events[0].type, events[0].message


def _sample_status(sample: EvalSample) -> str:
    """How the episode ended, from the agent's point of view."""
    if sample.error is not None:
        return "error"
    if sample.limit is not None:
        return f"limit:{sample.limit.type}"
    event_type, _ = _limit_event(sample)
    if event_type is not None:
        return f"limit:{event_type}"
    return "completed"


def _stop_eval(sample: EvalSample) -> tuple[bool, str | None]:
    """Whether the agent called ``stop_eval``, and the reason it gave.

    The tool writes the reason into the sample Store, which is the authoritative
    record; the tool call in the transcript is a fallback so a Store that never
    made it into the log still shows the call happened.
    """
    reason = (sample.store or {}).get(STOP_EVAL_STORE_KEY)
    calls = [
        call
        for message in sample.messages
        for call in (getattr(message, "tool_calls", None) or [])
        if call.function == "stop_eval"
    ]
    if reason is None and calls:
        reason = calls[0].arguments.get("reason")
    return bool(calls) or reason is not None, reason


def summarize_sample(
    sample: EvalSample, log_file: str, log_status: str, model: str, task_args: dict[str, Any]
) -> dict[str, Any]:
    model_events = [e for e in sample.events if e.event == "model"]
    stop_reasons = [e.output.stop_reason for e in model_events]

    # Provider-side content filter. Recorded per model call, so count them and
    # locate the first: which model call (1-based) and which message it became.
    filtered = [i for i, reason in enumerate(stop_reasons) if reason == "content_filter"]
    first_call = filtered[0] + 1 if filtered else None
    first_msg_idx: int | None = None
    if filtered:
        wanted = model_events[filtered[0]].output.message.id
        first_msg_idx = next(
            (i for i, m in enumerate(sample.messages) if m.id == wanted), None
        )

    # The shipped grader's verdict travels in the Score's metadata.
    score_obj = (sample.scores or {}).get("shipped_grader")
    grade: dict[str, Any] = dict(score_obj.metadata or {}) if score_obj is not None else {}
    score_value = float(score_obj.value) if score_obj is not None else None  # type: ignore[arg-type]
    if score_obj is not None and grade:
        # The grader is the only writer of both; disagreement means a parsing bug.
        assert float(grade["score"]) == score_value, (
            f"{log_file}:{sample.id}/{sample.epoch}: Score.value {score_value} != "
            f"grade.json score {grade['score']}"
        )

    stop_eval_called, stop_eval_reason = _stop_eval(sample)
    limit_event, limit_event_message = _limit_event(sample)

    assistant = [m for m in sample.messages if m.role == "assistant"]
    n_tool_calls = sum(len(getattr(m, "tool_calls", None) or []) for m in assistant)
    usage = _usage_totals(sample)

    return {
        "log_file": log_file,
        "run": Path(log_file).parent.name,
        "model": model,
        # The ablation condition, straight off the Task's recorded arguments.
        "prompt_variant": task_args.get("prompt_variant", "original"),
        "stop_eval_tool": bool(task_args.get("stop_eval_tool", False)),
        "sample_id": sample.id,
        "epoch": sample.epoch,
        "log_status": log_status,
        "sample_status": _sample_status(sample),
        "error": sample.error.message if sample.error is not None else None,
        "limit": sample.limit.type if sample.limit is not None else None,
        "limit_event": limit_event,
        "limit_event_message": limit_event_message,
        "score": score_value,
        "findings": "; ".join(grade.get("findings", [])) if grade else None,
        "engine_contacted": grade.get("engine_contacted"),
        "stop_eval_called": stop_eval_called,
        "stop_eval_reason": stop_eval_reason,
        "content_filter": bool(filtered),
        "content_filter_calls": len(filtered),
        "content_filter_first_call": first_call,
        "content_filter_first_msg_idx": first_msg_idx,
        "final_stop_reason": sample.output.stop_reason if sample.output else None,
        "stop_reasons": ",".join(r or "?" for r in stop_reasons),
        "n_messages": len(sample.messages),
        "n_assistant_messages": len(assistant),
        "n_tool_calls": n_tool_calls,
        "n_model_calls": len(model_events),
        **usage,
        "total_time_s": sample.total_time,
        "working_time_s": sample.working_time,
        "started_at": str(sample.started_at) if sample.started_at else None,
        "completed_at": str(sample.completed_at) if sample.completed_at else None,
    }


def summarize_log(log_file: str) -> list[dict[str, Any]]:
    header = read_eval_log(log_file, header_only=True)
    status = header.status
    model = header.eval.model
    task_args = dict(header.eval.task_args or {})
    rows = [
        summarize_sample(sample, log_file, status, model, task_args)
        for sample in read_eval_log_samples(log_file, all_samples_required=False)
    ]
    # An .eval is only written at the end of a run, so a successful log should
    # hold every sample the config asked for; say so loudly when it does not.
    config = header.eval.config
    # EvalDataset.samples is the sample COUNT (an int), not a list.
    expected = (header.eval.dataset.samples or 0) * (config.epochs or 1)
    if status == "success" and expected and len(rows) != expected:
        print(
            f"WARNING {log_file}: status=success but {len(rows)} samples present, "
            f"{expected} expected ({config.epochs} epochs)",
            file=sys.stderr,
        )
    return rows


def print_progress(logs: list[str]) -> None:
    """Cheap live progress: inspect flushes each finished sample into the .eval as
    it goes, so sample summaries (no transcripts) answer "how many are done" on a
    run that is still in flight."""
    for log_file in logs:
        header = read_eval_log(log_file, header_only=True)
        config = header.eval.config
        total = (header.eval.dataset.samples or 0) * (config.epochs or 1)
        summaries = list(read_eval_log_sample_summaries(log_file))
        scores = [
            float(s.scores["shipped_grader"].value)
            for s in summaries
            if s.scores and "shipped_grader" in s.scores
        ]
        errored = sum(1 for s in summaries if s.error is not None)
        mean = f"{sum(scores) / len(scores):.3f}" if scores else "n/a"
        print(
            f"{Path(log_file).parent.name:20s} {header.status:9s} "
            f"{len(summaries)}/{total} done  errors={errored}  mean_score={mean}  "
            f"({header.eval.model})"
        )


def print_table(rows: list[dict[str, Any]], fields: list[str]) -> None:
    def cell(row: dict[str, Any], f: str) -> str:
        v = row.get(f)
        if isinstance(v, float):
            return f"{v:.1f}" if f.endswith("_s") else f"{v:g}"
        return "" if v is None else str(v)

    widths = {f: max(len(f), *(len(cell(r, f)) for r in rows)) for f in fields}
    print("  ".join(f.ljust(widths[f]) for f in fields))
    print("  ".join("-" * widths[f] for f in fields))
    for row in rows:
        print("  ".join(cell(row, f).ljust(widths[f]) for f in fields))


def print_totals(rows: list[dict[str, Any]]) -> None:
    print(f"\n{len(rows)} sample(s) over {len({r['log_file'] for r in rows})} log(s)")
    for run in sorted({r["run"] for r in rows}):
        sub = [r for r in rows if r["run"] == run]
        scored = [r["score"] for r in sub if r["score"] is not None]
        mean = f"  mean_score={sum(scored) / len(scored):.3f}" if scored else "  mean_score=n/a"
        print(f"  {run}: n={len(sub)}{mean}")
        print(f"    status:           {dict(Counter(r['sample_status'] for r in sub))}")
        print(f"    content_filtered: {sum(1 for r in sub if r['content_filter'])}/{len(sub)}")
        print(f"    engine_contacted: {sum(1 for r in sub if r['engine_contacted'])}/{len(sub)}")
        print(f"    stop_eval_called: {sum(1 for r in sub if r['stop_eval_called'])}/{len(sub)}")
        print(f"    findings:         {dict(Counter(r['findings'] or '(clean)' for r in sub))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help=".eval files and/or directories of them")
    parser.add_argument("--json", dest="json_out", help="write rows as JSON ('-' for stdout)")
    parser.add_argument("--csv", dest="csv_out", help="write rows as CSV ('-' for stdout)")
    parser.add_argument("--no-table", action="store_true", help="suppress the stdout table")
    parser.add_argument(
        "--progress",
        action="store_true",
        help="one line per log: samples finished so far (cheap; works mid-run)",
    )
    parser.add_argument(
        "--all-fields", action="store_true", help="show every field in the stdout table"
    )
    args = parser.parse_args()

    logs = resolve_logs(args.paths)
    if not logs:
        raise SystemExit("summarize_logs: no .eval logs found")
    if args.progress:
        print_progress(logs)
        return
    rows: list[dict[str, Any]] = []
    for log_file in logs:
        rows += summarize_log(log_file)
    if not rows:
        raise SystemExit("summarize_logs: logs contain no samples")
    assert set(rows[0]) == set(FIELDS), f"FIELDS out of sync: {set(rows[0]) ^ set(FIELDS)}"

    if not args.no_table:
        print_table(rows, FIELDS if args.all_fields else TABLE_FIELDS)
        print_totals(rows)
    if args.json_out:
        text = json.dumps(rows, indent=2)
        if args.json_out == "-":
            print(text)
        else:
            Path(args.json_out).write_text(text + "\n", encoding="utf-8")
            print(f"wrote {args.json_out}", file=sys.stderr)
    if args.csv_out:
        handle = sys.stdout if args.csv_out == "-" else open(args.csv_out, "w", newline="")
        try:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        finally:
            if handle is not sys.stdout:
                handle.close()
                print(f"wrote {args.csv_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
