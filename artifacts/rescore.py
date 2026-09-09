#!/usr/bin/env python3
"""Attach the two LLM-judge scorers to existing .eval logs, without resampling.

    python3 rescore.py                      # all 10 phase-1 runs -> logs-rescored/
    python3 rescore.py --runs baseline-fable51 --max-samples 2 --out-dir /tmp/smoke

This is ``inspect score`` done through the Python API (``inspect_ai.score``),
which is the only way to attach BOTH new scorers in ONE pass: the CLI's
``--scorer`` takes a single scorer, and disclosure_judge wants engine_use_judge's
verdict from the same pass's Store.

``action="append"`` keeps every score already in the log -- shipped_grader above
all -- and adds the two new ones beside it. The originals are never touched: the
rescored log is written to a NEW file under ``--out-dir``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "run"))

from inspect_ai import score  # noqa: E402
from inspect_ai.log import read_eval_log, write_eval_log  # noqa: E402

import evallib as E  # noqa: E402
from rollout import (  # noqa: E402
    JUDGE_MAX_CONNECTIONS,
    JUDGE_MAX_TOKENS,
    JUDGE_MODEL,
    disclosure_judge,
    engine_use_judge,
)


def rescore_one(
    run: str,
    logs_dir: str,
    out_dir: Path,
    model: str,
    max_tokens: int,
    max_connections: int,
    action: str,
    max_samples: int | None,
) -> Path:
    src = Path(E.log_path(run, logs_dir))
    log = read_eval_log(str(src))
    assert log.samples, f"{src}: no samples"
    if max_samples is not None:
        log.samples = log.samples[:max_samples]
        print(f"  [smoke] truncated to {len(log.samples)} sample(s)")
    n_before = len(log.samples)
    scored = score(
        log,
        [
            engine_use_judge(model, max_tokens, max_connections),
            disclosure_judge(model, max_tokens, max_connections),
        ],
        action=action,  # type: ignore[arg-type]
        copy=False,
    )
    assert scored.samples is not None and len(scored.samples) == n_before, (
        f"{run}: sample count changed during rescoring "
        f"({n_before} -> {len(scored.samples or [])})"
    )
    dest_dir = out_dir / run
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    write_eval_log(scored, str(dest))
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logs-dir", default="logs")
    ap.add_argument("--runs", nargs="*", default=E.RUNS)
    ap.add_argument("--out-dir", type=Path, default=Path("logs-rescored"))
    ap.add_argument("--model", default=JUDGE_MODEL)
    ap.add_argument("--max-tokens", type=int, default=JUDGE_MAX_TOKENS)
    ap.add_argument("--max-connections", type=int, default=JUDGE_MAX_CONNECTIONS)
    ap.add_argument("--action", choices=["append", "overwrite"], default="append")
    ap.add_argument(
        "--max-samples",
        type=int,
        help="SMOKE ONLY: score just the first N samples (the written log is then partial)",
    )
    args = ap.parse_args()

    if args.action == "overwrite":
        print(
            "WARNING --action overwrite REPLACES the log's score set: the shipped_grader "
            "score would be dropped, since it is not one of the scorers being run.",
            file=sys.stderr,
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for i, run in enumerate(args.runs, 1):
        t0 = time.time()
        print(f"[{i}/{len(args.runs)}] {run} ...", flush=True)
        dest = rescore_one(
            run,
            args.logs_dir,
            args.out_dir,
            args.model,
            args.max_tokens,
            args.max_connections,
            args.action,
            args.max_samples,
        )
        print(f"  -> {dest}  ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
