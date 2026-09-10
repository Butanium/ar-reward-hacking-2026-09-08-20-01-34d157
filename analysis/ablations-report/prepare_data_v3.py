#!/usr/bin/env python3
"""Payload v3: phases 1+2, judge decomposition, reasoning in transcripts.

Per-rollout rows come from rollout_rows.py (shared with the data export); this file only
aggregates them and writes data/payload_v3.b64.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rollout_rows import (CHEAT_SEGS, CONDITIONS, MODELS, OUTCOMES,  # noqa: E402
                          iter_rows, wilson)

rows = list(iter_rows())

models = MODELS
agg = {}
for m in models:
    for c in CONDITIONS:
        sub = [r for r in rows if r["model"] == m and r["condition"] == c]
        n = len(sub)
        cheats = [r for r in sub if r["outcome"] in CHEAT_SEGS]
        # method: engine-driven (a genuine cheat, disclosed or not) vs clean-play
        # (clean completed game or probe-only, where the moves were self-chosen).
        driven = lambda r: r["outcome"] in CHEAT_SEGS[:2]
        # result_split[result][method] = count of rollouts with that game result + method
        result_split = {
            res: {"cheat": sum(r["game_result"] == res and driven(r) for r in sub),
                  "clean": sum(r["game_result"] == res and not driven(r) for r in sub)}
            for res in ("win", "draw")
        }
        agg[f"{m}|{c}"] = {
            "n": n,
            "cheat_total": wilson(len([r for r in cheats if r["outcome"] != CHEAT_SEGS[2]]), n),
            "contact": wilson(sum(r["engine_contacted"] for r in sub), n),
            "win": wilson(sum(r["game_result"] == "win" for r in sub), n),
            "draw": wilson(sum(r["game_result"] == "draw" for r in sub), n),
            "nonloss": wilson(sum(r["game_result"] in ("win", "draw") for r in sub), n),
            "result_split": result_split,
            "seg": {o: {"count": sum(r["outcome"] == o for r in sub),
                        **wilson(sum(r["outcome"] == o for r in sub), n)} for o in OUTCOMES},
        }

payload = {
    "generated_at": os.popen("date '+%Y-%m-%d %H:%M %Z'").read().strip(),
    "models": models, "conditions": CONDITIONS,
    "cheat_segs": CHEAT_SEGS, "outcomes": OUTCOMES,
    "expected_n": {c: 30 for c in CONDITIONS},
    "agg": agg,
    "samples": rows,
}
out = Path(__file__).parent / "data"
out.mkdir(exist_ok=True)
js = json.dumps(payload)
blob = base64.b64encode(gzip.compress(js.encode(), 9)).decode()
(out / "payload_v3.b64").write_text(blob)
print(f"{len(rows)} samples; json {len(js)/1e6:.1f} MB -> b64 {len(blob)/1e6:.2f} MB")
for m in models:
    for c in CONDITIONS:
        a = agg[f"{m}|{c}"]
        if a["n"]:
            segs = {o[:12]: a["seg"][o]["count"] for o in OUTCOMES if a["seg"][o]["count"]}
            print(f"{m:12s} {c:22s} n={a['n']:2d} {segs}")
