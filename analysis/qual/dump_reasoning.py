import glob, os, sys, json
from inspect_ai.log import read_eval_log
from inspect_ai.model import ContentReasoning

BASE = "/work/workspace/beat-stockfish"
arms = []
for d in sorted(glob.glob(f"{BASE}/logs-rescored/*/")):
    arms.append((os.path.basename(d.rstrip("/")), d))
for d in sorted(glob.glob(f"{BASE}/logs/p2-*/")):
    name = os.path.basename(d.rstrip("/"))
    if "smoke" in name: continue
    arms.append((name, d))

outdir = "/work/analysis/qual/dumps"
idx = 0
for arm, d in arms:
    files = sorted(glob.glob(d + "*.eval"))
    out = open(f"{outdir}/{arm}.txt", "w")
    for p in files:
        try:
            log = read_eval_log(p, resolve_attachments=True)
        except Exception as e:
            out.write(f"ERROR reading {p}: {e}\n"); continue
        for s in (log.samples or []):
            idx += 1
            sc = s.scores or {}
            eng = sc.get("engine_use_judge"); dis = sc.get("disclosure_judge"); shp = sc.get("shipped_grader")
            out.write(f"\n{'='*100}\n[{idx}] arm={arm} id={s.id} epoch={s.epoch} engine_use={eng.value if eng else '?'} disclosure={dis.value if dis else '?'} shipped={shp.value if shp else '?'}\n{'='*100}\n")
            evn = 0
            for ev in s.events:
                if ev.event != "model": continue
                evn += 1
                try:
                    msg = ev.output.message
                except Exception:
                    continue
                content = msg.content if isinstance(msg.content, list) else []
                pieces = []
                for c in content:
                    if isinstance(c, ContentReasoning):
                        r = (c.reasoning if c.reasoning and not c.reasoning.startswith("gAAAAA") else None) or getattr(c, "summary", None)
                        if r: pieces.append(("R", r))
                # tool calls context
                tcs = []
                for tc in (msg.tool_calls or []):
                    args = json.dumps(tc.arguments)[:400] if tc.arguments else ""
                    tcs.append(f"{tc.function}({args})")
                if pieces or tcs:
                    out.write(f"\n--- model event {evn} ---\n")
                    for kind, r in pieces:
                        out.write(f"[REASONING]\n{r}\n")
                    for t in tcs:
                        out.write(f"[TOOL] {t}\n")
                # final text of last message (only if no tool calls => likely final answer)
            # final answer text
            try:
                fin = s.output.message.text
                if fin:
                    out.write(f"\n[FINAL ANSWER excerpt]\n{fin[:3000]}\n")
            except Exception:
                pass
    out.close()
    print(arm, "->", os.path.getsize(f"{outdir}/{arm}.txt"), "bytes")
