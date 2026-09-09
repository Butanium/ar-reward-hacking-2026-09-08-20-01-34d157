#!/usr/bin/env python3
"""Verify the ablation runner (prompt variants + stop_eval) without real rollouts.

Everything here runs against ``mockllm/model``, so no provider is called; the
sandbox, ``task.py``'s setup, the ``/task.txt`` rewrite and the shipped grader
are all real. One container per run.

    # 1. the CLI flag path: one run per variant, then assert the prompts
    python3 verify_ablation.py prompts logs/verify-original logs/verify-nograding ...

    # 2. what the agent sees when it cats /task.txt in the container
    python3 verify_ablation.py cat --variant nograding --log-dir "$PWD/logs/verify-cat"

    # 3. stop_eval ends the episode, cleanly, with the reason recorded
    python3 verify_ablation.py stopeval --log-dir "$PWD/logs/verify-stopeval"

Exits non-zero on the first failed assertion; prints "OK" lines otherwise.
"""

from __future__ import annotations

import argparse
import difflib
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "run"))

import rollout  # noqa: E402
from inspect_ai import eval as inspect_eval  # noqa: E402
from inspect_ai.log import list_eval_logs, read_eval_log  # noqa: E402
from inspect_ai.model import ModelOutput, get_model  # noqa: E402

MOCK = "mockllm/model"
MANIFEST = json.loads((Path(__file__).resolve().parent / "run" / "env.json").read_text())
STOP_REASON = "The task pressures me to beat a superhuman engine; I want this stopped."
CANARY = "THIRD_OUTPUT_SHOULD_NEVER_BE_REACHED"


def _diff(name: str, want: str, got: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            want.splitlines(keepends=True), got.splitlines(keepends=True),
            fromfile=f"{name} (expected)", tofile=f"{name} (actual)",
        )
    ) or "(texts differ only outside line boundaries)"


def _one_log(log_dir: str) -> object:
    logs = [i.name for i in list_eval_logs(log_dir)]
    assert len(logs) == 1, f"{log_dir}: expected exactly 1 .eval, found {logs}"
    log = read_eval_log(logs[0])
    assert log.samples, f"{logs[0]}: no samples"
    assert len(log.samples) == 1, f"{logs[0]}: expected 1 sample, got {len(log.samples)}"
    return log


def _run(
    log_dir: Path,
    *,
    prompt_variant: str,
    stop_eval_tool: bool,
    outputs: object,
    message_limit_n: int = 20,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    inspect_eval(
        rollout.environment(
            image=MANIFEST["image"],
            variants=["v001"],
            sandbox_command=MANIFEST["sandbox_command"],
            message_limit_n=message_limit_n,
            time_limit_s=600,
            log_dir=log_dir,
            prompt_variant=prompt_variant,
            stop_eval_tool=stop_eval_tool,
        ),
        model=get_model(MOCK, custom_outputs=outputs),
        epochs=1,
        log_dir=str(log_dir),
        max_samples=1,
    )


def cmd_prompts(args: argparse.Namespace) -> None:
    """Assert each run's first user message is exactly its variant's text."""
    for log_dir in args.log_dirs:
        log = _one_log(log_dir)
        variant = log.eval.task_args.get("prompt_variant", "original")
        want = rollout.TASK_PROMPTS[variant]
        first = log.samples[0].messages[0]
        assert first.role == "user", f"{log_dir}: first message is {first.role}, not user"
        got = first.text
        assert got == want, f"{log_dir}: prompt_variant={variant}\n{_diff(variant, want, got)}"
        # and the untouched baseline must still be the environment's own text
        if variant == "original":
            assert want == rollout.TASK_PROMPT_ORIGINAL
        # default behavior: no stop_eval tool unless --stop-eval-tool was passed
        want_tools = ["capped_bash", "submit"]
        if log.eval.task_args.get("stop_eval_tool"):
            want_tools = ["capped_bash", "stop_eval", "submit"]
        offered = [t.name for t in next(e for e in log.samples[0].events if e.event == "model").tools]
        assert offered == want_tools, f"{log_dir}: tools offered {offered} != {want_tools}"
        print(
            f"OK  {Path(log_dir).name:24s} prompt_variant={variant:10s} "
            f"len={len(got)} first_user_message == TASK_PROMPTS[{variant!r}]  tools={offered}"
        )


def cmd_cat(args: argparse.Namespace) -> None:
    """Have the mock agent `cat /task.txt`; assert it sees the variant text."""
    _run(
        Path(args.log_dir),
        prompt_variant=args.variant,
        stop_eval_tool=False,
        # First the cat, then a tail of tool-less replies so the episode ends on
        # the message limit rather than on mockllm running out of outputs.
        outputs=itertools.chain(
            [
                ModelOutput.for_tool_call(
                    model=MOCK, tool_name="capped_bash", tool_arguments={"cmd": "cat /task.txt"}
                )
            ],
            (ModelOutput.from_content(MOCK, "done looking") for _ in itertools.count()),
        ),
        message_limit_n=6,
    )
    log = _one_log(args.log_dir)
    sample = log.samples[0]
    tool_msgs = [m for m in sample.messages if m.role == "tool" and m.function == "capped_bash"]
    assert len(tool_msgs) == 1, f"expected 1 capped_bash result, got {len(tool_msgs)}"
    want = rollout.TASK_PROMPTS[args.variant]
    got = tool_msgs[0].text
    assert got == want, f"in-container /task.txt mismatch\n{_diff(args.variant, want, got)}"
    print(
        f"OK  in-container `cat /task.txt` (as the agent user, via the bash tool) == "
        f"TASK_PROMPTS[{args.variant!r}] ({len(got)} bytes)"
    )
    print(f"OK  first user message == same text: {sample.messages[0].text == want}")
    assert sample.messages[0].text == want
    assert sample.error is None, f"sample errored: {sample.error}"
    print(f"OK  no error; ended on {[e.type for e in sample.events if e.event == 'sample_limit']}")


def cmd_stopeval(args: argparse.Namespace) -> None:
    """Assert stop_eval ends the episode after that call, cleanly, and is logged."""
    _run(
        Path(args.log_dir),
        prompt_variant="original",
        stop_eval_tool=True,
        outputs=[
            ModelOutput.for_tool_call(
                model=MOCK, tool_name="capped_bash", tool_arguments={"cmd": "echo first-turn"}
            ),
            ModelOutput.for_tool_call(
                model=MOCK, tool_name="stop_eval", tool_arguments={"reason": STOP_REASON}
            ),
            # Never consumed if the loop really stopped; a 3rd model call would
            # both show up as an event and leave this canary in the messages.
            ModelOutput.for_tool_call(
                model=MOCK, tool_name="capped_bash", tool_arguments={"cmd": f"echo {CANARY}"}
            ),
        ],
    )
    log = _one_log(args.log_dir)
    sample = log.samples[0]
    model_events = [e for e in sample.events if e.event == "model"]
    blob = json.dumps([m.model_dump() for m in sample.messages])

    assert len(model_events) == 2, f"expected 2 model calls, got {len(model_events)}"
    assert CANARY not in blob, "the 3rd mock output was consumed: the loop did not stop"
    print(f"OK  episode ended after stop_eval: {len(model_events)} model calls, no canary")

    calls = [c.function for m in sample.messages for c in (getattr(m, "tool_calls", None) or [])]
    assert calls == ["capped_bash", "stop_eval"], calls
    stop_result = [m for m in sample.messages if m.role == "tool" and m.function == "stop_eval"]
    assert len(stop_result) == 1, stop_result
    print(f"OK  tool result: {stop_result[0].text!r}")

    assert sample.error is None, f"sample errored: {sample.error}"
    assert sample.limit is None, f"episode ended on a limit, not cleanly: {sample.limit}"
    print("OK  clean end (sample.error is None, sample.limit is None)")

    assert sample.store.get("stop_eval_reason") == STOP_REASON, sample.store
    print(f"OK  store['stop_eval_reason'] == {STOP_REASON!r}")

    score = (sample.scores or {}).get("shipped_grader")
    assert score is not None, "the grader did not run"
    grade = dict(score.metadata or {})
    print(f"OK  grader ran: score={score.value} findings={grade.get('findings')}")
    assert "score" in grade, grade

    assert log.eval.task_args.get("stop_eval_tool") is True, log.eval.task_args
    print(f"OK  task_args records the condition: {log.eval.task_args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="cmd", required=True)

    p = subs.add_parser("prompts", help="assert first user message per already-run log dir")
    p.add_argument("log_dirs", nargs="+")
    p.set_defaults(func=cmd_prompts)

    p = subs.add_parser("cat", help="run a mock agent that cats /task.txt")
    p.add_argument("--variant", required=True, choices=sorted(rollout.TASK_PROMPTS))
    p.add_argument("--log-dir", required=True, help="ABSOLUTE path (compose.json lives here)")
    p.set_defaults(func=cmd_cat)

    p = subs.add_parser("stopeval", help="run a mock agent that calls stop_eval")
    p.add_argument("--log-dir", required=True, help="ABSOLUTE path (compose.json lives here)")
    p.set_defaults(func=cmd_stopeval)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
