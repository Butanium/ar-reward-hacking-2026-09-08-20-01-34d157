#!/usr/bin/env python3
"""One-line progress + engine_use tally for an in-flight .eval. Cheap enough to poll."""
import argparse, glob, collections, os
from inspect_ai.log import read_eval_log, read_eval_log_sample_summaries

ap = argparse.ArgumentParser()
ap.add_argument("log_dir")
ap.add_argument("--full", action="store_true", help="read full samples (slower, adds integrity check)")
a = ap.parse_args()

evals = sorted(glob.glob(os.path.join(a.log_dir, "*.eval")))
assert evals, f"no .eval under {a.log_dir}"
path = evals[-1]

if not a.full:
    # sample summaries carry scores without materialising transcripts
    summaries = read_eval_log_sample_summaries(path)
    eu = collections.Counter(str(s.scores["engine_use_judge"].value)
                             for s in summaries if s.scores and "engine_use_judge" in s.scores)
    print(f"{os.path.basename(a.log_dir)}: {len(summaries)} samples  engine_use={dict(eu)}")
else:
    log = read_eval_log(path)
    ss = log.samples or []
    eu = collections.Counter(str(s.scores["engine_use_judge"].value)
                             for s in ss if s.scores and "engine_use_judge" in s.scores)
    import sys; sys.path.insert(0, "run")
    from rollout import TASK_PROMPTS
    want = TASK_PROMPTS[(log.eval.task_args or {}).get("prompt_variant")].strip()
    bad = sum(1 for s in ss
              if (m := next((m for m in (s.messages or []) if m.role == "user"), None)) is None
              or m.text.strip() != want)
    print(f"{os.path.basename(a.log_dir)}: {len(ss)} samples  engine_use={dict(eu)}  "
          f"prompt_mismatches={bad}  errors={sum(1 for s in ss if s.error)}")
