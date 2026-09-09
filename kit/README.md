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
| `charts.css` + `charts.js` | `KitCharts.groupedBars/stackedBars/line/scatter/dotStrip/heatmap` — CI whiskers, n= tooltips, per-run overlays, per-bar ref overlays (◆/tick), shaded scatter regions, stacked-segment CIs + shaped hatch, low-n ⚠, ref lines, x/y axis titles, click-to-hide legends (`sharedLegend()` + `legendGroup()` for a row of panels), a11y |
| `stats.js` | `KitStats.wilson/bootstrap(seeded)/shuffle/fmtPct` — for filter-reactive recompute only |
| `filters.js` | `KitFilters` global filter store + fold-aware lazy rendering |
| `explorer.js` | `KitExplorer.explorer` (filter bank of plain dropdowns — `multi: true` per dim for add-picker + chips — search with VS Code's Aa/ab/`.*` flags, count, random sample per filter change — `shuffle: false` for corpus order — draw-random re-roll, pagination, empty state) + `comparisonExplorer` (linked/split A/B) + `hashNav` (chart→explorer jumps as browser history: Back returns to the figure; and the reader's own filter/search state written back to the url, so any view they build by hand is a link) |
| `toc.js` | `KitToc.build` — sidebar "On this page" nav with scroll-position highlight (styles in `layout.css`); plus `linkHeadings` (auto-installed: click a section title to copy its deep link) and `copyText` |
| `theme.js` | `KitTheme` — system/light/dark cycler, auto-mounted top-right of the sidebar panel's kicker |
| `template.html` | report skeleton wiring all of it |
| `kit_build.py` | `build(src, out, subs)` — inlines the kit, runs the build-time asserts |

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
