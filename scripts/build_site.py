#!/usr/bin/env python3
"""Regenerate index.html and stage the GitHub Pages site into a directory.

The published site is only the landing page plus what it links to (kit/ for the
shared CSS/JS, reports/ for the report snapshots) — not data-release/, analysis/
or artifacts/, which are ~130 MB and are linked from the index as github.com
URLs rather than Pages paths.

Staged reports get a "Back to reports" link injected at the top. It is added
here rather than in the file because report_v*.html are frozen snapshots, and
because that way every version gets one, including the ones built before the
landing page existed.

Verification before deploy:
  - every local href in index.html must resolve to a staged file (a broken one
    is a 404 on the live site, and nothing else would catch it)
  - every reports/<dir> holding report_v*.html should be reachable from the
    index; an unreferenced one is a published report nobody can find, so it is
    reported as a warning rather than silently ignored
  - every latest report_vN.html should have a report_vN.md beside it, and every
    staged report should have taken the back link

Run it the same way CI does:
    python scripts/build_site.py --out _site
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
SITE_PATHS = ["index.html", ".nojekyll", "kit", "reports", "extras"]


def log(msg: str) -> None:
    print(msg, flush=True)


def annotate(level: str, msg: str) -> None:
    """A GitHub Actions annotation when running in CI, a plain line otherwise."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level}::{msg}", flush=True)
    else:
        print(f"{level.upper()}: {msg}", flush=True)


def generate_index() -> None:
    log("• running generate_index.py")
    r = subprocess.run([sys.executable, "generate_index.py"], cwd=ROOT,
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"generate_index.py failed (exit {r.returncode})")


def stage(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in SITE_PATHS:
        src = ROOT / name
        if not src.exists():
            if name == ".nojekyll":
                (out / name).touch()
                continue
            raise SystemExit(f"missing site path: {src}")
        if src.is_dir():
            shutil.copytree(src, out / name)
        else:
            shutil.copy2(src, out / name)
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    n = sum(1 for f in out.rglob("*") if f.is_file())
    log(f"• staged {n} files, {total / 1e6:.0f} MB -> {out}")


BACK_LINK_CLASS = "kit-back-to-index"

# Styled off the kit's tokens so it follows the theme the reader picked, with
# literal fallbacks for any report built before a token existed.
BACK_LINK_CSS = f"""<style>
/* injected by scripts/build_site.py */
.{BACK_LINK_CLASS} {{
  display: inline-flex; align-items: center; gap: 0.4em;
  font: 600 0.78rem/1 var(--sans, system-ui, -apple-system, sans-serif);
  color: var(--ink-2, #52514e); text-decoration: none;
  background: var(--surface, #fcfcfb);
  border: 1px solid var(--border, rgba(11, 11, 11, 0.12));
  border-radius: 999px; padding: 0.42em 0.9em 0.42em 0.72em;
  margin: 0 0 1.15rem; transition: color 0.15s, border-color 0.15s;
}}
.{BACK_LINK_CLASS}:hover {{ color: var(--accent, #256abf); border-color: var(--accent, #256abf); }}
.{BACK_LINK_CLASS}:focus-visible {{ outline: 2px solid var(--accent, #256abf); outline-offset: 2px; }}
@media print {{ .{BACK_LINK_CLASS} {{ display: none; }} }}
</style>
"""


def add_back_links(out: Path) -> int:
    """Give every staged report a link back to the landing page.

    The reports are frozen snapshots (never edited in place), and older versions
    were built before the index existed, so the link is added to the staged copy
    rather than to the file in git. Wording matches extras/transcript-compare.html.
    """
    done, skipped = 0, []
    for p in sorted((out / "reports").rglob("*.html")):
        html = p.read_text()
        if BACK_LINK_CLASS in html:
            continue
        m = re.search(r"<main\b[^>]*>", html)
        if not m or "</head>" not in html:
            skipped.append(str(p.relative_to(out)))
            continue
        up = "../" * (len(p.relative_to(out).parts) - 1)
        link = (f'\n<a class="{BACK_LINK_CLASS}" href="{up}index.html">'
                f'<span aria-hidden="true">&larr;</span> Back to reports</a>')
        html = html.replace("</head>", BACK_LINK_CSS + "</head>", 1)
        # re-find: the head insertion shifted every offset after it
        m = re.search(r"<main\b[^>]*>", html)
        p.write_text(html[:m.end()] + link + html[m.end():])
        done += 1
    for s in skipped:
        annotate("warning", f"{s}: no <main> or no </head>, left without a back link")
    log(f"• back-to-index link added to {done} report page(s)"
        + (f", {len(skipped)} skipped" if skipped else ""))
    return len(skipped)


def local_hrefs(html: str) -> list[str]:
    out = []
    for raw in re.findall(r'(?:href|src)="([^"]+)"', html):
        u = urlparse(raw)
        if u.scheme or raw.startswith(("#", "//", "data:", "mailto:")):
            continue
        out.append(unquote(u.path))
    return out


def check_links(out: Path) -> list[str]:
    html = (out / "index.html").read_text()
    hrefs = local_hrefs(html)
    broken = [h for h in hrefs if not (out / h).exists()]
    log(f"• {len(hrefs)} local links, {len(broken)} broken")
    return broken


def check_reports_surfaced(out: Path) -> list[str]:
    html = (out / "index.html").read_text()
    orphans = []
    for d in sorted((out / "reports").iterdir()):
        if d.is_dir() and any(d.glob("report_v*.html")) and f"reports/{d.name}/" not in html:
            orphans.append(d.name)
    return orphans


def check_markdown_copies(out: Path) -> list[str]:
    """Latest report_vN.html with no report_vN.md beside it.

    The markdown copy is how an agent reads a report; a new version published
    without one silently leaves them on a stale copy.
    """
    missing = []
    for d in sorted((out / "reports").iterdir()):
        if not d.is_dir():
            continue
        vs = sorted((int(m.group(1)), p) for p in d.glob("report_v*.html")
                    if (m := re.fullmatch(r"report_v(\d+)\.html", p.name)))
        if vs and not vs[-1][1].with_suffix(".md").exists():
            missing.append(f"{d.name}/{vs[-1][1].name}")
    return missing


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ROOT / "_site",
                   help="directory to stage the site into (default: _site)")
    p.add_argument("--skip-generate", action="store_true",
                   help="stage the committed index.html without regenerating it")
    p.add_argument("--strict", action="store_true",
                   help="also fail on the warnings: a report not linked from the index, "
                        "or one that could not take a back link")
    args = p.parse_args()

    if not args.skip_generate:
        generate_index()
    stage(args.out)
    no_back_link = add_back_links(args.out)

    broken = check_links(args.out)
    for b in broken:
        annotate("error", f"index.html links to {b}, which is not in the site")

    orphans = check_reports_surfaced(args.out)
    for o in orphans:
        annotate("warning",
                 f"reports/{o} has published versions but is not linked from the index "
                 f"— add it to CARDS or ALSO in generate_index.py")

    no_md = check_markdown_copies(args.out)
    for m in no_md:
        annotate("warning",
                 f"reports/{m} has no markdown copy beside it — regenerate the prose with "
                 f"`python3 tools/samples.py text <report> --engine rendered` and write "
                 f"report_vN.md (see tools/README.md)")

    if broken:
        log(f"\nFAILED: {len(broken)} broken link(s)")
        return 1
    if args.strict and (orphans or no_back_link):
        log(f"\nFAILED (--strict): {len(orphans)} unreferenced report(s), "
            f"{no_back_link} without a back link")
        return 1
    log(f"\nOK — site staged at {args.out}"
        + (f" ({len(orphans)} unreferenced report(s), see warnings)" if orphans else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
