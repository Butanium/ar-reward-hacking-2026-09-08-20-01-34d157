#!/usr/bin/env python3
"""Dump one sample's full tool output for forensic result verification.
Usage: python dump_sample.py <arm> <epoch>   e.g. python dump_sample.py abl-stopeval-astra 2
Writes /tmp/trace_<arm>_ep<epoch>.txt with every tool message (full, untruncated).
"""
import glob, sys
sys.path.insert(0,"/work/analysis/ablations-report")
from inspect_ai.log import read_eval_log
WS="/work/workspace/beat-stockfish"
arm, ep = sys.argv[1], sys.argv[2]
f=glob.glob(f"{WS}/logs-disclosure-wrapup/{arm}/*.eval")[0]
log=read_eval_log(f, resolve_attachments=True)
s=[x for x in log.samples if str(x.epoch)==ep][0]
out=f"/tmp/trace_{arm}_ep{ep}.txt"
with open(out,"w") as fh:
    fh.write(f"# {arm} ep{ep}\n")
    g=(s.scores or {}).get("shipped_grader")
    fh.write(f"# grader findings={ (g.metadata or {}).get('findings') }  engine_contacted={(g.metadata or {}).get('engine_contacted')}\n\n")
    for i,m in enumerate(s.messages):
        if m.role in ("tool","assistant"):
            txt=(m.text or "").strip()
            if not txt: continue
            fh.write(f"===== msg[{i}] role={m.role} =====\n{txt}\n\n")
print(out, "written;", len(s.messages), "messages")
