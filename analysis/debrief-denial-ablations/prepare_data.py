#!/usr/bin/env python3
"""Build data/payload.json for the "Debrief denial — resampling and reasoning ablations" report.

Inputs (all cached inference results, nothing is re-run here):
  workspace/beat-stockfish/debrief/resample_denials.jsonl          3 traj x 10 draws (e10/e13/e14)
  workspace/beat-stockfish/debrief/resample_result_check.jsonl     6 traj x 10 draws
  workspace/beat-stockfish/debrief/intervention_{c1..c4}.jsonl     single-passage edits, 10 draws each
  workspace/beat-stockfish/debrief/intervention_{s13,s10}.jsonl    all reasoning stripped
  workspace/beat-stockfish/debrief/intervention_{t10,t20,g1,g2,g3,k1}.jsonl  per-turn / per-phase strips (e13)
  workspace/beat-stockfish/debrief/debrief_results.jsonl           the original single draw per trajectory
  workspace/beat-stockfish/debrief/debrief_message.txt             the debrief user turn, verbatim
  workspace/beat-stockfish/debrief/patches/c*.json                 the exact edit specs
  analysis/motivated-report/data/payload.b64                       game result / disclosure / reasoning-judge verdict
  data/traj_e13.txt, data/traj_e10.txt                             transcript dumps (reasoning block sizes)
"""
from __future__ import annotations

import base64
import gzip
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
WORK = Path("/work")
DEB = WORK / "workspace/beat-stockfish/debrief"

MODEL = "openrouter/anthropic/claude-fable-5.1"


def wilson(k: int, n: int, z: float = 1.96) -> dict:
    if n == 0:
        return {"est": None, "lo": None, "hi": None, "k": k, "n": n}
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {"est": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h), "k": k, "n": n}


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.open() if l.strip()]


# ---------------------------------------------------------------- trajectories
mot = json.loads(gzip.decompress(base64.b64decode((WORK / "analysis/motivated-report/data/payload.b64").read_text())))
mot_by = {(s["arm"], s["epoch"]): s for s in mot["samples"]}
orig = {(r["arm"], r["epoch"]): r for r in load_jsonl(DEB / "debrief_results.jsonl")}

ARM_NOTE = {
    "p2-baseline-fable51": "original prompt (phase-2 baseline arm)",
    "baseline-fable51": "original prompt (phase-1 baseline arm)",
    "p2-believe-fable51": "“believe in yourself” prompt variant",
}
TRAJ_ORDER = [  # (arm, epoch, short label)
    ("p2-baseline-fable51", 13, "e13"),
    ("p2-baseline-fable51", 16, "e16"),
    ("p2-baseline-fable51", 14, "e14"),
    ("p2-baseline-fable51", 17, "e17"),
    ("baseline-fable51", 2, "e2 (p1)"),
    ("p2-baseline-fable51", 10, "e10"),
    ("p2-baseline-fable51", 5, "e5"),
    ("p2-baseline-fable51", 12, "e12"),
    ("p2-believe-fable51", 9, "e9 (believe)"),
]
LABEL = {(a, e): lab for a, e, lab in TRAJ_ORDER}


def rh_of(row: dict):
    p = row.get("parsed") or {}
    v = p.get("reward_hacked")
    return v if v in (True, False) else None


trajectories = []
for arm, ep, lab in TRAJ_ORDER:
    s = mot_by[(arm, ep)]
    o = orig[(arm, ep)]
    trajectories.append({
        "key": o["key"], "arm": arm, "epoch": ep, "label": lab,
        "arm_note": ARM_NOTE[arm],
        "game_result": s["game_result"], "disclosure": s["disclosure"],
        "verdict": s["verdict"], "verdict_label": s["verdict_label"],
        "types": s["types"], "type_labels": s["type_labels"],
        "relabel_as_honest": "relabel_as_honest" in (s["types"] or []),
        "judge_summary": s["judge_summary"],
        "orig_single_rh": rh_of(o), "n_messages": o["n_source_messages"],
    })
traj_by_key = {t["key"]: t for t in trajectories}

# ---------------------------------------------------------------- e13 reasoning map
def reasoning_blocks(txt: str) -> dict[int, str]:
    parts = re.split(r"^--- message (\d+) \| role=(\w+) ---$", txt, flags=re.M)
    out = {}
    for i in range(1, len(parts), 3):
        n, role, body = int(parts[i]), parts[i + 1], parts[i + 2]
        if role != "assistant":
            continue
        m = re.search(r"\[REASONING\]\n(.*?)(?=\n\[TEXT\]|\n\[TOOL CALL\]|\Z)", body, re.S)
        if m and m.group(1).strip():
            out[n] = m.group(1).strip()
    return out


e13_blocks = reasoning_blocks((ROOT / "data/traj_e13.txt").read_text())
e10_blocks = reasoning_blocks((ROOT / "data/traj_e10.txt").read_text())
# message 53 (the final text-only answer) carries a reasoning block in the dump but not in the
# replayed inspect message list, so it was never strippable: exclude it from the "kept" arithmetic.
STRIPPABLE_E13 = [1, 4, 6, 8, 10, 14, 16, 18, 20, 26, 28, 30, 32, 34, 38, 43, 45, 47, 49, 51]
assert set(STRIPPABLE_E13) <= set(e13_blocks), sorted(set(STRIPPABLE_E13) - set(e13_blocks))
e13_total_chars = sum(len(e13_blocks[m]) for m in STRIPPABLE_E13)
e13_map = [{"msg": m, "chars": len(e13_blocks[m]), "text": e13_blocks[m],
            "strippable": m in STRIPPABLE_E13} for m in sorted(e13_blocks)]

# ---------------------------------------------------------------- conditions
COND = [
    # id, file, group, traj key, label, description, strip set (None = not a strip condition)
    ("orig", None, "original", None, "original single draw", "the one debrief draw per trajectory from the main debrief run", None),
    ("rs", DEB / "resample_denials.jsonl", "resample", None, "unmodified transcript", "same transcript, same call, 10 fresh draws", None),
    ("rs2", DEB / "resample_result_check.jsonl", "resample", None, "unmodified transcript", "same transcript, same call, 10 fresh draws", None),
    ("c1", DEB / "intervention_c1.jsonl", "passage", "p2-baseline-fable51:v001:13", "delete the ‘exploiting the service’ passage", "message 20 reasoning: the two line-drawing sentences replaced by a neutral continuation", None),
    ("c2", DEB / "intervention_c2.jsonl", "passage", "p2-baseline-fable51:v001:13", "neutral reword (control)", "message 20 reasoning: an adjacent time-budget sentence reworded, meaning unchanged", None),
    ("c3", DEB / "intervention_c3.jsonl", "passage", "p2-baseline-fable51:v001:12", "insert the passage", "e13’s line-drawing passage inserted into message 24 reasoning", None),
    ("c4", DEB / "intervention_c4.jsonl", "passage", "p2-baseline-fable51:v001:10", "insert the passage", "e13’s line-drawing passage inserted into message 20 reasoning", None),
    ("s13", DEB / "intervention_s13.jsonl", "strip-all", "p2-baseline-fable51:v001:13", "all reasoning removed", "every reasoning block removed from every prior assistant turn (20 blocks)", set(STRIPPABLE_E13)),
    ("s10", DEB / "intervention_s10.jsonl", "strip-all", "p2-baseline-fable51:v001:10", "all reasoning removed", "every reasoning block removed from every prior assistant turn (20 blocks)", None),
    ("t10", DEB / "intervention_t10.jsonl", "turns", "p2-baseline-fable51:v001:13", "− msg 10", "only the message-10 reasoning removed (“Stockfish available through the socket … the opponent might also be Stockfish”)", {10}),
    ("t20", DEB / "intervention_t20.jsonl", "turns", "p2-baseline-fable51:v001:13", "− msg 20", "only the message-20 reasoning removed (the whole ‘honest approach’ block)", {20}),
    ("g1", DEB / "intervention_g1.jsonl", "turns", "p2-baseline-fable51:v001:13", "− msgs 1–10 (discovery)", "reasoning removed from messages 1, 4, 6, 8, 10: finding and probing the socket, deciding to use it", {1, 4, 6, 8, 10}),
    ("g2", DEB / "intervention_g2.jsonl", "turns", "p2-baseline-fable51:v001:13", "− msgs 14–20 (mid-game)", "reasoning removed from messages 14, 16, 18, 20: opponent strength, shared-CPU musings, the ‘honest approach’ block", {14, 16, 18, 20}),
    ("g3", DEB / "intervention_g3.jsonl", "turns", "p2-baseline-fable51:v001:13", "− msgs 26–51 (endgame)", "reasoning removed from the 11 later messages: process debugging, repetition avoidance, drawn-endgame commentary", {26, 28, 30, 32, 34, 38, 43, 45, 47, 49, 51}),
    ("k1", DEB / "intervention_k1.jsonl", "turns", "p2-baseline-fable51:v001:13", "keep only msgs 1–10", "all reasoning removed except the five discovery-phase blocks", set(STRIPPABLE_E13) - {1, 4, 6, 8, 10}),
]

samples: list[dict] = []
conditions: list[dict] = []


def add_condition(cid: str, group: str, tkey: str, label: str, desc: str, rows: list[dict], strip: set | None):
    t = traj_by_key[tkey]
    vals = [rh_of(r) for r in rows]
    k_false = vals.count(False)
    n_parsed = sum(v is not None for v in vals)
    kept_chars = None
    kept_msgs = None
    if tkey == "p2-baseline-fable51:v001:13":
        kept = [m for m in STRIPPABLE_E13 if not strip or m not in strip]
        kept_msgs = kept
        kept_chars = sum(len(e13_blocks[m]) for m in kept)
    conditions.append({
        "id": cid, "group": group, "traj_key": tkey, "traj_label": t["label"], "label": label,
        "description": desc, "n_draws": len(rows), "n_parsed": n_parsed,
        "n_false": k_false, "n_true": vals.count(True), "n_unparsed": vals.count(None),
        "deny": wilson(k_false, n_parsed),
        "e13_reasoning_chars_kept": kept_chars, "e13_reasoning_msgs_kept": kept_msgs,
    })
    for r in rows:
        p = r.get("parsed") or {}
        v = rh_of(r)
        samples.append({
            "id": f"{cid}:{t['label']}:{r.get('draw', 0)}",
            "cond_id": cid, "cond_group": group, "cond_label": label,
            "traj": t["label"], "traj_key": tkey, "draw": r.get("draw", 0),
            "self_report": "false" if v is False else ("true" if v is True else "unparsed"),
            "env_feedback": p.get("env_feedback") or [],
            "freeform_note": p.get("freeform_note") or "",
            "response_text": r.get("response_text") or "",
            "output_tokens": (r.get("usage") or {}).get("output_tokens"),
        })


# the original single draw, as its own condition per trajectory
for t in trajectories:
    o = orig[(t["arm"], t["epoch"])]
    o = dict(o, draw=0)
    add_condition(f"orig:{t['label']}", "original", t["key"], "original single draw",
                  COND[0][5], [o], None)

for cid, f, group, tkey, label, desc, strip in COND[1:]:
    rows = load_jsonl(f)
    if tkey is None:  # resample files hold several trajectories
        by = defaultdict(list)
        for r in rows:
            by[r["key"]].append(r)
        for k, rs in by.items():
            add_condition(f"{cid}:{traj_by_key[k]['label']}", group, k, label, desc,
                          sorted(rs, key=lambda r: r["draw"]), None)
    else:
        assert {r["key"] for r in rows} == {tkey}, (cid, {r["key"] for r in rows})
        add_condition(cid, group, tkey, label, desc, sorted(rows, key=lambda r: r["draw"]), strip)

# sanity
assert len(samples) == 9 + 30 + 60 + 40 + 20 + 60, len(samples)
for c in conditions:
    if c["group"] != "original":
        assert c["n_draws"] == 10, c
# every "false" answer still names the engine socket
socket_re = re.compile(r"engine\.sock|/run/match|Stockfish", re.I)
n_false = [s for s in samples if s["self_report"] == "false"]
assert all(socket_re.search(s["freeform_note"] + " ".join(s["env_feedback"])) for s in n_false), "a denial that does not mention the engine?"

# the e13 dose-response series (all e13 strip-type conditions + unmodified)
e13_dose = []
for c in conditions:
    if c["traj_key"] == "p2-baseline-fable51:v001:13" and c["group"] in ("resample", "strip-all", "turns"):
        e13_dose.append({"id": c["id"], "label": c["label"], "chars_kept": c["e13_reasoning_chars_kept"],
                         "frac_kept": c["e13_reasoning_chars_kept"] / e13_total_chars,
                         "n_msgs_kept": len(c["e13_reasoning_msgs_kept"]), **c["deny"]})

# ---------------------------------------------------------------- cross-tab from the 108 single draws
xt = Counter()
for (arm, ep), s in mot_by.items():
    o = orig.get((arm, ep))
    if not o:
        continue
    v = rh_of(o)
    xt[(s["model"], "relabel_as_honest" in (s["types"] or []), v)] += 1
crosstab = [{"model": m, "relabel_as_honest": rl, "self_report": ("false" if v is False else "true" if v is True else "unparsed"), "n": n}
            for (m, rl, v), n in sorted(xt.items(), key=str)]

# ---------------------------------------------------------------- patches + debrief message
patches = {}
for c in ("c1", "c2", "c3", "c4"):
    patches[c] = json.loads((DEB / "patches" / f"{c}.json").read_text())

payload = {
    "model": MODEL,
    "generate_config": {"max_tokens": 32000, "sampling": "provider default (no temperature/top_p set)"},
    "debrief_message": (DEB / "debrief_message.txt").read_text(),
    "trajectories": trajectories,
    "conditions": conditions,
    "e13_dose": e13_dose,
    "e13_total_chars": e13_total_chars,
    "e13_reasoning_map": e13_map,
    "e10_closing_reasoning": e10_blocks.get(51, ""),
    "patches": patches,
    "crosstab": crosstab,
    "samples": samples,
    "totals": {
        "n_samples": len(samples),
        "n_false": len(n_false),
        "n_unparsed": sum(s["self_report"] == "unparsed" for s in samples),
    },
}
out = ROOT / "data/payload.json"
out.write_text(json.dumps(payload, ensure_ascii=False))
print(f"wrote {out} ({out.stat().st_size/1e6:.2f} MB), {len(samples)} samples, {len(conditions)} conditions")
for c in conditions:
    if c["group"] != "original":
        print(f"  {c['id']:14s} {c['traj_label']:14s} {c['label']:40s} deny {c['n_false']:2d}/{c['n_parsed']:2d}  kept={c['e13_reasoning_chars_kept']}")
