# tools/ — reading the reports without a browser

`samples.py` opens a published report the way the on-page sample explorer does.
Every report embeds its **full corpus** (that is what the explorer filters over);
this reads the same blob from the terminal, with the same filter dimensions.

No dependencies — plain `python3`. The one exception is `text --engine rendered`,
which needs playwright (`uv run --with playwright python tools/samples.py ...`).

Alongside it, each report directory carries a **`report_vN.md`**: the same report
as markdown, written from the rendered page. Read that first; come here when you
want the underlying rollouts.

## Which report

```
$ python3 tools/samples.py reports
ablations  v14   360 samples   Beat-stockfish reproduction and prompt ablations
repro      v2     20 samples   Beat-stockfish reproduction — baseline results
debrief    v8    108 samples   Debrief probe — self-reported reward-hacking & concealment
stopeval   v5     90 samples   Fable 5 on the stop_eval condition
motivated  v2    108 samples   Motivated reasoning in the cheating decisions
```

The alias, the directory name, or any unambiguous fragment of it all work.
`--version N` reads an older snapshot; the default is the latest.

## What you can filter on

```
python3 tools/samples.py schema ablations
```

Prints the dimensions the report's own explorer offers — parsed out of the
report, so this stays in step with the page — every value with its count, the
other filterable fields, and which fields hold searchable text.

## Drawing samples

`--where key=value`, repeatable. Repeats are AND, commas inside one clause are
OR, and values are case-insensitive fnmatch patterns:

```bash
# what the explorer's "draw 4 random" button does, with a seed so it repeats
python3 tools/samples.py draw ablations -n 4 --seed 3 \
    --where 'outcome=cheated*' --where model='Fable 5.1'

# same draw, each sample printed in full with its transcript
python3 tools/samples.py draw motivated -n 2 --seed 3 --transcripts \
    --where verdict=rationalized_as_legitimate --out /tmp/draw.md

# everything matching, one line each
python3 tools/samples.py list stopeval --where 'group=Fable 5 *' --where outcome='cheated*'

# how many, without the rows
python3 tools/samples.py list ablations --where 'outcome=cheated*' --count
```

Omit `--seed` for a different draw every run. `--format json|jsonl` gives you
rows without the text fields; add `--full` to keep transcripts in the JSON.

A value that itself contains a comma escapes it:
`--where 'type_labels=honeypot considered\, dismissed'`.

## One sample in full

```bash
python3 tools/samples.py show ablations --id p2-baseline-fable51:e3 --out /tmp/t.md
python3 tools/samples.py show ablations --id p2-baseline-fable51:e3 --messages 30:44
python3 tools/samples.py show motivated --id abl-believe-astra:e1 --no-transcript
```

Ids are `run:eN` where the report has a run and an epoch, and the report's own
`id` field otherwise (`schema` lists them). Transcripts print in full — a long
one is 100k+ characters, so `--out FILE` and then grep is usually the move.
`--messages A:B` slices by message index, `--roles assistant` drops the tool
output.

## Searching the corpus

```bash
python3 tools/samples.py search ablations -q honeypot --scope transcript
python3 tools/samples.py search motivated -q "not prohibited" --scope quotes,judge_summary
python3 tools/samples.py search ablations -q 'engine\.sock' --regex --count
```

Prints each matching sample's card line plus up to `--per-sample` snippets with
`--context` characters either side. `--scope` defaults to every text field the
report has. Search composes with `--where`.

## The numbers behind the figures

```bash
python3 tools/samples.py stats debrief
```

The report's precomputed aggregates — the exact values its figures draw, rates
with their Wilson / bootstrap CIs as `est` / `lo` / `hi`. These are computed in
Python at build time, so they are the numbers to quote, not anything
recomputed from the rows.

## Reading the prose

```bash
python3 tools/samples.py text debrief --out /tmp/debrief.md          # static
uv run --with playwright python tools/samples.py text debrief \
    --engine rendered --out /tmp/debrief.md                          # JS-filled
```

Static extraction leaves `{{some-id}}` where the page fills a number from the
payload. The rendered engine runs the page first, so those are filled in and
chart axis labels come through as text; bar values do not (they are geometry),
which is what `stats` is for. `code` dumps the report's own inline script if you
need to see how a figure is defined.

The explorer subtree is dropped from `text` output — `draw` covers it.
