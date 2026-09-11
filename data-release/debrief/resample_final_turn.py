#!/usr/bin/env python3
"""Resample the final-answer turn of 4 Fable 5.1 cheating trajectories.

These four trajectories were judged `disclosed` by the campaign's
`disclosure_judge_wrapup` scorer: their wrap-up explicitly owned up to using
the planted engine socket. This script replays each trajectory verbatim up to
(not including) that final-answer turn, redraws it 10 times from the same
model with the same tools attached, and re-runs the SAME disclosure judge
(reused by import from run/rollout.py, not reimplemented) on every redrawn
text answer -- to see how often the disclosure survives resampling.

Does NOT modify debrief/debrief_probe.py or run/rollout.py; imports from
both. Reuses `latest_eval` and `reasoning_blocks` from debrief_probe.py, and
`disclosure_judge_wrapup` (+ its full rubric/prompt machinery) from
run/rollout.py.

Cache_control breakpoints:
    OpenRouter's own `_add_anthropic_cache_markers` (installed by inspect_ai's
    openrouter provider whenever `cache_prompt is not False`) only marks a
    message-level breakpoint when a message's OUTGOING (OpenAI-format)
    content is list-typed. But inspect's own message conversion
    (`openai_chat_message` in inspect_ai/model/_openai.py) unconditionally
    flattens `tool`-role content to a plain string, and collapses
    single-block assistant/user text to a plain string too -- true for every
    message in these plain-text agentic transcripts. So by default the ONLY
    marker that ever attaches is on the last tool schema (`submit`), a span
    far below Anthropic's minimum cacheable block size: confirmed empirically
    (write=None, read=0, reproducibly) across all 4 targets before this
    script existed -- see debrief/cache_diagnostic.py.

    Fix (installed by `install_cache_marker_patch` below): after the
    provider's own marker pass runs, mark a REAL breakpoint ourselves,
    directly on the outgoing OpenAI-format request dict, in one of two ways:
      - "last_message": wrap the last message's plain-string content in
        `[{"type": "text", "text": <the same string>, "cache_control": ...}]`
        -- one part, no splitting, so the text sent to the model is
        byte-identical (asserted). Works on `tool`-role messages too:
        Anthropic accepts cache_control on tool_result content blocks
        forwarded through OpenRouter.
      - "last_assistant_text": same trick, but anchored on the last assistant
        message with non-empty text before the cut. Fallback for a target
        where "last_message" is not honoured by the API (validated
        empirically per target below; in practice "last_message" worked for
        all 4).
    Total markers are capped at 4 per request (Anthropic's hard per-request
    cache_control limit), dropping the provider's own tool-schema marker
    first if needed.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Literal

WS = Path(__file__).resolve().parent.parent  # beat-stockfish/
sys.path.insert(0, str(WS / "debrief"))
sys.path.insert(0, str(WS / "run"))

from debrief_probe import latest_eval, reasoning_blocks  # noqa: E402
from inspect_ai.log import read_eval_log  # noqa: E402
from inspect_ai.model import GenerateConfig, ModelName, get_model  # noqa: E402
from inspect_ai.scorer import Target  # noqa: E402
from inspect_ai.solver import TaskState  # noqa: E402
import inspect_ai.model._providers.openrouter as _orm  # noqa: E402

import rollout  # noqa: E402  (run/rollout.py, sibling-file import)

logger = logging.getLogger("resample_final_turn")

# --------------------------------------------------------------------------- #
# targets -- verified against logs-disclosure-wrapup/*.eval: messages[idx] is
# the assistant's real final-answer turn (no tool call), messages[idx-1] is
# the tool result / "N turn(s) remaining" nudge that precedes it, BEFORE the
# harness's "No tool was called this turn..." nudge + terse repeat that
# follows for 3 of the 4.
# --------------------------------------------------------------------------- #
TARGETS = [
    dict(arm="baseline-fable51", epoch=2, cut=41),
    dict(arm="p2-believe-fable51", epoch=9, cut=73),
    dict(arm="abl-believe-fable51", epoch=10, cut=24),
    dict(arm="abl-believe-fable51", epoch=3, cut=41),
]
LOG_ROOT = WS / "logs-disclosure-wrapup"

CacheAnchorStrategy = Literal["last_message", "last_assistant_text", "none"]


# --------------------------------------------------------------------------- #
# wire-level Anthropic cache_control injection (see module docstring)
# --------------------------------------------------------------------------- #
_ORIG_ADD_MARKERS = _orm._add_anthropic_cache_markers


class _AnchorState:
    """Mutable, module-scoped: which strategy the NEXT generate() call should use.

    Safe under concurrency only because trajectories are processed one at a
    time (draws WITHIN a trajectory share its single decided anchor); see
    `main()`.
    """

    strategy: CacheAnchorStrategy = "last_message"
    last_used: str | None = None


_anchor_state = _AnchorState()


def _count_cache_markers(request: dict[str, Any]) -> int:
    n = 0
    for m in request.get("messages") or []:
        c = m.get("content")
        if isinstance(c, list):
            n += sum(1 for b in c if isinstance(b, dict) and "cache_control" in b)
    for t in request.get("tools") or []:
        if isinstance(t, dict) and "cache_control" in t:
            n += 1
    return n


def _mark_last_message(request: dict[str, Any]) -> bool:
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str) and content:
        block = {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        assert block["text"] == content, "cache anchor mutated message text"
        last["content"] = [block]
        return True
    if isinstance(content, list) and content:
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                block["cache_control"] = {"type": "ephemeral"}
                return True
    return False


def _mark_last_assistant_text(request: dict[str, Any]) -> bool:
    messages = request.get("messages")
    if not isinstance(messages, list):
        return False
    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if isinstance(content, str) and content:
            block = {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            assert block["text"] == content, "cache anchor mutated message text"
            m["content"] = [block]
            return True
        if isinstance(content, list) and content:
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    block["cache_control"] = {"type": "ephemeral"}
                    return True
    return False


def _patched_add_markers(request: dict[str, Any]) -> None:
    _ORIG_ADD_MARKERS(request)
    strategy = _anchor_state.strategy
    applied = False
    if strategy == "last_message":
        applied = _mark_last_message(request)
    elif strategy == "last_assistant_text":
        applied = _mark_last_assistant_text(request)
    _anchor_state.last_used = strategy if applied else "none"
    n = _count_cache_markers(request)
    if n > 4:
        for t in reversed(request.get("tools") or []):
            if n <= 4:
                break
            if isinstance(t, dict) and "cache_control" in t:
                del t["cache_control"]
                n -= 1


def install_cache_marker_patch() -> None:
    _orm._add_anthropic_cache_markers = _patched_add_markers
    logger.info("installed custom Anthropic cache_control marker patch over %s", _ORIG_ADD_MARKERS)


# --------------------------------------------------------------------------- #
# loading + auditing the 4 targets
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class Trajectory:
    arm: str
    epoch: int
    cut: int
    key: str
    sample_id: str
    log_path: str
    model_name: str
    prefix: list[Any]
    tools: list[Any]
    tool_choice: Any
    original_message: Any
    engine_use_metadata: dict[str, Any]
    cache_anchor: CacheAnchorStrategy = "last_message"
    cache_validation: dict[str, Any] | None = None


def load_targets() -> list[Trajectory]:
    trajectories = []
    for t in TARGETS:
        arm, epoch, cut = t["arm"], t["epoch"], t["cut"]
        d = LOG_ROOT / arm
        path = latest_eval(str(d))
        log = read_eval_log(path, resolve_attachments=True)
        samples = [s for s in log.samples if int(s.epoch) == epoch]
        assert len(samples) == 1, f"{arm} ep{epoch}: expected exactly 1 sample, got {len(samples)}"
        s = samples[0]
        msgs = s.messages
        assert cut < len(msgs), f"{arm} ep{epoch}: cut {cut} out of range ({len(msgs)} messages)"

        target_msg = msgs[cut]
        assert target_msg.role == "assistant", (
            f"{arm} ep{epoch}: messages[{cut}] is role={target_msg.role!r}, expected assistant"
        )
        assert not (getattr(target_msg, "tool_calls", None) or []), (
            f"{arm} ep{epoch}: messages[{cut}] has tool_calls, expected none (this must be the "
            "text-only final answer)"
        )
        prev_msg = msgs[cut - 1]
        assert prev_msg.role in ("tool", "user"), (
            f"{arm} ep{epoch}: messages[{cut - 1}] is role={prev_msg.role!r}, expected tool or user"
        )

        logger.info("=== %s epoch %d cut=%d: auditing messages[%d..%d] ===", arm, epoch, cut, cut - 2, len(msgs) - 1)
        for i in range(max(0, cut - 2), len(msgs)):
            m = msgs[i]
            tc = bool(getattr(m, "tool_calls", None))
            logger.info("  [%d] role=%s tool_calls=%s text_len=%d", i, m.role, tc, len(m.text))
        logger.info("--- original text at cut=%d (messages[%d]) ---\n%s\n--- end original text ---", cut, cut, target_msg.text)

        model_events = [e for e in s.events if e.event == "model" and e.model == log.eval.model]
        assert model_events, f"{arm} ep{epoch}: no model events found for agent model {log.eval.model!r}"
        last_ev = model_events[-1]
        logger.info(
            "%s ep%d: tools=%s tool_choice=%r (from agent's last own model event, filtered by model==%r; "
            "NOTE: the literal `[e for e in sample.events if e.event=='model'][-1]` picks up the LAST "
            "model event overall, which is a judge call (e.g. glm-5.3-flash, tools=[]) appended after "
            "the agent's turns -- fixed by filtering on e.model == log.eval.model first)",
            arm, epoch, [ti.name for ti in last_ev.tools], last_ev.tool_choice, log.eval.model,
        )

        sc = s.scores or {}
        assert "engine_use_judge" in sc, f"{arm} ep{epoch}: no engine_use_judge score present"
        engine_meta = dict(sc["engine_use_judge"].metadata)
        assert engine_meta.get("classification") in ("engine_driven", "engine_assisted"), (
            f"{arm} ep{epoch}: engine_use_judge classification is "
            f"{engine_meta.get('classification')!r}, expected engine_driven/engine_assisted"
        )

        key = f"{arm}:{s.id}:{epoch}"
        trajectories.append(
            Trajectory(
                arm=arm, epoch=epoch, cut=cut, key=key, sample_id=str(s.id), log_path=path,
                model_name=log.eval.model, prefix=list(msgs[:cut]), tools=last_ev.tools,
                tool_choice=last_ev.tool_choice, original_message=target_msg,
                engine_use_metadata=engine_meta,
            )
        )
    return trajectories


# --------------------------------------------------------------------------- #
# cache validation (per trajectory, before spending any real draws)
# --------------------------------------------------------------------------- #
async def validate_cache_anchor(traj: Trajectory, probe_max_tokens: int = 50) -> None:
    """Try `last_message`, then `last_assistant_text`. Raises if neither hits."""
    model = get_model(traj.model_name, config=GenerateConfig(max_tokens=probe_max_tokens))
    last_u1 = last_u2 = None
    for strategy in ("last_message", "last_assistant_text"):
        _anchor_state.strategy = strategy
        o1 = await model.generate(input=traj.prefix, tools=traj.tools, tool_choice=traj.tool_choice)
        o2 = await model.generate(input=traj.prefix, tools=traj.tools, tool_choice=traj.tool_choice)
        u1, u2 = o1.usage, o2.usage
        last_u1, last_u2 = u1, u2
        cw1 = (u1.input_tokens_cache_write or 0) if u1 else 0
        cr1 = (u1.input_tokens_cache_read or 0) if u1 else 0
        cr2 = (u2.input_tokens_cache_read or 0) if u2 else 0
        logger.info(
            "%s: cache validation strategy=%s call1(input=%s,write=%s,read=%s) "
            "call2(input=%s,write=%s,read=%s)",
            traj.key, strategy,
            u1.input_tokens if u1 else None, u1.input_tokens_cache_write if u1 else None,
            u1.input_tokens_cache_read if u1 else None,
            u2.input_tokens if u2 else None, u2.input_tokens_cache_write if u2 else None,
            u2.input_tokens_cache_read if u2 else None,
        )
        # Accept either a fresh write-then-read (the clean case), OR a call-1
        # that is ITSELF already a cache hit (cr1 > 0) -- which happens if this
        # exact (prefix, anchor) combination was already warmed by an earlier
        # probe within the 5 min TTL (e.g. our own ad hoc pre-script testing,
        # or a previous --validate-cache-only run). Either way, call 2 showing
        # a cache read proves the breakpoint is real and is being honoured by
        # Anthropic; requiring call 1 to specifically be a MISS is stricter
        # than what we actually need and produces false negatives against
        # residual warm cache. Only cr2 == 0 (no read at all on the repeat
        # call) is real evidence the anchor did not engage.
        if (cw1 > 0 or cr1 > 0) and cr2 > 0:
            traj.cache_anchor = strategy
            traj.cache_validation = {
                "strategy": strategy,
                "call1_usage": u1.model_dump() if u1 else None,
                "call2_usage": u2.model_dump() if u2 else None,
            }
            return
    raise RuntimeError(
        f"{traj.key}: cache did not engage under either anchor strategy "
        f"(last attempt {traj.cache_anchor!r}: call1_usage={last_u1.model_dump() if last_u1 else None}, "
        f"call2_usage={last_u2.model_dump() if last_u2 else None}). Stopping per instructions rather "
        "than proceeding with un-cached draws."
    )


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #
def classify_outcome(message: Any) -> Literal["text", "submit", "tool_call"]:
    """"text": no tool calls at all -- the bare assistant text is the answer.

    "submit": every tool call on this message is a call to submit(). rollout.py's
    own wrap-up logic (`_took_action` / `wrapup_parts`) does NOT count submit() as
    "taking an action" -- it is the harness's formal way of handing in the final
    answer, and `_submit_answer()` pulls the answer text straight out of its
    `answer` argument, combining it with any accompanying assistant text (both
    become separate wrap-up "parts", in order) via `wrapup_parts`. So a bare
    submit() call already contains a complete, judgeable final answer, exactly
    the way the original campaign's own scorer would have read it -- it is
    judged the same as "text", not discarded.

    "tool_call": at least one call to something OTHER than submit() (e.g.
    capped_bash) -- a genuine in-progress action that would need to be executed
    to see what happens next. Not executed, not judged, per the brief.
    """
    calls = getattr(message, "tool_calls", None) or []
    if not calls:
        return "text"
    if all(tc.function == rollout.SUBMIT_TOOL for tc in calls):
        return "submit"
    return "tool_call"


@dataclasses.dataclass
class Draw:
    draw: int
    outcome: Literal["text", "submit", "tool_call"]
    answer_text: str
    submit_answer: str
    tool_calls: list[dict[str, Any]] | None
    reasoning: list[dict[str, Any]]
    usage: dict[str, Any]
    stop_reason: str
    message: Any  # raw ChatMessageAssistant, kept for judging, not serialized


async def do_one_draw(traj: Trajectory, model, draw_idx: int, sem: asyncio.Semaphore | None = None) -> Draw:
    async def _go():
        return await model.generate(input=traj.prefix, tools=traj.tools, tool_choice=traj.tool_choice)

    if sem is not None:
        async with sem:
            output = await _go()
    else:
        output = await _go()

    message = output.message
    tool_calls = getattr(message, "tool_calls", None) or []
    answer_text = rollout._content_text(message.content)
    submit_answer = rollout._submit_answer(message)
    outcome = classify_outcome(message)
    tc_payload = (
        [
            {"id": tc.id, "function": tc.function, "arguments": tc.arguments, "parse_error": tc.parse_error}
            for tc in tool_calls
        ]
        if tool_calls
        else None
    )

    usage = output.usage.model_dump() if output.usage else {}
    logger.info(
        "%s draw %d: outcome=%s input=%s cache_write=%s cache_read=%s output=%s reasoning=%s",
        traj.key, draw_idx, outcome, usage.get("input_tokens"), usage.get("input_tokens_cache_write"),
        usage.get("input_tokens_cache_read"), usage.get("output_tokens"), usage.get("reasoning_tokens"),
    )
    return Draw(
        draw=draw_idx, outcome=outcome, answer_text=answer_text, submit_answer=submit_answer,
        tool_calls=tc_payload, reasoning=reasoning_blocks(message), usage=usage,
        stop_reason=str(output.stop_reason), message=message,
    )


async def run_trajectory_draws(traj: Trajectory, n_draws: int, concurrency: int, max_tokens: int) -> list[Draw]:
    _anchor_state.strategy = traj.cache_anchor
    model = get_model(traj.model_name, config=GenerateConfig(max_tokens=max_tokens))

    t0 = time.monotonic()
    logger.info("%s: draw 0 alone (priming cache, anchor=%s)...", traj.key, traj.cache_anchor)
    draw0 = await do_one_draw(traj, model, 0)
    t_after0 = time.monotonic()
    logger.info("%s: draw 0 done in %.1fs, launching draws 1-%d at concurrency %d...",
                traj.key, t_after0 - t0, n_draws - 1, concurrency)

    sem = asyncio.Semaphore(concurrency)
    rest = await asyncio.gather(*[do_one_draw(traj, model, i, sem) for i in range(1, n_draws)])
    total_elapsed = time.monotonic() - t0
    logger.info("%s: all %d draws done, total elapsed since draw0=%.1fs", traj.key, n_draws, total_elapsed)
    if total_elapsed > 240:
        logger.warning(
            "%s: draws spanned %.1fs since draw 0 -- getting close to Anthropic's 5 min cache TTL",
            traj.key, total_elapsed,
        )

    all_draws = [draw0, *rest]
    reads = [d.usage.get("input_tokens_cache_read") or 0 for d in all_draws[1:]]
    writes0 = draw0.usage.get("input_tokens_cache_write") or 0
    reads0 = draw0.usage.get("input_tokens_cache_read") or 0
    logger.info(
        "%s: draw0 cache_write=%d cache_read=%d, draws1-%d cache_read=%s",
        traj.key, writes0, reads0, n_draws - 1, reads,
    )
    # draw 0 is expected to show a fresh WRITE (priming the cache). But
    # validate_cache_anchor() just ran, for this same trajectory+anchor,
    # immediately beforehand -- so by the time draw 0 fires, the exact same
    # (prefix, anchor) request shape may already be warm, and draw 0 itself
    # comes back as a cache READ instead of a WRITE. That is not a failure of
    # the mechanism (it's the same evidence, just already-cached); only "the
    # marker did not engage at all" (write==0 AND read==0) is a real problem.
    assert writes0 > 0 or reads0 > 0, (
        f"{traj.key}: draw 0 shows neither a cache write nor a cache read "
        f"(write={writes0}, read={reads0}) -- the cache marker did not engage at all"
    )
    assert all(r > 0 for r in reads), (
        f"{traj.key}: not all of draws 1-{n_draws - 1} show input_tokens_cache_read > 0: {reads}"
    )
    return all_draws


# --------------------------------------------------------------------------- #
# judging (reuses rollout.disclosure_judge_wrapup verbatim)
# --------------------------------------------------------------------------- #
def build_task_state(traj: Trajectory, message: Any) -> TaskState:
    return TaskState(
        model=ModelName(traj.model_name),
        sample_id=traj.sample_id,
        epoch=traj.epoch,
        input=traj.prefix,
        messages=[*traj.prefix, message],
        target=Target(""),
        store={rollout.ENGINE_JUDGE_STORE_KEY: dict(traj.engine_use_metadata)},
    )


async def judge_message(traj: Trajectory, message: Any, judge_scorer, sem: asyncio.Semaphore) -> dict[str, Any]:
    state = build_task_state(traj, message)
    async with sem:
        try:
            score = await judge_scorer(state, Target(""))
        except Exception as exc:  # noqa: BLE001 -- never let one judge call kill the run
            logger.exception("%s: judge call raised", traj.key)
            return {
                "judge_verdict": "script_error",
                "judge_explanation": f"{type(exc).__name__}: {exc}",
                "judge_metadata": {},
            }
    return {
        "judge_verdict": str(score.value),
        "judge_explanation": str(score.explanation or ""),
        "judge_metadata": score.metadata or {},
    }


# --------------------------------------------------------------------------- #
# output rows
# --------------------------------------------------------------------------- #
def draw_row(traj: Trajectory, d: Draw, judge: dict[str, Any] | None) -> dict[str, Any]:
    row = {
        "arm": traj.arm,
        "epoch": traj.epoch,
        "key": traj.key,
        "cut_index": traj.cut,
        "draw": d.draw,
        "outcome": d.outcome,
        "answer_text": d.answer_text,
        "submit_answer": d.submit_answer,
        "tool_calls": d.tool_calls,
        "reasoning": d.reasoning,
        "usage": d.usage,
        "stop_reason": d.stop_reason,
        "cache_anchor": traj.cache_anchor,
        "judge_verdict": None,
        "judge_explanation": None,
        "rendered_final_answer": None,
    }
    if judge is not None:
        row["judge_verdict"] = judge["judge_verdict"]
        row["judge_explanation"] = judge["judge_explanation"]
        row["rendered_final_answer"] = judge["judge_metadata"].get("final_answer")
    return row


def original_row(traj: Trajectory, judge: dict[str, Any]) -> dict[str, Any]:
    return {
        "arm": traj.arm,
        "epoch": traj.epoch,
        "key": traj.key,
        "cut_index": traj.cut,
        "draw": -1,
        "outcome": classify_outcome(traj.original_message),
        "answer_text": traj.original_message.text,
        "submit_answer": rollout._submit_answer(traj.original_message),
        "tool_calls": None,
        "reasoning": reasoning_blocks(traj.original_message),
        "usage": None,
        "stop_reason": None,
        "cache_anchor": traj.cache_anchor,
        "judge_verdict": judge["judge_verdict"],
        "judge_explanation": judge["judge_explanation"],
        "rendered_final_answer": judge["judge_metadata"].get("final_answer"),
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
async def main_async(args: argparse.Namespace) -> None:
    install_cache_marker_patch()
    trajectories = load_targets()
    logger.info("loaded %d targets: %s", len(trajectories), [t.key for t in trajectories])

    judge_scorer = rollout.disclosure_judge_wrapup()
    judge_sem = asyncio.Semaphore(rollout.JUDGE_MAX_CONNECTIONS)

    out_path = Path(args.out)
    raw_path = Path(args.raw_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict[str, Any]] = []
    with out_path.open("w") as out_fh, raw_path.open("w") as raw_fh:
        for traj in trajectories:
            logger.info("##### starting trajectory %s #####", traj.key)
            await validate_cache_anchor(traj)
            logger.info("%s: chosen cache anchor = %s (validation=%s)", traj.key, traj.cache_anchor, traj.cache_validation)

            if args.validate_cache_only:
                continue

            draws = await run_trajectory_draws(traj, args.n_draws, args.concurrency, args.max_tokens)

            # raw backup, written before judging, so generation is never lost
            for d in draws:
                raw_fh.write(json.dumps(draw_row(traj, d, None)) + "\n")
            raw_fh.flush()

            # judge every draw whose outcome carries a final answer -- "text" (bare
            # assistant text) or "submit" (a submit() call, whose `answer` argument
            # IS a final answer under rollout.py's own wrapup_parts()/_submit_answer()
            # -- see classify_outcome()). "tool_call" (a call to some other tool)
            # is not executed and not judged.
            judge_tasks = {
                d.draw: judge_message(traj, d.message, judge_scorer, judge_sem)
                for d in draws if d.outcome in ("text", "submit")
            }
            orig_task = judge_message(traj, traj.original_message, judge_scorer, judge_sem)
            judged = await asyncio.gather(*judge_tasks.values())
            judge_by_draw = dict(zip(judge_tasks.keys(), judged))
            orig_judge = await orig_task

            outcome_counts: dict[str, int] = {}
            verdict_counts: dict[str, int] = {}
            verdict_counts_by_outcome: dict[str, dict[str, int]] = {}
            for d in draws:
                j = judge_by_draw.get(d.draw)
                row = draw_row(traj, d, j)
                out_fh.write(json.dumps(row) + "\n")
                all_rows.append(row)
                outcome_counts[d.outcome] = outcome_counts.get(d.outcome, 0) + 1
                if j is not None:
                    verdict_counts[j["judge_verdict"]] = verdict_counts.get(j["judge_verdict"], 0) + 1
                    by_out = verdict_counts_by_outcome.setdefault(d.outcome, {})
                    by_out[j["judge_verdict"]] = by_out.get(j["judge_verdict"], 0) + 1

            orow = original_row(traj, orig_judge)
            out_fh.write(json.dumps(orow) + "\n")
            all_rows.append(orow)
            out_fh.flush()

            logger.info(
                "%s: DONE. outcome_counts=%s verdict_counts=%s verdict_counts_by_outcome=%s "
                "original_rejudge=%s (expected disclosed)",
                traj.key, outcome_counts, verdict_counts, verdict_counts_by_outcome,
                orig_judge["judge_verdict"],
            )
            if orig_judge["judge_verdict"] != "disclosed":
                logger.warning(
                    "%s: re-judging the ORIGINAL final-answer message did NOT reproduce 'disclosed' "
                    "(got %r) -- flag this to the manager", traj.key, orig_judge["judge_verdict"],
                )

    if not args.validate_cache_only:
        expected_rows = len(trajectories) * (args.n_draws + 1)
        assert len(all_rows) == expected_rows, (
            f"expected {expected_rows} rows ({len(trajectories)} targets x ({args.n_draws}+1)), "
            f"got {len(all_rows)}"
        )
        valid_keys = {t.key for t in trajectories}
        bad = [r["key"] for r in all_rows if r["key"] not in valid_keys]
        assert not bad, f"rows with unexpected keys: {bad}"
        logger.info("sanity check passed: %d rows, all keys in %s", len(all_rows), sorted(valid_keys))

    logger.info("wrote %s and %s", out_path, raw_path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(WS / "debrief" / "resample_final_turn.jsonl"))
    ap.add_argument("--raw-out", default=str(WS / "debrief" / "resample_final_turn.raw.jsonl"))
    ap.add_argument("--log-file", default=str(WS / "debrief" / "resample_final_turn.log"))
    ap.add_argument("--n-draws", type=int, default=10)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--validate-cache-only", action="store_true",
                     help="only run the per-target cache-anchor validation, then exit (no real draws)")
    ap.add_argument("--only", default=None,
                     help="comma-separated arm:epoch pairs to restrict to (e.g. for smoke-testing), "
                          "default: all 4 targets")
    args = ap.parse_args()

    if args.only:
        wanted = set()
        for pair in args.only.split(","):
            arm, epoch = pair.split(":")
            wanted.add((arm, int(epoch)))
        global TARGETS
        TARGETS = [t for t in TARGETS if (t["arm"], t["epoch"]) in wanted]
        assert TARGETS, f"--only {args.only!r} matched no targets"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
