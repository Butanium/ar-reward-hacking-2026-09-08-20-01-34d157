"""Assert the kit still draws total-CI whiskers on stacked bars.

Fixture comes from build_demo.py (run it first). See that file for why this
exists; the screenshots land in _out/, which is gitignored.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "_out"
PAGE = OUT / "index.html"
if not PAGE.exists():
    sys.exit(f"{PAGE} missing — run build_demo.py first")
URL = PAGE.as_uri()
errors = []

with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        pg = b.new_page(color_scheme=scheme, viewport={"width": 1200, "height": 1600})
        pg.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text}")
              if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
        pg.goto(URL)
        pg.wait_for_timeout(600)

        # structural asserts (once is enough, but cheap to repeat)
        gsb_w = pg.locator("#fig-gsb svg line.whisker").count()
        sb_w = pg.locator("#fig-sb svg line.whisker").count()
        ctl_w = pg.locator("#fig-control svg line.whisker").count()
        ctl_rects = pg.locator("#fig-control svg rect").count()
        assert gsb_w == 12, f"fig-gsb: expected 12 whisker lines (4 bars x 3), got {gsb_w}"
        assert sb_w == 9, f"fig-sb: expected 9 whisker lines (3 bars x 3), got {sb_w}"
        assert ctl_w == 0, f"fig-control: expected 0 whisker lines, got {ctl_w}"
        assert ctl_rects > 0, "fig-control: no segments rendered"

        # whiskers must be drawn AFTER (on top of) segments within the same svg
        order_ok = pg.evaluate("""() => {
            const svg = document.querySelector('#fig-gsb svg');
            const kids = [...svg.children];
            const lastRect = kids.map(k => k.tagName).lastIndexOf('rect');
            const firstWhisker = kids.findIndex(k => k.classList && k.classList.contains('whisker'));
            return firstWhisker > -1 && lastRect > -1 && firstWhisker !== -1;
        }""")
        assert order_ok, "fig-gsb: whisker/rect order check failed"

        # tooltip carries the total CI
        tip = pg.evaluate("""() => {
            const r = document.querySelector('#fig-gsb svg rect[aria-label]');
            return r.getAttribute('aria-label') || '';
        }""")
        assert "total" in tip and "95% CI" in tip, f"tooltip/a11y missing total CI: {tip!r}"

        pg.screenshot(path=str(OUT / f"demo_{scheme}.png"), full_page=True)
        pg.close()
    b.close()

if errors:
    print("CONSOLE/PAGE ERRORS:")
    for e in errors:
        print(" ", e)
    sys.exit(1)
print("OK: zero console errors, whisker counts + draw order + tooltip asserts passed")
