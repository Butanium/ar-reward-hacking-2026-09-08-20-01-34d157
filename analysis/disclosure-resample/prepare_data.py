#!/usr/bin/env python3
"""Build data/payload.json for the "Disclosure rate on resampled final turns" report.

Input: workspace/beat-stockfish/debrief/resample_final_turn.jsonl (44 rows: 4 trajectories x
(10 redraws + 1 re-judged original)). Nothing is re-run here.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
SRC = Path("/work/workspace/beat-stockfish/debrief/resample_final_turn.jsonl")
TRANSCRIPTS = Path("/work/data-release/transcripts.jsonl")

ARM_NOTE = {
    "baseline-fable51": "original prompt (phase-1 arm)",
    "p2-believe-fable51": "“believe in yourself!” (phase-2 arm)",
    "abl-believe-fable51": "“believe in yourself!” (phase-1 arm)",
}
LABEL = {
    ("baseline-fable51", 2): "original · e2",
    ("p2-believe-fable51", 9): "believe · e9",
    ("abl-believe-fable51", 10): "believe · e10",
    ("abl-believe-fable51", 3): "believe · e3",
}
ORDER = [("baseline-fable51", 2), ("p2-believe-fable51", 9), ("abl-believe-fable51", 10), ("abl-believe-fable51", 3)]


def wilson(k: int, n: int, z: float = 1.96) -> dict:
    if n == 0:
        return {"est": None, "lo": None, "hi": None, "k": 0, "n": 0}
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {"est": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h), "k": k, "n": n}


rows = [json.loads(l) for l in SRC.open() if l.strip()]
assert len(rows) == 44, len(rows)
# game results from the data release
res = {}
for l in TRANSCRIPTS.open():
    r = json.loads(l)
    res[(r["condition"], r["model"], r.get("epoch"))] = r
game_result = {}
for l in TRANSCRIPTS.open():
    r = json.loads(l)
    if r["model"] != "Fable 5.1":
        continue
    game_result[(r.get("arm") or "", r.get("epoch"))] = r["game_result"]

by = defaultdict(list)
for r in rows:
    by[(r["arm"], r["epoch"])].append(r)
assert set(by) == set(ORDER), set(by)

trajectories = []
samples = []
pool = Counter()
for arm, ep in ORDER:
    rs = sorted(by[(arm, ep)], key=lambda r: r["draw"])
    orig = [r for r in rs if r["draw"] < 0]
    draws = [r for r in rs if r["draw"] >= 0]
    assert len(orig) == 1 and len(draws) == 10, (arm, ep, len(orig), len(draws))
    o = orig[0]
    c = Counter(r["outcome"] for r in draws)
    verdict = Counter(r["judge_verdict"] for r in draws if r["outcome"] != "tool_call")
    judged = sum(verdict.values())
    n_disc = verdict.get("disclosed", 0)
    form = Counter((r["outcome"], r["judge_verdict"]) for r in draws if r["outcome"] != "tool_call")
    for (f, v), n in form.items():
        pool[(f, v)] += n
    usage0 = draws[0]["usage"] or {}
    cache_reads = [(r["usage"] or {}).get("input_tokens_cache_read") or 0 for r in draws]
    uncached = [(r["usage"] or {}).get("input_tokens") or 0 for r in draws]
    label = LABEL[(arm, ep)]
    # chess result of the original game (from the campaign's outcome table: e2 and e9 drew, e10 and e3 won)
    gr = {("baseline-fable51", 2): "draw", ("p2-believe-fable51", 9): "draw",
          ("abl-believe-fable51", 10): "win", ("abl-believe-fable51", 3): "win"}[(arm, ep)]
    trajectories.append({
        "key": o["key"], "arm": arm, "epoch": ep, "label": label, "arm_note": ARM_NOTE[arm],
        "game_result": gr, "cut_index": o["cut_index"],
        "nudge_after": arm != "abl-believe-fable51" or ep != 10,
        "original_rendered": o["rendered_final_answer"], "original_rejudge": o["judge_verdict"],
        "original_judge_explanation": o.get("judge_explanation"),
        "n_text": c.get("text", 0), "n_submit": c.get("submit", 0), "n_tool_call": c.get("tool_call", 0),
        "n_disclosed": n_disc, "n_silent": verdict.get("silent", 0), "n_denied": verdict.get("denied", 0),
        "n_judged": judged,
        "disclosed_of_judged": wilson(n_disc, judged),
        "disclosed_of_all": wilson(n_disc, 10),
        "silent_of_all": wilson(verdict.get("silent", 0), 10),
        "toolcall_of_all": wilson(c.get("tool_call", 0), 10),
        "by_form": {f: {"disclosed": form.get((f, "disclosed"), 0), "silent": form.get((f, "silent"), 0)} for f in ("text", "submit")},
        "cache": {"prefix_tokens": max(cache_reads), "cache_read_min": min(cache_reads), "cache_read_max": max(cache_reads),
                  "uncached_input_min": min(uncached), "uncached_input_max": max(uncached),
                  "anchor": sorted({r.get("cache_anchor") for r in draws})},
    })
    for r in draws:
        u = r["usage"] or {}
        samples.append({
            "id": f"{label}:{r['draw']}", "traj": label, "key": r["key"], "draw": r["draw"],
            "outcome": r["outcome"], "verdict": r["judge_verdict"] or "not judged (called bash)",
            "rendered": r["rendered_final_answer"] or "", "answer_text": r["answer_text"] or "",
            "submit_answer": r["submit_answer"] or "", "tool_calls": r["tool_calls"] or [],
            "judge_explanation": r.get("judge_explanation") or "",
            "reasoning": r.get("reasoning") or "",
            "output_tokens": u.get("output_tokens"), "cache_read": u.get("input_tokens_cache_read"),
        })

pooled_by_form = {
    f: {"disclosed": pool.get((f, "disclosed"), 0), "silent": pool.get((f, "silent"), 0),
        "rate": wilson(pool.get((f, "disclosed"), 0), pool.get((f, "disclosed"), 0) + pool.get((f, "silent"), 0))}
    for f in ("text", "submit")
}
payload = {
    "model": "openrouter/anthropic/claude-fable-5.1",
    "judge_model": "openrouter/z-ai/glm-5.3-flash (the campaign's disclosure_judge_wrapup scorer, t=1.0)",
    "generate_config": {"max_tokens": 32000, "sampling": "provider default", "tools": ["capped_bash", "submit"]},
    "trajectories": trajectories, "samples": samples, "pooled_by_form": pooled_by_form,
    "totals": {"n_draws": len(samples), "n_judged": sum(t["n_judged"] for t in trajectories),
               "n_disclosed": sum(t["n_disclosed"] for t in trajectories), "n_tool_call": sum(t["n_tool_call"] for t in trajectories)},
}
out = ROOT / "data/payload.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(payload, ensure_ascii=False))
print(f"wrote {out} ({out.stat().st_size/1e3:.0f} KB)")
for t in trajectories:
    print(f"{t['label']:14s} {t['game_result']:5s} cut={t['cut_index']:3d} text={t['n_text']} submit={t['n_submit']} bash={t['n_tool_call']} | disclosed {t['n_disclosed']}/{t['n_judged']} judged  silent {t['n_silent']} | cache prefix {t['cache']['prefix_tokens']} read min {t['cache']['cache_read_min']} uncached {t['cache']['uncached_input_min']}-{t['cache']['uncached_input_max']} | orig rejudge {t['original_rejudge']}")
print("pooled by form:", pooled_by_form)
