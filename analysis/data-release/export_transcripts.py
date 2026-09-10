#!/usr/bin/env python3
"""Flat JSONL export of every rollout: metadata + all judge scores + full transcript.

One JSON object per line (one rollout = one line), so people can resample, re-run the
judge, or inspect transcripts without Inspect or the HTML explorer. Rows come from the
SAME module the ablations report uses (analysis/ablations-report/rollout_rows.py), so the
export and the report can never drift.

Usage: python export_transcripts.py [OUT.jsonl]
Default OUT = /work/data-release/transcripts.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/work/analysis/ablations-report")
from rollout_rows import iter_rows  # noqa: E402

out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/work/data-release/transcripts.jsonl")
out_path.parent.mkdir(parents=True, exist_ok=True)

n_lines = 0
by_outcome: dict[str, int] = {}
by_result: dict[str, int] = {}
with out_path.open("w") as fh:
    for r in iter_rows():
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        n_lines += 1
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
        by_result[str(r["game_result"])] = by_result.get(str(r["game_result"]), 0) + 1

sz = out_path.stat().st_size
print(f"{n_lines} rollouts -> {out_path}  ({sz/1e6:.1f} MB)")
for k, v in sorted(by_outcome.items(), key=lambda kv: -kv[1]):
    print(f"  {v:3d}  {k}")
print("game_result:", by_result)
