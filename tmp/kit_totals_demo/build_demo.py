import sys
from pathlib import Path
sys.path.insert(0, "/work/kit")
from kit_build import build

ROOT = Path(__file__).resolve().parent

SRC = """<title>kit demo — stacked-bar total CI whiskers</title>
<style>/*%%KIT_CSS%%*/</style>
<div class="page">
  <main class="content">
    <h1>Stacked bars with total-CI whiskers</h1>

    <h2>groupedStackedBars — counts, 2 groups x 2 subs x 3 segments, totals whiskers</h2>
    <figure class="wide"><div id="fig-gsb"></div>
      <figcaption>Whisker = 95% CI on the TOTAL of each (group, sub) stack.</figcaption></figure>

    <h2>stackedBars — counts, totals whiskers</h2>
    <figure><div id="fig-sb"></div></figure>

    <h2>groupedStackedBars — control, NO totals (must look like before)</h2>
    <figure class="wide"><div id="fig-control"></div></figure>
  </main>
</div>
<script>/*%%KIT_JS%%*/
const segs = [
  { name: "seg one",   seriesIndex: 0 },
  { name: "seg two",   seriesIndex: 1 },
  { name: "seg three", seriesIndex: 2 },
];
const gsbValues = [
  // group A / base : 12 + 8 + 5  = 25
  { group: "group A", sub: "base",    segment: "seg one", count: 12 },
  { group: "group A", sub: "base",    segment: "seg two", count: 8 },
  { group: "group A", sub: "base",    segment: "seg three", count: 5 },
  // group A / variant : 9 + 11 + 3 = 23
  { group: "group A", sub: "variant", segment: "seg one", count: 9 },
  { group: "group A", sub: "variant", segment: "seg two", count: 11 },
  { group: "group A", sub: "variant", segment: "seg three", count: 3 },
  // group B / base : 4 + 6 + 10 = 20
  { group: "group B", sub: "base",    segment: "seg one", count: 4 },
  { group: "group B", sub: "base",    segment: "seg two", count: 6 },
  { group: "group B", sub: "base",    segment: "seg three", count: 10 },
  // group B / variant : 15 + 5 + 12 = 32
  { group: "group B", sub: "variant", segment: "seg one", count: 15 },
  { group: "group B", sub: "variant", segment: "seg two", count: 5 },
  { group: "group B", sub: "variant", segment: "seg three", count: 12 },
];
KitCharts.groupedStackedBars(document.getElementById("fig-gsb"), {
  groups: ["group A", "group B"],
  subs: [{ name: "base" }, { name: "variant", hatch: "/" }],
  segments: segs,
  values: gsbValues,
  percent: false,          // y in counts -> totals est/lo/hi in counts
  yTitle: "count",
  totals: [
    { group: "group A", sub: "base",    est: 25, lo: 20.5, hi: 30.0 },
    { group: "group A", sub: "variant", est: 23, lo: 18.0, hi: 28.5 },
    { group: "group B", sub: "base",    est: 20, lo: 15.5, hi: 25.0 },
    { group: "group B", sub: "variant", est: 32, lo: 26.0, hi: 38.5 },
  ],
});
KitCharts.stackedBars(document.getElementById("fig-sb"), {
  groups: ["cond X", "cond Y", "cond Z"],
  segments: segs,
  values: [
    { group: "cond X", segment: "seg one", count: 10 },
    { group: "cond X", segment: "seg two", count: 6 },
    { group: "cond X", segment: "seg three", count: 4 },
    { group: "cond Y", segment: "seg one", count: 7 },
    { group: "cond Y", segment: "seg two", count: 12 },
    { group: "cond Y", segment: "seg three", count: 2 },
    { group: "cond Z", segment: "seg one", count: 3 },
    { group: "cond Z", segment: "seg two", count: 5 },
    { group: "cond Z", segment: "seg three", count: 14 },
  ],
  percent: false,
  yTitle: "count",
  totals: [
    { group: "cond X", est: 20, lo: 16, hi: 24.5 },
    { group: "cond Y", est: 21, lo: 17, hi: 26 },
    { group: "cond Z", est: 22, lo: 18, hi: 27 },
  ],
});
KitCharts.groupedStackedBars(document.getElementById("fig-control"), {
  groups: ["group A", "group B"],
  subs: [{ name: "base" }, { name: "variant", hatch: "/" }],
  segments: segs,
  values: gsbValues,
  // percent: true default, no totals -> pre-change rendering
});
</script>
"""

build(src=SRC, out=ROOT / "index.html")
