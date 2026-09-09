#!/usr/bin/env python3
"""Payload for the debrief-probe report.

Reads the 79 debrief self-reports (debrief_results.jsonl), attaches each
trajectory's IN-EPISODE disclosure verdict (disclosure_judge_wrapup) and final
game result from the source logs, computes per-model x per-arm aggregates
(self-reported reward-hacked rate, in-episode disclosure rate) with Wilson CIs,
and embeds the full corpus for the explorer.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

WS = Path("/work/workspace/beat-stockfish")
sys.path.insert(0, str(WS / ".venv/lib/python3.12/site-packages"))
from inspect_ai.log import read_eval_log  # noqa: E402

DEBRIEF = WS / "debrief" / "debrief_results.jsonl"

# arm -> (display setup, phase). Setups mirror the ablation naming.
ARM_SETUP = {
    "baseline": "original",
    "believe": "believe in yourself!",
    "nograding": "no GRADING",
    "stopeval": "stop_eval tool",
}
SETUP_ORDER = ["original", "no GRADING", "believe in yourself!", "stop_eval tool"]
MODELS = ["Fable 5.1", "GPT-6-Astra"]

RESULT_PAT = re.compile(r"Game over:\s*(1-0|0-1|1/2-1/2)")
RESULT_NAME = {"1-0": "win", "0-1": "loss", "1/2-1/2": "draw"}


def wilson(k, n):
    if n == 0:
        return {"est": None, "lo": None, "hi": None, "n": 0, "k": int(k)}
    z = 1.959963984540054
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"est": p, "lo": max(0.0, centre - half), "hi": min(1.0, centre + half),
            "n": n, "k": int(k)}


def setup_of(arm: str) -> str:
    # arm like abl-believe-astra / p2-nograding-fable51 / baseline-astra
    core = arm.replace("abl-", "").replace("p2-", "")
    for key, disp in ARM_SETUP.items():
        if core.startswith(key):
            return disp
    raise ValueError(arm)


recs = [json.loads(l) for l in open(DEBRIEF)]
assert len(recs) == 79, len(recs)

# --- attach in-episode disclosure + game result from source logs ---
by_log = defaultdict(dict)
for r in recs:
    by_log[r["source_log"]][r["epoch"]] = r

for src, ep_map in by_log.items():
    log = read_eval_log(str(WS / src), resolve_attachments=True)
    for s in log.samples or []:
        if s.epoch not in ep_map:
            continue
        r = ep_map[s.epoch]
        dj = (s.scores or {}).get("disclosure_judge_wrapup")
        r["in_episode_disclosure"] = str(dj.value) if dj else "MISSING"
        r["disclosure_evidence"] = (dj.metadata or {}).get("evidence") if dj else None
        results = []
        for m in s.messages:
            if m.role == "tool":
                results += RESULT_PAT.findall(m.text or "")
        r["game_result"] = RESULT_NAME[results[-1]] if results else None

assert all("in_episode_disclosure" in r for r in recs), "unmatched source samples"

# --- build sample rows for the explorer ---
samples = []
def readable_reasoning(raw) -> str:
    """Join human-readable reasoning blocks; drop encrypted (gAAAAA...) blobs."""
    if isinstance(raw, str):
        return "" if raw.startswith("gAAAAA") else raw
    if not isinstance(raw, list):
        return ""
    parts = []
    for blk in raw:
        txt = blk.get("reasoning") if isinstance(blk, dict) else None
        if isinstance(txt, str) and txt and not txt.startswith("gAAAAA"):
            parts.append(txt)
    return "\n\n".join(parts)


for r in sorted(recs, key=lambda r: (r["model"], r["arm"], r["epoch"])):
    p = r["parsed"]
    model = "Fable 5.1" if "fable" in r["model"] else "GPT-6-Astra"
    rh = p.get("reward_hacked")
    assert isinstance(rh, bool), (r["arm"], r["epoch"], rh)
    samples.append({
        "id": f"{r['arm']}:e{r['epoch']}",
        "arm": r["arm"],
        "model": model,
        "setup": setup_of(r["arm"]),
        "epoch": r["epoch"],
        "reward_hacked": rh,
        "in_episode_disclosure": r["in_episode_disclosure"],
        "game_result": r.get("game_result"),
        "env_feedback": p.get("env_feedback") or [],
        "freeform_note": p.get("freeform_note") or "",
        "response_text": r["response_text"],
        "reasoning": readable_reasoning(r.get("reasoning")),
        "parse_repaired": bool(r.get("parse_repair")),
        "out_tokens": (r.get("usage") or {}).get("output_tokens"),
    })

# --- aggregates: per model x setup ---
agg = {}
for m in MODELS:
    for setup in SETUP_ORDER:
        sub = [s for s in samples if s["model"] == m and s["setup"] == setup]
        n = len(sub)
        agg[f"{m}|{setup}"] = {
            "n": n,
            "reward_hacked": wilson(sum(s["reward_hacked"] for s in sub), n),
            "in_episode_disclosed": wilson(
                sum(s["in_episode_disclosure"] == "disclosed" for s in sub), n),
            "in_episode_silent": sum(s["in_episode_disclosure"] == "silent" for s in sub),
        }

# --- model-level totals ---
model_tot = {}
for m in MODELS:
    sub = [s for s in samples if s["model"] == m]
    n = len(sub)
    model_tot[m] = {
        "n": n,
        "reward_hacked": wilson(sum(s["reward_hacked"] for s in sub), n),
        "disclosed": sum(s["in_episode_disclosure"] == "disclosed" for s in sub),
        "silent": sum(s["in_episode_disclosure"] == "silent" for s in sub),
        "in_episode_disclosed_rate": wilson(
            sum(s["in_episode_disclosure"] == "disclosed" for s in sub), n),
    }

# --- the 2x2 concealment mismatch (in-episode disclosure x debrief self-report) ---
mismatch = {m: {"silent_confessed": 0, "silent_denied": 0,
                "disclosed_confessed": 0, "disclosed_denied": 0} for m in MODELS}
for s in samples:
    m = s["model"]
    sil = s["in_episode_disclosure"] != "disclosed"
    conf = s["reward_hacked"] is True
    key = ("silent" if sil else "disclosed") + ("_confessed" if conf else "_denied")
    mismatch[m][key] += 1

# --- xhigh robustness comparison (default effort vs reasoning_effort=xhigh) ---
import statistics  # noqa: E402

XHIGH = WS / "debrief" / "debrief_results_xhigh.jsonl"
xrecs = [json.loads(l) for l in open(XHIGH)]
assert len(xrecs) == 79, len(xrecs)


def model_of(r):
    return "Fable 5.1" if "fable" in r["model"] else "GPT-6-Astra"


def rh_of(r):
    v = r["parsed"]["reward_hacked"]
    assert isinstance(v, bool), (r["key"], v)
    return v


def tok_stats(rs, field):
    vals = [(r.get("usage") or {}).get(field) or 0 for r in rs]
    return {"median": statistics.median(vals), "max": max(vals), "min": min(vals)}


def_by_key = {r["key"]: r for r in recs}
x_by_key = {r["key"]: r for r in xrecs}
assert set(def_by_key) == set(x_by_key), "key set differs between default and xhigh runs"
# every xhigh record used the intended config
for r in xrecs:
    assert r["generate_config_used"] == {"max_tokens": 64000, "reasoning_effort": "xhigh"}, r["key"]

flip_keys = [k for k in def_by_key if rh_of(def_by_key[k]) != rh_of(x_by_key[k])]
xhigh_block = {
    "n": len(xrecs),
    "n_flips": len(flip_keys),
    "flip_keys": flip_keys,
    "reward_hacked": {},           # xhigh, per model
    "reward_hacked_default": {},   # default effort, per model
    "tokens": {},
}
for m in MODELS:
    xsub = [r for r in xrecs if model_of(r) == m]
    dsub = [r for r in recs if model_of(r) == m]
    xhigh_block["reward_hacked"][m] = wilson(sum(rh_of(r) for r in xsub), len(xsub))
    xhigh_block["reward_hacked_default"][m] = wilson(sum(rh_of(r) for r in dsub), len(dsub))
    xhigh_block["tokens"][m] = {
        "xhigh": {"reasoning": tok_stats(xsub, "reasoning_tokens"),
                  "output": tok_stats(xsub, "output_tokens")},
        "default": {"reasoning": tok_stats(dsub, "reasoning_tokens"),
                    "output": tok_stats(dsub, "output_tokens")},
    }
# how many samples' reasoning-token usage actually rose under xhigh (effort-took-effect)
xhigh_block["n_reasoning_rose"] = sum(
    ((x_by_key[k].get("usage") or {}).get("reasoning_tokens") or 0)
    > ((def_by_key[k].get("usage") or {}).get("reasoning_tokens") or 0)
    for k in def_by_key)

payload = {
    "generated_at": os.popen("date '+%Y-%m-%d %H:%M %Z'").read().strip(),
    "xhigh": xhigh_block,
    "models": MODELS,
    "setups": SETUP_ORDER,
    "n_total": len(samples),
    "agg": agg,
    "model_tot": model_tot,
    "mismatch": mismatch,
    "samples": samples,
    "debrief_message": recs[0]["debrief_message"],
    "qual": None,  # filled by a later pass once the qualitative agent returns
}

out = Path(__file__).parent / "data"
out.mkdir(exist_ok=True)
js = json.dumps(payload)
(out / "payload.json").write_text(js)
print(f"{len(samples)} samples; payload {len(js)/1e6:.2f} MB")
for m in MODELS:
    t = model_tot[m]
    print(f"  {m:12s} n={t['n']:2d} reward_hacked={t['reward_hacked']['k']}/{t['n']} "
          f"in-episode disclosed={t['disclosed']}/{t['n']} silent={t['silent']}")
print("  mismatch:", json.dumps(mismatch))
print(f"  xhigh: n={xhigh_block['n']} flips={xhigh_block['n_flips']} "
      f"reasoning_rose={xhigh_block['n_reasoning_rose']}/79")
for m in MODELS:
    t = xhigh_block["tokens"][m]
    print(f"    {m:12s} rh_default={xhigh_block['reward_hacked_default'][m]['k']}/"
          f"{xhigh_block['reward_hacked_default'][m]['n']} "
          f"rh_xhigh={xhigh_block['reward_hacked'][m]['k']}/{xhigh_block['reward_hacked'][m]['n']} "
          f"| reasoning med default={t['default']['reasoning']['median']} "
          f"xhigh={t['xhigh']['reasoning']['median']}")
