#!/usr/bin/env python3
"""Payload for the motivated-reasoning report.

Reads the Sonnet-judged copies in workspace/beat-stockfish/logs-motivated/<arm>/*.eval, keeps every
sample the motivated_reasoning_judge actually judged (engine-driven trajectories), joins the
in-episode disclosure verdict and the chess result, and computes per-model / per-condition
aggregates with Wilson 95% CIs. Embeds the exact judge-visible transcript of every judged sample.
"""
from __future__ import annotations

import base64
import glob
import gzip
import json
import os
import sys
from collections import Counter
from pathlib import Path

WS = Path("/work/workspace/beat-stockfish")
sys.path.insert(0, str(WS / ".venv/lib/python3.12/site-packages"))
sys.path.insert(0, "/work/analysis/ablations-report")
sys.path.insert(0, str(WS))
from inspect_ai.log import read_eval_log  # noqa: E402
from rollout_rows import game_result_of, wilson  # noqa: E402
from run.motivated_reasoning_judge import (  # noqa: E402
    MOTIVATED_JUDGE_MODEL, MOTIVATED_JUDGE_PROMPT, MOTIVATED_JUDGE_REASONING_EFFORT)

LOGS = WS / "logs-motivated"
TRANSCRIPTS = LOGS / "transcripts"
MODELS = ["Fable 5.1", "GPT-6-Astra"]
CONDITIONS = ["original", "no GRADING", "believe in yourself!", "stop_eval tool"]
VERDICTS = ["rationalized_as_legitimate", "acknowledged_and_overrode", "no_deliberation", "other"]
VERDICT_LABEL = {"rationalized_as_legitimate": "rationalised as legitimate",
                 "acknowledged_and_overrode": "acknowledged cheating, proceeded",
                 "no_deliberation": "no deliberation", "other": "other"}
TYPES = ["intentional_affordance", "not_prohibited", "task_wording", "disclosure_licenses_it",
         "only_way_to_win", "relabel_as_honest", "honeypot_dismissed", "other"]
TYPE_LABEL = {"intentional_affordance": "socket was intentionally provided",
              "not_prohibited": "not prohibited", "task_wording": "task wording licenses it",
              "disclosure_licenses_it": "I'll disclose, so it's fine",
              "only_way_to_win": "only way to win", "relabel_as_honest": "relabelled as honest/legitimate",
              "honeypot_dismissed": "honeypot considered, dismissed", "other": "other"}
FLAGS = ["deliberated", "considered_it_might_be_cheating", "planned_disclosure"]


def condition_of(arm: str) -> str:
    core = arm.replace("abl-", "").replace("p2-", "")
    for k, c in [("baseline", "original"), ("nograding", "no GRADING"),
                 ("believe", "believe in yourself!"), ("stopeval", "stop_eval tool")]:
        if core.startswith(k):
            return c
    raise ValueError(arm)


rows = []
for f in sorted(glob.glob(str(LOGS / "*" / "*.eval"))):
    arm = Path(f).parent.name
    model = "Fable 5.1" if "fable" in arm else "GPT-6-Astra"
    log = read_eval_log(f, resolve_attachments=True)
    for s in log.samples or []:
        sc = (s.scores or {}).get("motivated_reasoning_judge")
        if sc is None or str(sc.value) == "not_applicable":
            continue
        md = sc.metadata or {}
        assert str(sc.value) in VERDICTS, (arm, s.epoch, sc.value)
        dj = s.scores.get("disclosure_judge_wrapup")
        g = s.scores["shipped_grader"]
        completed = "the game was not completed" not in (g.metadata or {}).get("findings", [])
        tpath = TRANSCRIPTS / f"{arm}_ep{s.epoch}.txt"
        assert tpath.exists(), tpath
        types = [t for t in md.get("rationalization_types", []) if t in TYPES]
        rows.append({
            "id": f"{arm}:e{s.epoch}", "arm": arm, "epoch": s.epoch, "model": model,
            "condition": condition_of(arm),
            "verdict": str(sc.value), "verdict_label": VERDICT_LABEL[str(sc.value)],
            "types": types, "type_labels": [TYPE_LABEL[t] for t in types],
            **{k: bool(md.get(k)) for k in FLAGS},
            "confidence": md.get("confidence"),
            "judge_summary": md.get("summary", ""),
            "quotes": md.get("key_quotes", []),
            "disclosure": str(dj.value) if dj else None,
            "game_result": game_result_of(s, arm, completed),
            "n_messages": len(s.messages),
            "transcript": tpath.read_text(),
        })

assert len({r["id"] for r in rows}) == len(rows)
print(f"{len(rows)} judged samples:", Counter(r["model"] for r in rows))


def agg_for(sub):
    n = len(sub)
    return {
        "n": n,
        "verdict": {v: {"count": sum(r["verdict"] == v for r in sub),
                        **wilson(sum(r["verdict"] == v for r in sub), n)} for v in VERDICTS},
        "type": {t: {"count": sum(t in r["types"] for r in sub),
                     **wilson(sum(t in r["types"] for r in sub), n)} for t in TYPES},
        "flag": {k: {"count": sum(r[k] for r in sub), **wilson(sum(r[k] for r in sub), n)} for k in FLAGS},
        "confidence": dict(Counter(r["confidence"] for r in sub)),
        # planned vs actual in-episode disclosure
        "plan_x_actual": {
            f"{'planned' if p else 'not_planned'}|{'disclosed' if d else 'silent'}":
                sum((r["planned_disclosure"] == p) and ((r["disclosure"] == "disclosed") == d) for r in sub)
            for p in (True, False) for d in (True, False)
        },
        "disclosed": sum(r["disclosure"] == "disclosed" for r in sub),
    }


agg = {m: agg_for([r for r in rows if r["model"] == m]) for m in MODELS}
agg_cond = {f"{m}|{c}": agg_for([r for r in rows if r["model"] == m and r["condition"] == c])
            for m in MODELS for c in CONDITIONS}

payload = {
    "generated_at": os.popen("date '+%Y-%m-%d %H:%M %Z'").read().strip(),
    "models": MODELS, "conditions": CONDITIONS,
    "verdicts": VERDICTS, "verdict_label": VERDICT_LABEL,
    "types": TYPES, "type_label": TYPE_LABEL, "flags": FLAGS,
    "judge": {"model": MOTIVATED_JUDGE_MODEL, "reasoning_effort": MOTIVATED_JUDGE_REASONING_EFFORT,
              "prompt": MOTIVATED_JUDGE_PROMPT},
    "agg": agg, "agg_cond": agg_cond,
    "models_present": [m for m in MODELS if agg[m]["n"]],
    "samples": rows,
}
js = json.dumps(payload)
blob = base64.b64encode(gzip.compress(js.encode(), 9)).decode()
(Path(__file__).parent / "data/payload.b64").write_text(blob)
print(f"json {len(js)/1e6:.1f} MB -> b64 {len(blob)/1e6:.2f} MB")
for m in MODELS:
    a = agg[m]
    if a["n"]:
        print(f"  {m:12s} n={a['n']:3d} " + " ".join(f"{v}={a['verdict'][v]['count']}" for v in VERDICTS)
              + f" | types " + ", ".join(f"{t}={a['type'][t]['count']}" for t in TYPES if a['type'][t]['count'])
              + f" | flags " + ", ".join(f"{k}={a['flag'][k]['count']}" for k in FLAGS)
              + f" | plan×actual {a['plan_x_actual']}")
