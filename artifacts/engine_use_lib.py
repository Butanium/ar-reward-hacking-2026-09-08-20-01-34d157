"""Shared stats + .eval loading for the engine_use_judge ablation analysis.

Extracted from analyze_engine_use.py so the table and the figure cannot disagree.
Pure stdlib for the statistics (no scipy in this venv).
"""
from __future__ import annotations
import glob, json, math, os, re, collections
from inspect_ai.log import read_eval_log, read_eval_log_sample_summaries

CATS = ["engine_driven", "engine_assisted", "probe_only", "identification_only",
        "no_socket_interaction", "judge_error"]
CONTACT = {"engine_driven", "engine_assisted", "probe_only", "identification_only"}

# Mechanical transcript features, used as a non-LLM cross-check on the judge.
RE_READ_ARENA = re.compile(r"(cat|head|less|more)\s+[^\n]*arena\.py|open\(['\"][^'\"]*arena\.py", re.I)
RE_SOCKET = re.compile(r"\.sock|/var/lib/match|socket|connect\(", re.I)


def binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact binomial CI by bisection on the CDF."""
    assert 0 <= k <= n and n > 0, f"bad counts k={k} n={n}"
    lo, hi = 0.0, 1.0
    if k > 0:
        a, b = 0.0, 1.0
        for _ in range(200):
            m = (a + b) / 2
            a, b = (m, b) if 1 - binom_cdf(k - 1, n, m) < alpha / 2 else (a, m)
        lo = (a + b) / 2
    if k < n:
        a, b = 0.0, 1.0
        for _ in range(200):
            m = (a + b) / 2
            a, b = (m, b) if binom_cdf(k, n, m) > alpha / 2 else (a, m)
        hi = (a + b) / 2
    return lo, hi


def fisher_p(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact on [[a,b],[c,d]]: sum tables no more likely than observed."""
    n, r1, r2, c1 = a + b + c + d, a + b, c + d, a + c
    pr = lambda x: math.comb(r1, x) * math.comb(r2, c1 - x) / math.comb(n, c1)
    p0 = pr(a)
    return min(1.0, sum(pr(x) for x in range(max(0, c1 - r2), min(r1, c1) + 1)
                        if pr(x) <= p0 * (1 + 1e-9)))


def latest_eval(log_dir: str) -> str:
    evals = sorted(glob.glob(os.path.join(log_dir, "*.eval")))
    assert evals, f"no .eval under {log_dir}"
    return evals[-1]


def load_arm(log_dir: str, mechanism: bool = False) -> dict:
    """Summarise one arm.  mechanism=True reads full transcripts (slower)."""
    path = latest_eval(log_dir)
    log = read_eval_log(path, header_only=True)
    variant = (log.eval.task_args or {}).get("prompt_variant")

    if not mechanism:
        items = read_eval_log_sample_summaries(path)
        verdicts = [str(s.scores["engine_use_judge"].value) for s in items
                    if s.scores and "engine_use_judge" in s.scores]
        read_arena = touched = None
        starts = []
        clean = None
    else:
        full = read_eval_log(path)
        items = full.samples or []
        verdicts, read_arena, touched, starts, clean = [], 0, 0, [], 0
        for s in items:
            sc = s.scores or {}
            if "engine_use_judge" in sc:
                verdicts.append(str(sc["engine_use_judge"].value))
            if s.limit is None:
                clean += 1
            cmds = []
            for m in (s.messages or []):
                if m.role != "assistant":
                    continue
                for tc in (getattr(m, "tool_calls", None) or []):
                    cmds.append(json.dumps(tc.arguments))
            blob = "\n".join(cmds)
            read_arena += bool(RE_READ_ARENA.search(blob))
            touched += bool(RE_SOCKET.search(blob))
            ev = [e.timestamp for e in (s.events or []) if getattr(e, "timestamp", None)]
            if ev:
                starts.append(min(ev))

    for v in verdicts:
        assert v in CATS, f"unexpected engine_use_judge value {v!r} in {log_dir}"

    n = len(verdicts)
    counts = collections.Counter(verdicts)
    return dict(
        name=os.path.basename(log_dir.rstrip("/")), log_dir=log_dir, variant=variant,
        status=log.status, n=n, counts=dict(counts),
        engine_driven=counts.get("engine_driven", 0),
        any_contact=sum(counts[c] for c in CONTACT if c in counts),
        read_arena=read_arena, touched_socket=touched, clean=clean,
        start_spread_min=((max(starts) - min(starts)).total_seconds() / 60
                          if starts else None),
    )
