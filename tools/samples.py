#!/usr/bin/env python3
"""Read the published reports the way the human sample explorers do.

Every report embeds its full corpus in the HTML (that is what the on-page
explorer filters over). This exposes the same corpus to a terminal: the same
filter dimensions, the same draw-N-random, plus full-text search and whole
transcripts.

    python3 tools/samples.py reports
    python3 tools/samples.py schema ablations
    python3 tools/samples.py draw ablations -n 3 --where outcome='cheated*' --seed 1
    python3 tools/samples.py show ablations --id p2-believe-astra:e4 --out /tmp/t.md

Stdlib only, except `text --engine rendered`, which needs playwright.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import gzip
import json
import random
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"

# Short names for the command line. A report directory is also accepted, so a
# report added later works without touching this map.
ALIASES = {
    "ablations": "Beat-stockfish-reproduction-and-prompt-ablations",
    "debrief": "Debrief-probe-self-reported-reward-hacking",
    "denial": "Debrief-denial---resampling-and-reasoning-ablations",
    "poll": "Poll-predictions-vs-results",
    "stopeval": "Fable-5-on-the-stop_eval-condition",
    "motivated": "Motivated-reasoning-in-Fable-s-cheating-decisions",
    "repro": "Beat-stockfish-reproduction-results",
}

# Fields whose value is a wall of text: never printed in a list row, and used as
# the default haystack for `search`.
TEXT_FIELDS = ("transcript", "response_text", "reasoning", "judge_summary",
               "judge_evidence", "quotes", "env_feedback", "freeform_note",
               "disclosure_wrapup_evidence", "findings")


# --------------------------------------------------------------------------
# loading


def resolve_dir(name: str) -> Path:
    if name in ALIASES:
        return REPORTS_DIR / ALIASES[name]
    p = REPORTS_DIR / name
    if p.is_dir():
        return p
    p = Path(name)
    if p.is_dir():
        return p
    matches = [d for d in REPORTS_DIR.iterdir()
               if d.is_dir() and name.lower() in d.name.lower()]
    if len(matches) == 1:
        return matches[0]
    known = ", ".join(sorted(ALIASES))
    raise SystemExit(f"unknown report {name!r}; try one of: {known}")


def versions(d: Path) -> list[tuple[int, Path]]:
    out = []
    for p in d.glob("report_v*.html"):
        m = re.match(r"report_v(\d+)\.html$", p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def report_path(d: Path, version: int | None) -> Path:
    vs = versions(d)
    if not vs:
        raise SystemExit(f"no report_v*.html in {d}")
    if version is None:
        return vs[-1][1]
    for n, p in vs:
        if n == version:
            return p
    raise SystemExit(f"{d.name} has no v{version} (have {[n for n, _ in vs]})")


PAYLOAD_PATTERNS = (
    r'<script[^>]*id="data"[^>]*>(.*?)</script>',
    r'<script[^>]*id="data-b64"[^>]*>(.*?)</script>',
)


def extract_payload(html: str) -> dict:
    """The `#data` / `#data-b64` blob: raw JSON, or base64 of (gzip of) JSON."""
    for pat in PAYLOAD_PATTERNS:
        m = re.search(pat, html, re.S)
        if not m:
            continue
        raw = m.group(1).strip()
        if raw[:1] in "{[":
            return json.loads(raw)
        blob = base64.b64decode(raw)
        if blob[:2] == b"\x1f\x8b":
            blob = gzip.decompress(blob)
        return json.loads(blob)
    raise SystemExit("no embedded payload found (looked for #data and #data-b64)")


def report_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return unescape(m.group(1).strip()) if m else ""


class Report:
    def __init__(self, name: str, version: int | None = None):
        self.dir = resolve_dir(name)
        self.path = report_path(self.dir, version)
        self.version = int(re.search(r"v(\d+)", self.path.name).group(1))
        self.html = self.path.read_text()
        self.data = extract_payload(self.html)
        self.title = report_title(self.html)
        self.samples: list[dict] = self.data.get("samples", [])

    # -- ids -------------------------------------------------------------
    def sample_id(self, row: dict) -> str:
        if "id" in row:
            return str(row["id"])
        if "run" in row and "epoch" in row:
            return f"{row['run']}:e{row['epoch']}"
        return str(row.get("epoch", ""))

    def find(self, ident: str) -> tuple[int, dict]:
        for i, r in enumerate(self.samples):
            if self.sample_id(r) == ident:
                return i, r
        hits = [(i, r) for i, r in enumerate(self.samples)
                if ident.lower() in self.sample_id(r).lower()]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise SystemExit(f"no sample with id {ident!r} "
                             f"(try `list` to see ids)")
        ids = ", ".join(self.sample_id(r) for _, r in hits[:8])
        raise SystemExit(f"{ident!r} matches {len(hits)} samples: {ids} ...")

    # -- dims ------------------------------------------------------------
    def explorer_dims(self) -> list[dict]:
        """The dimensions the report's own explorer offers, parsed out of it.

        Reading them off the report keeps this in step with the page instead of
        duplicating the config; `field_values` covers anything the parse misses.
        """
        m = re.search(r"KitExplorer\.explorer\(.*?dims:\s*\[(.*?)\]\s*,\s*\n?\s*(?:search|render|pageSize)",
                      self.html, re.S)
        if not m:
            return []
        dims = []
        for d in re.finditer(r"\{([^{}]*)\}", m.group(1)):
            body = d.group(1)
            key = re.search(r"key:\s*\"([^\"]+)\"", body)
            if not key:
                continue
            label = re.search(r"label:\s*\"([^\"]+)\"", body)
            dims.append({
                "key": key.group(1),
                "label": label.group(1) if label else key.group(1),
                "multi": "multi: true" in body,
                "advanced": "advanced: true" in body,
            })
        return dims

    def field_values(self, key: str) -> dict:
        counts: dict = {}
        for r in self.samples:
            v = r.get(key)
            for item in (v if isinstance(v, list) else [v]):
                if isinstance(item, (dict, list)):
                    continue
                counts[item] = counts.get(item, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))))

    def scalar_fields(self) -> list[str]:
        keys = []
        for r in self.samples[:50]:
            for k, v in r.items():
                if k in keys or k in TEXT_FIELDS:
                    continue
                if isinstance(v, (str, int, float, bool, type(None))):
                    keys.append(k)
        return keys


# --------------------------------------------------------------------------
# filtering


def parse_where(clauses: list[str]) -> list[tuple[str, list[str]]]:
    out = []
    for c in clauses or []:
        if "=" not in c:
            raise SystemExit(f"--where wants key=value, got {c!r}")
        k, v = c.split("=", 1)
        # Comma separates alternatives, so a value that contains one (two of the
        # motivated-reasoning labels do) escapes it: 'honeypot considered\, dismissed'.
        vals = [x.strip().replace("\\,", ",") for x in re.split(r"(?<!\\),", v)]
        out.append((k.strip(), [x for x in vals if x]))
    return out


def matches(row: dict, where: list[tuple[str, list[str]]]) -> bool:
    """Every clause must hold; values inside one clause are OR'd.

    Patterns are case-insensitive fnmatch, so `outcome='cheated*'` catches both
    cheated segments and `condition=orig*` saves typing the full label.
    """
    for key, pats in where:
        have = row.get(key)
        have_list = have if isinstance(have, list) else [have]
        hay = [str(x).lower() for x in have_list]
        if not any(fnmatch.fnmatch(h, p.lower()) or h == p.lower()
                   for h in hay for p in pats):
            return False
    return True


def select(rep: Report, args) -> list[dict]:
    where = parse_where(getattr(args, "where", None))
    rows = [r for r in rep.samples if matches(r, where)]
    q = getattr(args, "contains", None)
    if q:
        scopes = (args.scope.split(",") if getattr(args, "scope", None)
                  else list(TEXT_FIELDS))
        rows = [r for r in rows if text_of(r, scopes).lower().find(q.lower()) >= 0]
    return rows


def text_of(row: dict, fields) -> str:
    parts = []
    for f in fields:
        v = row.get(f)
        if v is None:
            continue
        parts.append(flatten_text(v))
    return "\n".join(parts)


def flatten_text(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(flatten_text(x) for x in v)
    if isinstance(v, dict):
        if "role" in v:  # transcript message
            bits = [v.get("reasoning"), v.get("reasoning_summary"), v.get("text")]
            for c in v.get("tool_calls") or []:
                bits.append(f"{c.get('fn')}({json.dumps(c.get('args'))})")
            return "\n".join(b for b in bits if b)
        return "\n".join(flatten_text(x) for x in v.values())
    return "" if v is None else str(v)


# --------------------------------------------------------------------------
# rendering


def row_line(rep: Report, row: dict) -> str:
    """One-line card, the fields the on-page card puts in its meta row."""
    ident = rep.sample_id(row)
    bits = []
    for k in ("model", "condition", "group", "setup", "phase", "outcome",
              "verdict_label", "judge_class", "disclosure", "in_episode_disclosure",
              "reward_hacked", "game_result", "n_messages"):
        v = row.get(k)
        if v is None or v == "" or v == "not_applicable":
            continue
        if k == "n_messages":
            bits.append(f"{v} msgs")
        elif isinstance(v, bool):
            bits.append(f"{k}={str(v).lower()}")
        else:
            bits.append(str(v))
    seen, uniq = set(), []
    for b in bits:
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    return f"{ident:<34} {' · '.join(uniq)}"


def render_transcript(t, roles=None, msg_range=None) -> str:
    if isinstance(t, str):
        return t
    out = []
    for i, m in enumerate(t or []):
        if msg_range and not (msg_range[0] <= i < msg_range[1]):
            continue
        role = str(m.get("role", "?")).upper()
        if roles and role.lower() not in roles:
            continue
        out.append(f"--- [{i}] {role} ---")
        for key, label in (("reasoning", "reasoning"),
                           ("reasoning_summary", "reasoning summary"),
                           ("text", "text")):
            v = m.get(key)
            if v:
                out.append(f"[{label}]\n{v}")
        for c in m.get("tool_calls") or []:
            args = c.get("args")
            args_s = json.dumps(args, indent=2) if isinstance(args, dict) else str(args)
            out.append(f"[tool call] {c.get('fn')}\n{args_s}")
        out.append("")
    return "\n".join(out)


def render_sample(rep: Report, row: dict, args) -> str:
    out = [f"# {rep.sample_id(row)}", ""]
    for k, v in row.items():
        if k in TEXT_FIELDS or isinstance(v, (list, dict)):
            continue
        out.append(f"- **{k}**: {v}")
    for k in ("findings", "judge_evidence", "env_feedback"):
        v = row.get(k)
        if v:
            out.append(f"\n## {k}")
            out += [f"- {x}" for x in (v if isinstance(v, list) else [v])]
    for k in ("judge_summary", "freeform_note", "disclosure_wrapup_evidence"):
        v = row.get(k)
        if v:
            out += [f"\n## {k}", str(v)]
    if row.get("quotes"):
        out.append("\n## quotes")
        for q in row["quotes"]:
            if isinstance(q, dict):
                src = q.get("source", "")
                idx = q.get("message_index", "")
                out.append(f"- [{src} @ msg {idx}] {q.get('quote', '')}")
            else:
                out.append(f"- {q}")
    for k in ("reasoning", "response_text"):
        v = row.get(k)
        if v:
            out += [f"\n## {k}", str(v)]
    if row.get("transcript") is not None and not args.no_transcript:
        n = row["transcript"]
        n = len(n) if isinstance(n, list) else "1 blob"
        out += [f"\n## transcript ({n} messages)", ""]
        roles = args.roles.lower().split(",") if args.roles else None
        rng = None
        if args.messages:
            a, _, b = args.messages.partition(":")
            rng = (int(a or 0), int(b) if b else 10 ** 9)
        out.append(render_transcript(row["transcript"], roles, rng))
    return "\n".join(out)


def emit(text: str, out_path: str | None):
    if out_path:
        Path(out_path).write_text(text)
        print(f"wrote {out_path} ({len(text)} chars)")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def as_json(rows, args) -> str:
    if args.format == "jsonl":
        return "\n".join(json.dumps(r) for r in rows)
    return json.dumps(rows, indent=2)


def slim(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in TEXT_FIELDS} for r in rows]


# --------------------------------------------------------------------------
# prose extraction


def unescape(s: str) -> str:
    import html as _html
    return _html.unescape(s)


class Prose(HTMLParser):
    """Body markup -> markdown.

    Two things it has to get right: inline tags (`<code>`, `<strong>`) must not
    break a sentence into fragments, and an element that is empty because the
    page script fills it becomes a `{{id}}` placeholder rather than a hole.
    """

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "source", "track", "wbr"}
    SKIP = {"script", "style", "aside", "svg", "select", "button", "template"}
    BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "figcaption", "tr", "div",
             "blockquote", "summary", "figure", "section", "td", "th",
             "table", "ul", "ol", "details", "main"}
    WRAP = {"code": "`", "strong": "**", "b": "**", "em": "*", "i": "*"}
    # Elements whose emptiness means "the page script fills this in"; headings
    # and anchors carry ids for navigation, which is not the same thing.
    SLOTTABLE = {"span", "strong", "em", "code", "b", "i", "div", "ul", "ol",
                 "p", "tbody", "nav"}
    HEADINGS = {"h1": "\n# ", "h2": "\n## ", "h3": "\n### ", "h4": "\n#### ",
                "li": "- ", "figcaption": "*Figure: ", "summary": "> "}

    def __init__(self, keep_svg_text=False, skip_ids=()):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.buf: list[str] = []
        self.depth = 0
        self.skip_from: int | None = None
        self.svg_depth = 0
        self.in_body = False
        self.keep_svg_text = keep_svg_text
        self.skip_ids = set(skip_ids)
        self.row_cells: list[str] = []
        self.slots: list[tuple[str, str, int, int]] = []

    # -- helpers ---------------------------------------------------------
    def flush(self, prefix="", suffix=""):
        s = " ".join("".join(self.buf).split())
        self.buf = []
        s = re.sub(r"`\s*`", "", s)
        s = re.sub(r"\*\*\s*\*\*", "", s)
        s = re.sub(r"\s+([,.;:)])", r"\1", s)
        if s.strip():
            self.out.append(prefix + s + suffix)

    @property
    def skipping(self) -> bool:
        return self.skip_from is not None

    # -- parser callbacks ------------------------------------------------
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
            self.depth = 0
            return
        if tag not in self.VOID:
            self.depth += 1
        if self.skipping or not self.in_body:
            return
        ident = dict(attrs).get("id")
        keep_svg = self.keep_svg_text and (tag == "svg" or self.svg_depth)
        if (tag in self.SKIP and not keep_svg) or (ident in self.skip_ids):
            # Flushing here would strip a heading of its prefix: the kit puts an
            # inline <button> (the copy-link affordance) inside every heading,
            # and the prefix is only known at the heading's end tag.
            if tag in self.BLOCK or tag == "aside":
                self.flush()
            self.skip_from = self.depth
            return
        if tag == "svg" or self.svg_depth:
            self.svg_depth += 1
            return
        if tag in self.WRAP:
            self.buf.append(self.WRAP[tag])
        elif tag in self.BLOCK:
            self.flush()
        elif tag == "br":
            self.buf.append(" ")
        if ident and tag in self.SLOTTABLE:
            self.slots.append((tag, ident, len(self.buf), len(self.out)))

    def handle_endtag(self, tag):
        if self.skipping:
            if self.depth == self.skip_from:
                self.skip_from = None
            if tag not in self.VOID:
                self.depth -= 1
            return
        if tag not in self.VOID:
            self.depth -= 1
        if not self.in_body:
            return
        if self.svg_depth:
            self.svg_depth -= 1
            if self.svg_depth == 0:
                self.flush()
            return
        if self.slots and self.slots[-1][0] == tag:
            _, ident, mark, out_mark = self.slots.pop()
            if len(self.buf) == mark and len(self.out) == out_mark:
                self.buf.append(f"{{{{{ident}}}}}")
        if tag in self.WRAP:
            self.buf.append(self.WRAP[tag])
            return
        if tag in ("td", "th"):
            self.row_cells.append(" ".join("".join(self.buf).split()))
            self.buf = []
            return
        if tag == "tr":
            if any(self.row_cells):
                self.out.append("| " + " | ".join(self.row_cells) + " |")
            self.row_cells = []
            return
        if tag not in self.BLOCK:
            return
        self.flush(self.HEADINGS.get(tag, ""), "*" if tag == "figcaption" else "")
        if tag in ("p", "figure", "h1", "h2", "h3", "table", "section", "ul", "details"):
            self.out.append("")

    def handle_data(self, data):
        if self.skipping or not self.in_body:
            return
        # SVG text nodes are separate labels, not a sentence: keep them apart.
        self.buf.append(f" {data} " if self.svg_depth else data)

    def text(self) -> str:
        self.flush()
        lines, prev_blank = [], False
        for ln in self.out:
            blank = not ln.strip()
            if blank and prev_blank:
                continue
            lines.append(ln)
            prev_blank = blank
        return "\n".join(lines).strip() + "\n"


def prose(html: str, keep_svg_text=False, skip_ids=("explorer",)) -> str:
    p = Prose(keep_svg_text=keep_svg_text, skip_ids=skip_ids)
    p.feed(html)
    return p.text()


def rendered_html(path: Path, wait_ms: int = 1200) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("--engine rendered needs playwright: "
                         "uv run --with playwright python tools/samples.py ...")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 1000})
        pg.goto(path.resolve().as_uri(), wait_until="load")
        pg.wait_for_timeout(wait_ms)
        # every <details> holds content that is only rendered once opened
        pg.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        pg.wait_for_timeout(wait_ms)
        html = pg.content()
        b.close()
    return html


# --------------------------------------------------------------------------
# commands


def cmd_reports(args):
    rows = []
    for d in sorted(REPORTS_DIR.iterdir()):
        if not d.is_dir() or not versions(d):
            continue
        alias = next((a for a, n in ALIASES.items() if n == d.name), "")
        vs = [n for n, _ in versions(d)]
        rep = Report(d.name)
        rows.append((alias, d.name, f"v{rep.version}", f"{len(rep.samples)} samples",
                     f"(v{vs[0]}..v{vs[-1]})", rep.title))
    w = max(len(r[0]) for r in rows)
    for alias, name, v, n, span, title in rows:
        print(f"{alias:<{w}}  {v:<5} {n:<13} {span:<12} {name}")
        print(f"{'':<{w}}  {title}")


def cmd_schema(args):
    rep = Report(args.report, args.version)
    print(f"{rep.title}\n{rep.path.relative_to(ROOT)} · {len(rep.samples)} samples\n")
    dims = rep.explorer_dims()
    if dims:
        print("Filter dimensions the on-page explorer offers "
              "(--where key=value, comma = OR, fnmatch patterns ok):")
        for d in dims:
            vals = rep.field_values(d["key"])
            shown = ", ".join(f"{k} ({v})" for k, v in list(vals.items())[:12])
            tag = " [advanced]" if d["advanced"] else ""
            print(f"  {d['key']:<22} {d['label']}{tag}\n      {shown}")
        print()
    print("Other filterable fields:")
    dim_keys = {d["key"] for d in dims}
    for k in rep.scalar_fields():
        if k in dim_keys:
            continue
        vals = rep.field_values(k)
        shown = ", ".join(str(x) for x in list(vals)[:6])
        more = " ..." if len(vals) > 6 else ""
        print(f"  {k:<22} {len(vals)} values: {shown}{more}")
    present = [f for f in TEXT_FIELDS if any(f in r for r in rep.samples[:5])]
    print(f"\nText fields (search --scope, default = all of them):\n  {', '.join(present)}")
    print(f"\nOther payload keys (see `stats`): "
          f"{', '.join(k for k in rep.data if k != 'samples')}")


def cmd_stats(args):
    rep = Report(args.report, args.version)
    blob = {k: v for k, v in rep.data.items() if k != "samples"}
    if args.format in ("json", "jsonl"):
        emit(json.dumps(blob, indent=2), args.out)
        return
    lines = [f"# {rep.title} — precomputed aggregates ({rep.path.name})", ""]
    lines.append("Rates carry their CI as est/lo/hi; these are the numbers the "
                 "report's figures draw.\n")
    lines.append(json.dumps(blob, indent=2))
    emit("\n".join(lines), args.out)


def cmd_list(args):
    rep = Report(args.report, args.version)
    rows = select(rep, args)
    if args.count:
        print(len(rows))
        return
    rows_out = rows[: args.limit] if args.limit else rows
    if args.format in ("json", "jsonl"):
        emit(as_json(rows_out if args.full else slim(rows_out), args), args.out)
        return
    body = [row_line(rep, r) for r in rows_out]
    head = f"{len(rows)} match" + (f", showing {len(rows_out)}" if len(rows_out) < len(rows) else "")
    emit("\n".join([head, ""] + body), args.out)


def cmd_draw(args):
    rep = Report(args.report, args.version)
    rows = select(rep, args)
    if not rows:
        raise SystemExit("no samples match those filters")
    rng = random.Random(args.seed)
    picked = rng.sample(rows, min(args.n, len(rows)))
    if args.format in ("json", "jsonl"):
        emit(as_json(picked if args.full else slim(picked), args), args.out)
        return
    head = (f"drew {len(picked)} of {len(rows)} matching samples"
            + (f" (seed {args.seed})" if args.seed is not None else " (unseeded)"))
    if args.transcripts:
        body = [render_sample(rep, r, args) for r in picked]
        emit("\n\n---\n\n".join([head] + body), args.out)
    else:
        emit("\n".join([head, ""] + [row_line(rep, r) for r in picked]), args.out)


def cmd_show(args):
    rep = Report(args.report, args.version)
    if args.index is not None:
        row = rep.samples[args.index]
    else:
        _, row = rep.find(args.id)
    emit(render_sample(rep, row, args), args.out)


def cmd_search(args):
    rep = Report(args.report, args.version)
    rows = select(rep, args)
    scopes = args.scope.split(",") if args.scope else list(TEXT_FIELDS)
    flags = 0 if args.case_sensitive else re.IGNORECASE
    pat = re.compile(args.query if args.regex else re.escape(args.query), flags)
    hits, n_rows = [], 0
    for r in rows:
        row_hits = []
        for f in scopes:
            if f not in r:
                continue
            hay = flatten_text(r[f])
            for m in pat.finditer(hay):
                a = max(0, m.start() - args.context)
                b = min(len(hay), m.end() + args.context)
                snippet = " ".join(hay[a:b].split())
                row_hits.append(f"    [{f}] …{snippet}…")
                if len(row_hits) >= args.per_sample:
                    break
            if len(row_hits) >= args.per_sample:
                break
        if row_hits:
            n_rows += 1
            hits.append(row_line(rep, r))
            hits += row_hits
    head = f"{n_rows}/{len(rows)} samples match {args.query!r}"
    if args.count:
        print(head)
        return
    emit("\n".join([head, ""] + hits), args.out)


def cmd_text(args):
    rep = Report(args.report, args.version)
    engine = args.engine
    if engine == "auto":
        try:
            import playwright  # noqa: F401
            engine = "rendered"
        except ImportError:
            engine = "static"
    html = rendered_html(rep.path) if engine == "rendered" else rep.html
    skip = tuple(x for x in (args.skip_ids or "").split(",") if x)
    body = prose(html, keep_svg_text=(engine == "rendered" and not args.no_svg),
                 skip_ids=skip)
    header = (f"<!-- {rep.dir.name} {rep.path.name} · prose extracted "
              f"({engine}) by tools/samples.py -->\n")
    emit(header + body, args.out)


def cmd_code(args):
    """The report's own inline script — figure definitions, explorer config.

    The kit is inlined ahead of it in the same <script>, so the cut is after the
    last kit module's closing IIFE.
    """
    rep = Report(args.report, args.version)
    blocks = re.findall(r"<script>(.*?)</script>", rep.html, re.S)
    if not blocks:
        raise SystemExit("no inline <script> found")
    block = blocks[-1]
    banners = [m.start() for m in re.finditer(r"/\* clab report kit — ", block)]
    if banners:
        end = re.search(r"\n\}\)\(\);\n", block[banners[-1]:])
        if end:
            block = block[banners[-1] + end.end():]
    emit(block.strip(), args.out)


# --------------------------------------------------------------------------


def add_filters(p, with_search=True):
    p.add_argument("report", help="alias or reports/ directory name")
    p.add_argument("--version", type=int, help="report version (default: latest)")
    p.add_argument("--where", action="append", metavar="KEY=VAL[,VAL]",
                   help="filter; repeatable (AND), comma-separated values OR, "
                        "fnmatch patterns allowed")
    if with_search:
        p.add_argument("--contains", metavar="TEXT",
                       help="keep samples whose text contains TEXT")
        p.add_argument("--scope", metavar="FIELD[,FIELD]",
                       help="text fields --contains/search looks at")
    p.add_argument("--format", choices=("text", "json", "jsonl"), default="text")
    p.add_argument("--out", metavar="FILE", help="write to FILE instead of stdout")


def add_transcript_opts(p):
    p.add_argument("--no-transcript", action="store_true")
    p.add_argument("--roles", metavar="user,assistant", help="only these roles")
    p.add_argument("--messages", metavar="A:B", help="message index slice")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 2)[2])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("reports", help="list reports, versions, sample counts")

    p = sub.add_parser("schema", help="filter dimensions and their values")
    p.add_argument("report")
    p.add_argument("--version", type=int)

    p = sub.add_parser("stats", help="the report's precomputed aggregates")
    add_filters(p, with_search=False)

    p = sub.add_parser("list", help="one line per matching sample")
    add_filters(p)
    p.add_argument("--limit", type=int, default=0, help="0 = no limit")
    p.add_argument("--count", action="store_true")
    p.add_argument("--full", action="store_true",
                   help="json output keeps transcripts and other text fields")

    p = sub.add_parser("draw", help="draw N random matching samples")
    add_filters(p)
    add_transcript_opts(p)
    p.add_argument("-n", type=int, default=3)
    p.add_argument("--seed", type=int, help="omit for a different draw each run")
    p.add_argument("--transcripts", action="store_true",
                   help="print each drawn sample in full")
    p.add_argument("--full", action="store_true")

    p = sub.add_parser("show", help="one sample in full, transcript included")
    p.add_argument("report")
    p.add_argument("--version", type=int)
    p.add_argument("--id", help="sample id, e.g. p2-believe-astra:e4")
    p.add_argument("--index", type=int, help="position in samples[] instead")
    p.add_argument("--out", metavar="FILE")
    p.add_argument("--format", choices=("text",), default="text")
    add_transcript_opts(p)

    p = sub.add_parser("search", help="full-text search with context")
    add_filters(p)
    p.add_argument("-q", "--query", required=True)
    p.add_argument("--regex", action="store_true")
    p.add_argument("--case-sensitive", action="store_true")
    p.add_argument("--context", type=int, default=140)
    p.add_argument("--per-sample", type=int, default=3)
    p.add_argument("--count", action="store_true")

    p = sub.add_parser("text", help="the report's prose as markdown")
    p.add_argument("report")
    p.add_argument("--version", type=int)
    p.add_argument("--engine", choices=("auto", "static", "rendered"), default="auto",
                   help="rendered runs the page so JS-filled numbers and SVG "
                        "chart labels are in the output (needs playwright)")
    p.add_argument("--no-svg", action="store_true", help="drop chart text")
    p.add_argument("--skip-ids", default="explorer", metavar="ID[,ID]",
                   help="drop these subtrees; the explorer is dropped by "
                        "default since `draw` covers it")
    p.add_argument("--out", metavar="FILE")

    p = sub.add_parser("code", help="the report's own inline script")
    p.add_argument("report")
    p.add_argument("--version", type=int)
    p.add_argument("--out", metavar="FILE")

    args = ap.parse_args(argv)
    fn = {"reports": cmd_reports, "schema": cmd_schema, "stats": cmd_stats,
          "list": cmd_list, "draw": cmd_draw, "show": cmd_show,
          "search": cmd_search, "text": cmd_text, "code": cmd_code}[args.cmd]
    if args.cmd == "show" and args.id is None and args.index is None:
        raise SystemExit("show needs --id or --index")
    fn(args)


if __name__ == "__main__":
    main()
