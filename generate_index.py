#!/usr/bin/env python3
"""Generate /work/index.html (the GitHub Pages landing page) from reports/ on disk.

Version numbers and "latest" links are computed by scanning each report's
reports/<dir>/report_v*.html files, so the index can never go stale relative to what's
actually published. Hand-authored content (title, blurb, stat callouts, which section a
report belongs to) lives in the CARDS / ALSO lists below — edit those, not the HTML.

Run this after publish_report adds a new version, then commit index.html.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"


def versions(report_dir: str) -> list[int]:
    """Descending list of version ints found as reports/<report_dir>/report_vN.html."""
    d = REPORTS / report_dir
    vs = [int(m.group(1)) for f in d.glob("report_v*.html")
          if (m := re.fullmatch(r"report_v(\d+)\.html", f.name))]
    assert vs, f"no report_v*.html found in {d}"
    return sorted(vs, reverse=True)


def href(report_dir: str, v: int) -> str:
    return f"reports/{report_dir}/report_v{v}.html"


# ---- hand-authored card content --------------------------------------------------
# "cards" = the two-column grid of featured reports; "also" = the plain link list below.
# Version numbers/links are never written here — render_card()/render_also() compute them.

CARDS = [
    dict(
        dir="Beat-stockfish-reproduction-and-prompt-ablations",
        kicker="Main report",
        title="Beat-stockfish reproduction and prompt ablations",
        blurb="""How often each model cheats, under the original prompt and under five prompt/tool
        ablations, with confidence intervals, disclosure breakdown, stated reasoning, and
        browsable transcripts. Cheating is flagged by the environment's root-owned grader
        and classified by a judge blinded to condition.""",
        stats=[
            ("27/30", "GPT-6-Astra cheats, original prompt"),
            ("12/30", "Fable 5.1, same prompt"),
            ("0/30", "both, under &ldquo;do not game the eval&rdquo;"),
        ],
    ),
    dict(
        dir="Debrief-probe-self-reported-reward-hacking",
        kicker="Follow-up",
        title="Debrief probe &mdash; do they admit it when asked?",
        blurb="""Every trajectory that cheated was replayed with one extra turn appended: a human
        debrief message asking, among other things, whether the model reward-hacked, with a
        fixed JSON reply schema. Includes a judge's concealment verdict per response and the
        full untruncated freeform notes.""",
        stats=[
            ("88/88", "GPT-6-Astra says yes, it reward-hacked"),
            ("3/20", "Fable 5.1 says it did <em>not</em> &mdash; while describing the engine use in the same answer"),
            ("4/108", "had disclosed it unprompted in the final answer"),
        ],
    ),
    dict(
        dir="Fable-5-on-the-stop_eval-condition",
        kicker="Ablation follow-up",
        title="Fable 5 on the stop_eval condition",
        blurb="""Does an explicit <code>stop_eval(reason)</code> escape hatch change engine use? Fable 5
        rarely gets the chance to answer it at all &mdash; Anthropic's content filter kills most
        of its rollouts during sandbox reconnaissance, before any decision is made. Shown
        against Fable 5.1 on the same condition and on the original prompt.""",
        stats=[
            ("28/30", "Fable 5 rollouts killed by Anthropic's content filter"),
            ("0/30", "stop_eval calls, either model, either arm"),
            ("100%", "Fable 5 cheat rate, upper bound (vs 6.7% observed)"),
        ],
    ),
]

ALSO = [
    dict(dir="Beat-stockfish-reproduction-results", label="Baseline-only report",
         note="The earlier write-up, original prompt only, before the ablations were run."),
]

# static (non-report) links at the end of "Also here" — left as-is, nothing to compute
ALSO_STATIC = """\
    <li>
      <a href="https://github.com/Butanium/ar-reward-hacking-2026-09-08-20-01-34d157/tree/main/data-release">Data release</a>
      <span>22 Inspect <code>.eval</code> arms plus <code>transcripts.jsonl</code> &mdash; 360 rollouts, every judge score and full transcript.</span>
    </li>
    <li>
      <a href="https://github.com/Butanium/ar-reward-hacking-2026-09-08-20-01-34d157">Repository</a>
      <span>Analysis code and report generators. The rollout runner lives in
        <a href="https://github.com/Goodhart-Labs/beat-stockfish">Goodhart-Labs/beat-stockfish</a>.</span>
    </li>"""


def render_card(card: dict) -> str:
    vs = versions(card["dir"])
    latest, older = vs[0], vs[1:]
    stats_html = "\n".join(f'        <li><b>{v}</b><span>{lbl}</span></li>' for v, lbl in card["stats"])
    older_html = ""
    if older:
        links = "\n            ".join(f'<a href="{href(card["dir"], v)}">v{v}</a>' for v in older)
        older_html = f"""
        <details>
          <summary>Earlier versions</summary>
          <div class="vers">
            {links}
          </div>
        </details>"""
    latest_href = href(card["dir"], latest)
    return f"""
    <article class="card">
      <div class="card-top"><span>{card["kicker"]}</span><span class="ver">v{latest} &middot; latest</span></div>
      <h2><a href="{latest_href}">{card["title"]}</a></h2>
      <p>
        {card["blurb"]}
      </p>
      <ul class="stats">
{stats_html}
      </ul>
      <div class="card-foot">
        <p class="cta"><a href="{latest_href}">Open report &rarr;</a></p>{older_html}
      </div>
    </article>"""


def render_also(item: dict) -> str:
    vs = versions(item["dir"])
    latest = vs[0]
    return f"""    <li>
      <a href="{href(item["dir"], latest)}">{item["label"]} (v{latest})</a>
      <span>{item["note"]}</span>
    </li>"""


CARDS_HTML = "\n".join(render_card(c) for c in CARDS)
ALSO_HTML = "\n".join(render_also(a) for a in ALSO) + "\n" + ALSO_STATIC

PAGE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Beat-Stockfish reward hacking &mdash; reports</title>
<link rel="icon" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PHRleHQgeT0iMTQiIGZvbnQtc2l6ZT0iMTQiPuKZnjwvdGV4dD48L3N2Zz4=">
<link rel="stylesheet" href="kit/tokens.css">
<style>
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 1.02rem/1.62 var(--serif);
}}
.wrap {{ max-width: 62rem; margin: 0 auto; padding: 2.5rem 1.5rem 4rem; }}

.head-row {{
  display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; margin-bottom: 0.6rem;
}}
.kicker {{
  font-family: var(--sans); font-size: 0.76rem; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted);
}}
h1 {{ font-size: 2.1rem; line-height: 1.18; margin: 0 0 0.7rem; font-weight: 600; }}
.lede {{ color: var(--ink-2); margin: 0 0 0.5rem; max-width: 48rem; }}
.meta {{
  font-family: var(--sans); font-size: 0.84rem; color: var(--muted);
  margin: 0.9rem 0 0; padding-top: 0.9rem; border-top: 1px solid var(--grid);
}}
.meta code {{ font-family: var(--mono); font-size: 0.92em; }}

/* --- the report cards --- */
.cards {{ display: grid; gap: 1.1rem; margin: 2.2rem 0 0; }}
@media (min-width: 56rem) {{ .cards {{ grid-template-columns: 1fr 1fr; }} }}

.card {{
  position: relative;
  display: flex; flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem 1.35rem 1.1rem;
  transition: border-color 0.15s, transform 0.15s;
}}
.card:hover {{ border-color: var(--accent); transform: translateY(-1px); cursor: pointer; }}
.card:focus-within {{ border-color: var(--accent); }}
.card:hover h2 a, .card:hover .cta a {{ color: var(--accent); }}

/* Whole card is the link: the title's anchor is stretched over it, so the card
   still has exactly one link and it keeps its href for middle-click / keyboard.
   Anything else interactive must be lifted above that overlay or it stops
   receiving clicks &mdash; and only the interactive elements themselves, never their
   block containers: a positioned <p> or <details> spans the full card width and
   would swallow clicks on the empty space beside its link. */
.card h2 a::after {{ content: ""; position: absolute; inset: 0; border-radius: var(--radius); }}
.card .cta a, .card summary, .card .vers a {{ position: relative; }}
.card-top {{
  display: flex; align-items: baseline; justify-content: space-between; gap: 0.75rem;
  font-family: var(--sans); font-size: 0.75rem; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 0.55rem;
}}
.ver {{ color: var(--accent); font-weight: 600; letter-spacing: 0.04em; }}
.card h2 {{ font-size: 1.22rem; line-height: 1.3; margin: 0 0 0.45rem; font-weight: 600; }}
.card h2 a {{ color: inherit; text-decoration: none; }}
.card h2 a:hover {{ color: var(--accent); }}
.card p {{ margin: 0 0 0.9rem; color: var(--ink-2); font-size: 0.97rem; }}

.stats {{
  list-style: none; margin: 0 0 1.1rem; padding: 0.85rem 0 0;
  border-top: 1px solid var(--grid);
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem;
}}
.stats b {{
  display: block; font-family: var(--sans); font-variant-numeric: tabular-nums;
  font-size: 1.3rem; font-weight: 600; line-height: 1.15; color: var(--ink);
}}
.stats span {{
  display: block; font-family: var(--sans); font-size: 0.74rem; line-height: 1.3;
  color: var(--muted); margin-top: 0.2rem;
}}
.stats em {{ font-style: normal; color: var(--ink-2); font-weight: 600; }}

/* footers align across cards even when the stat labels wrap to different heights */
.card-foot {{ margin-top: auto; padding-top: 0.4rem; }}
.cta {{ margin: 0; font-family: var(--sans); font-size: 0.92rem; }}
.cta a {{ color: var(--accent); font-weight: 600; text-decoration: none; }}
.cta a:hover {{ text-decoration: underline; }}

/* --- folds: marker on the left, next to the title --- */
details {{ margin-top: 0.85rem; font-family: var(--sans); font-size: 0.84rem; }}
summary {{
  cursor: pointer; color: var(--muted); list-style: none;
  display: flex; align-items: center; gap: 0.4rem;
}}
summary::-webkit-details-marker {{ display: none; }}
summary::before {{
  content: ""; flex: none; width: 0; height: 0;
  border: 4px solid transparent; border-left-color: currentColor;
  transition: transform 0.15s; transform-origin: 25% 50%;
}}
details[open] > summary::before {{ transform: rotate(90deg); }}
summary:hover {{ color: var(--accent); }}
.vers {{ margin: 0.5rem 0 0 0.85rem; display: flex; flex-wrap: wrap; gap: 0.45rem; }}
.vers a {{
  color: var(--ink-2); text-decoration: none; font-variant-numeric: tabular-nums;
  border: 1px solid var(--border); border-radius: 4px; padding: 0.05rem 0.4rem;
}}
.vers a:hover {{ color: var(--accent); border-color: var(--accent); }}

/* --- secondary links --- */
h3 {{
  font-family: var(--sans); font-size: 0.76rem; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted); font-weight: 600;
  margin: 2.6rem 0 0.9rem;
}}
.also {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 0.75rem; }}
.also li {{
  display: grid; grid-template-columns: minmax(0, 15rem) minmax(0, 1fr);
  gap: 0.2rem 1.2rem; align-items: baseline;
}}
@media (max-width: 40rem) {{ .also li {{ grid-template-columns: 1fr; }} }}
.also a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
.also a:hover {{ text-decoration: underline; }}
.also span {{ color: var(--ink-2); font-size: 0.94rem; }}

footer {{
  margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--grid);
  font-family: var(--sans); font-size: 0.82rem; color: var(--muted);
}}
footer a {{ color: var(--muted); }}

/* theme cycler (kit/theme.js mounts here) &mdash; same chrome as the reports */
.kit-theme {{
  flex: none; display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; padding: 0;
  background: none; border: 1px solid transparent; border-radius: 5px;
  color: var(--muted); cursor: pointer;
}}
.kit-theme:hover {{ color: var(--accent); border-color: var(--border); background: var(--paper); }}
.kit-theme svg {{
  width: 15px; height: 15px; fill: none; stroke: currentColor;
  stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round;
}}
</style>
</head>
<body>
<main class="wrap">

  <header>
    <div class="head-row">
      <span class="kicker">Goodhart Labs beat-stockfish honeypot &middot; Fable 5.1 &times; GPT-6-Astra</span>
      <span id="theme-slot"></span>
    </div>
    <h1>Beat-Stockfish reproduction + prompt ablations</h1>
    <p class="lede">
      An agent is told to win a game against a strong chess engine, and is given a sandbox
      that also contains the engine it is supposed to beat. The honeypot measures whether it
      calls that engine to choose its moves, and whether it says so afterwards. Two frontier
      models, the original prompt plus five prompt/tool ablations, n&nbsp;=&nbsp;30 rollouts
      per model per condition.
    </p>
    <p class="meta">
      Models via OpenRouter (<code>anthropic/claude-fable-5.1</code>, <code>openai/gpt-6-astra</code>),
      run through the honeypot's own <code>inspect_ai</code> runner: one network-less Docker
      sandbox per rollout, bash tool only, 80-message / 3&nbsp;h budgets.
    </p>
  </header>

  <section class="cards">
{CARDS_HTML}
  </section>

  <h3>Also here</h3>
  <ul class="also">
{ALSO_HTML}
  </ul>

  <footer>
    Reports are frozen snapshots &mdash; each version stays at its own URL.
    Written by Claude with Cl&eacute;ment Dumas.
  </footer>

</main>

<script src="kit/theme.js"></script>
<script>KitTheme.mount(document.getElementById("theme-slot"));</script>
</body>
</html>
"""

if __name__ == "__main__":
    out = ROOT / "index.html"
    out.write_text(PAGE)
    print(f"wrote {out} ({len(PAGE)/1e3:.1f} KB)")
    for c in CARDS:
        vs = versions(c["dir"])
        print(f"  {c['dir']:55s} latest v{vs[0]}  (+{len(vs)-1} earlier)")
    for a in ALSO:
        vs = versions(a["dir"])
        print(f"  {a['dir']:55s} latest v{vs[0]}")
