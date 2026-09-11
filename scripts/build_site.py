#!/usr/bin/env python3
"""Regenerate index.html and stage the GitHub Pages site into a directory.

The published site is only the landing page plus what it links to (kit/ for the
shared CSS/JS, reports/ for the report snapshots) — not data-release/, analysis/
or artifacts/, which are ~130 MB and are linked from the index as github.com
URLs rather than Pages paths.

Verification before deploy:
  - every local href in index.html must resolve to a staged file (a broken one
    is a 404 on the live site, and nothing else would catch it)
  - every reports/<dir> holding report_v*.html should be reachable from the
    index; an unreferenced one is a published report nobody can find, so it is
    reported as a warning rather than silently ignored

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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ROOT / "_site",
                   help="directory to stage the site into (default: _site)")
    p.add_argument("--skip-generate", action="store_true",
                   help="stage the committed index.html without regenerating it")
    p.add_argument("--strict", action="store_true",
                   help="also fail when a report directory is not linked from the index")
    args = p.parse_args()

    if not args.skip_generate:
        generate_index()
    stage(args.out)

    broken = check_links(args.out)
    for b in broken:
        annotate("error", f"index.html links to {b}, which is not in the site")

    orphans = check_reports_surfaced(args.out)
    for o in orphans:
        annotate("warning",
                 f"reports/{o} has published versions but is not linked from the index "
                 f"— add it to CARDS or ALSO in generate_index.py")

    if broken:
        log(f"\nFAILED: {len(broken)} broken link(s)")
        return 1
    if orphans and args.strict:
        log(f"\nFAILED (--strict): {len(orphans)} unreferenced report(s)")
        return 1
    log(f"\nOK — site staged at {args.out}"
        + (f" ({len(orphans)} unreferenced report(s), see warnings)" if orphans else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
