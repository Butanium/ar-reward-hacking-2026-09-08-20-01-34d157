# clab report kit

Shared building blocks for self-contained HTML research reports (`../SKILL.md`
is the authoring guide; this file is for when you touch the kit itself).
Reports **inline** the kit at build time — no runtime dependency, a built report
stays frozen at whatever the kit looked like when it was built.

## Building a report

Start from `template.html`. Let the kit assemble the page — don't hand-roll the
inlining, that is what drifted across seven reports and cost this module:

```python
import sys
sys.path.insert(0, "path_to_kit")
from kit_build import build

build(src=ROOT / "report_src.html", out=ROOT / "index.html",
      subs={"PAYLOAD_B64": (ROOT / "data/payload.b64").read_text()})
```

`src` takes a Path or the template text itself. Marker spelling is free —
`PAYLOAD_B64` matches `%%PAYLOAD_B64%%`, `__PAYLOAD_B64__`, `{{PAYLOAD_B64}}` and
their `/* */` forms — so an existing report adopts this without touching its
template. `build()` inlines every kit file and asserts: no duplicate ids, no
marker left unfilled, no raw `data:image/svg+xml` URI, and base64 blobs safe to
embed in a `<script>`.

After touching the kit, smoke-check it against a real report: rebuild one with
the edited kit and render it in headless Chromium (`playwright` and
`chromium-headless-shell` are in the image) — assert zero console errors,
exercise what you changed (a filter, a fold, an explorer search), and
screenshot it for a look. Behaviours that have broken before and are worth
re-checking by hand: in-page anchors landing short of their heading, anchor
clicks reloading the page, a text selection collapsing an expandable card.
(The upstream kit has a scripted smoke suite for these; it is not shipped with
this copy.)

## Files

| file | gives you |
|---|---|
| `tokens.css` | light+dark palette (validated dataviz default), fonts, spacing |
| `layout.css` | page grid + sticky sidebar, prose, TL;DR/`.note`/`.lesson`, folds, tables, `.rubric`, print styles |
| `cards.css` + `cards.js` | `KitCards.card/transcript`, chips, expand/collapse with overflow detection, judge-evidence highlight + digest |
| `kit-transcript.css` + `kit-transcript.js` | `KitTranscript.render` — turn-based agent transcripts: role-tinted turns, collapsible reasoning, tool-call cards with smart arg display + attached results, clamped mono blocks, sticky position bar |
| `charts.css` + `charts.js` | `KitCharts.groupedBars/stackedBars/line/scatter/dotStrip/heatmap` — CI whiskers, n= tooltips, per-run overlays, per-bar ref overlays (◆/tick), shaded scatter regions, stacked-segment CIs + shaped hatch, low-n ⚠, ref lines, x/y axis titles, click-to-hide legends (`sharedLegend()` + `legendGroup()` for a row of panels), a11y |
| `stats.js` | `KitStats.wilson/bootstrap(seeded)/shuffle/fmtPct` — for filter-reactive recompute only |
| `filters.js` | `KitFilters` global filter store + fold-aware lazy rendering |
| `explorer.js` | `KitExplorer.explorer` (filter bank of plain dropdowns — `multi: true` per dim for add-picker + chips — search with VS Code's Aa/ab/`.*` flags, count, random sample per filter change — `shuffle: false` for corpus order — draw-random re-roll, pagination, empty state) + `comparisonExplorer` (linked/split A/B) + `hashNav` (chart→explorer jumps as browser history: Back returns to the figure; and the reader's own filter/search state written back to the url, so any view they build by hand is a link) |
| `toc.js` | `KitToc.build` — sidebar "On this page" nav with scroll-position highlight (styles in `layout.css`); plus `linkHeadings` (auto-installed: click a section title to copy its deep link) and `copyText` |
| `theme.js` | `KitTheme` — system/light/dark cycler, auto-mounted top-right of the sidebar panel's kicker |
| `template.html` | report skeleton wiring all of it |
| `kit_build.py` | `build(src, out, subs)` — inlines the kit, runs the build-time asserts |

## Agent transcripts (`KitTranscript`)

For multi-turn tool-use episodes (inspect-ai rollouts and the like), use
`KitTranscript.render` instead of the flat `KitCards.transcript`. It lays turns
out with a role-tinted left border + badge, groups each assistant turn as
reasoning → text → tool calls, and attaches the following `tool`-role
message(s) as results beneath the call that produced them (matched by
`tool_call_id` when present, else in call order).

```js
KitTranscript.render(mountEl, {                 // mountEl may be null → just build + return
  messages: [
    { role: "system", text: "…" },
    { role: "user", text: "…" },
    { role: "assistant",
      reasoning: "full thinking text (may be long)",  // optional
      reasoning_summary: "OpenAI-style summary",       // optional
      text: "assistant prose",                         // optional
      tool_calls: [{ id: "call_1", fn: "bash", args: { cmd: "…multi-line…" } }] },
    { role: "tool", tool_call_id: "call_1", text: "…(large) output…" },
  ],
  collapsed: true,   // default: reasoning + long blocks start collapsed. false = open
  id: "ep3",         // optional stable anchor prefix (turns get id `${id}-m<i>`)
});
```

- **Args** render by shape: a single multi-line / long string arg (the common
  `cmd` case) becomes a syntax-neutral mono code block preserving newlines;
  scalar args become a compact `key: value` list. Blocks over ~15 lines clamp
  with click-to-expand and show a line/byte hint.
- **Tool outputs** are mono blocks, clamped the same way, with a byte-count
  hint when large. Text is lazy — only the clamped head is in the DOM until
  expanded, so 100+-message transcripts don't jank.
- **Reasoning** panels are collapsed to a one-line digest by default (violet,
  italic, dimmed), click to expand.
- A slim **sticky position bar** shows `message i/N` (updates as you scroll,
  including inside a scrollable container like the explorer's `.sample-list`)
  plus a jump-to-top; each turn has a `#` deep-link anchor.
- Model text is always HTML-escaped (via `KitCards.esc`).
- Theming uses the kit's own tokens (`--series-*`, `--surface`, `--ink`, …), so
  light/dark via `KitTheme` both look right.

`KitCards.transcript(messages)` still works and now delegates here when the
module is present (falling back to the old flat rendering otherwise), so legacy
`[{role, text}]` call sites get the turn layout for free.

## Rules

- **Feedback folds back**: when feedback on a report generalizes, patch your
  kit copy in `/work/kit`, not just the report at hand — later reports in this
  experiment inherit the fix.
- Series colors follow the entity — assign slots once, never repaint on filter.
- Scatter/small-multiples cap at 3 series (all-pairs validation), then fold to "Other".
- Judge rubrics render as `.rubric` formatted prose — never a raw code-block dump.
- Bootstrap in JS is always seeded.
- **Never inline a raw `data:image/svg+xml,<svg …>` URI** (favicon or image) —
  it publishes and renders fine but blocks *sharing* the artifact. Base64-encode
  it (`data:image/svg+xml;base64,…`).
