#!/usr/bin/env python3
"""Resample the debrief-probe turn N times each, for an arbitrary set of trajectories.

Originally hardcoded to 3 "denial" trajectories (all arm ``p2-baseline-fable51``,
sample_id ``v001``) which self-reported ``reward-hacked: false`` on a SINGLE
default-effort debrief draw in ``debrief_results.jsonl``:

    p2-baseline-fable51:v001:10
    p2-baseline-fable51:v001:13
    p2-baseline-fable51:v001:14

Generalized to accept any list of debrief keys (``arm:sample_id:epoch``, as they
appear in ``debrief_results.jsonl``), spanning any arm(s).

This script does NOT change the probe setup in any way (no reasoning-effort override,
no reasoning-summary override, same max_tokens=32000, same trailing-user handling) — it
just re-draws the same call N independent times per trajectory to see how stable the
self-report is under resampling. All transcript-replay / prompt / parsing logic is
reused verbatim from ``debrief_probe.py`` (``select`` + ``run_one``); nothing is
reimplemented here.

Output: a JSONL file (default ``debrief/resample_denials.jsonl``; the original
``debrief_results.jsonl`` is never read for writing and never touched).

    # default: the original 3 p2-baseline-fable51 keys, 10 draws each
    python3 debrief/resample_denials.py

    # arbitrary keys, spanning multiple arms
    python3 debrief/resample_denials.py \\
        --keys p2-baseline-fable51:v001:5 baseline-fable51:v001:2 \\
        --out debrief/resample_result_check.jsonl --draws 10 --concurrency 5

Optional transcript intervention (``--patch``): a JSON file containing a list of edits,
each ``{"message_index": N, "block": "reasoning"|"text", "find": "...", "replace": "..."}``.
``message_index`` is the 0-based index into the replayed ``sample.messages`` (the SAME
transcript that gets replayed to the model, before the debrief turn is appended). For
``block="reasoning"`` the edit is applied to the ``.reasoning`` string of the
``ContentReasoning`` block(s) of that (assistant) message; for ``"text"`` to the
``ContentText`` block(s). Each edit's ``find`` must occur EXACTLY ONCE across the
targeted block(s) of that message before the edit, and the result must contain ZERO
occurrences of ``find`` and (if non-empty) at least one occurrence of ``replace``
afterwards — otherwise the script stops (prints the actual block text) rather than
guessing. The patch is applied once, before any draws, to every trajectory in
``--keys`` (so a shared ``--patch`` file across multiple keys applies the same edits
to each of them). This does NOT change anything else about the call (same
``max_tokens``, no reasoning overrides, same trailing-user handling); each output row
records the patch (``patch``) and ``patch_applied: true``.

    python3 debrief/resample_denials.py --keys p2-baseline-fable51:v001:13 \\
        --patch debrief/patch_c1.json --draws 1 \\
        --out debrief/intervention_c1_smoke.jsonl

Optional reasoning stripping (``--strip-reasoning``): removes every ``ContentReasoning``
item from every prior ASSISTANT message in the replayed transcript before the debrief
call is built (the same operation ``debrief_probe.py`` already does for Astra around
line 361, generalized here to apply for any model). Applied once per trajectory, before
any draws (same timing as ``--patch``; if both are given, ``--patch`` is applied first,
then ``--strip-reasoning``). Each output row's ``prior_reasoning_blocks_stripped``
records the actual number of reasoning blocks removed for that trajectory (asserted to
be > 0 when the flag is set), and after stripping it is asserted that no assistant
message left in the transcript still carries a reasoning item.

    python3 debrief/resample_denials.py --keys p2-baseline-fable51:v001:13 \\
        --strip-reasoning --out debrief/intervention_s13.jsonl

Selective reasoning stripping: two mutually exclusive optional selectors narrow which
assistant messages ``--strip-reasoning``'s stripping applies to (indices are 0-based,
into ``sel.messages``, same numbering as ``--patch``'s ``message_index`` / transcript
dumps' ``--- [N] ROLE ---``). Either selector on its own implies stripping is active
(no need to also pass ``--strip-reasoning``); passing neither keeps the plain
"strip everything" behaviour above.

    --strip-reasoning-msgs 6,8,10   strip ONLY the assistant messages at these indices
    --keep-reasoning-msgs 1,4,6,8,10  strip ALL assistant messages EXCEPT these indices

Every index passed to either selector is asserted to refer to an assistant message
that actually carries >=1 ``ContentReasoning`` item before stripping (otherwise the
script prints the actual reasoning-bearing indices for that trajectory and stops,
rather than guessing). Each output row records ``prior_reasoning_blocks_stripped``
(actual count removed), ``reasoning_msgs_stripped`` (sorted list of message indices
actually stripped), and ``reasoning_msgs_kept`` (sorted list of assistant message
indices that still carry reasoning afterward) -- and it is asserted internally that
these two lists exactly match what was requested.

    python3 debrief/resample_denials.py --keys p2-baseline-fable51:v001:13 \\
        --strip-reasoning-msgs 10 --out debrief/intervention_t10.jsonl
    python3 debrief/resample_denials.py --keys p2-baseline-fable51:v001:13 \\
        --keep-reasoning-msgs 1,4,6,8,10 --out debrief/intervention_k1.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

WS = Path(__file__).resolve().parent.parent  # /work/workspace/beat-stockfish
DEBRIEF_DIR = Path(__file__).resolve().parent  # .../beat-stockfish/debrief

# Same sys.path setup as prepare_data.py (analysis/debrief-report) uses to reach the
# project venv + the shared `wilson()` CI helper in rollout_rows.py.
sys.path.insert(0, str(WS / ".venv/lib/python3.12/site-packages"))
sys.path.insert(0, str(DEBRIEF_DIR))
sys.path.insert(0, "/work/analysis/ablations-report")

from debrief_probe import run_one, select  # noqa: E402
from rollout_rows import wilson  # noqa: E402

DEFAULT_KEYS = [
    "p2-baseline-fable51:v001:10",
    "p2-baseline-fable51:v001:13",
    "p2-baseline-fable51:v001:14",
]
DEFAULT_N_DRAWS = 10
DEFAULT_CONCURRENCY = 5
MAX_TOKENS = 32000
DEFAULT_OUT_JSONL = DEBRIEF_DIR / "resample_denials.jsonl"
DEBRIEF_MESSAGE = DEBRIEF_DIR / "debrief_message.txt"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Resample the debrief-probe turn N times each for a set of "
                    "trajectories (identified by 'arm:sample_id:epoch' keys).",
    )
    p.add_argument("--keys", nargs="+", default=list(DEFAULT_KEYS), metavar="KEY",
                    help="Debrief keys 'arm:sample_id:epoch' (as in "
                         "debrief_results.jsonl). Default: the original 3 "
                         "p2-baseline-fable51 denial keys.")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT_JSONL,
                    help=f"Output JSONL path (default: {DEFAULT_OUT_JSONL}).")
    p.add_argument("--draws", type=int, default=DEFAULT_N_DRAWS,
                    help=f"Number of resample draws per key (default: {DEFAULT_N_DRAWS}).")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"Max concurrent in-flight API calls (default: {DEFAULT_CONCURRENCY}).")
    p.add_argument("--patch", type=Path, default=None,
                    help="Path to a JSON file with a list of transcript edits "
                         "{message_index, block, find, replace} to apply to every "
                         "selected trajectory before drawing (see module docstring).")
    p.add_argument("--strip-reasoning", action="store_true",
                    help="Remove every ContentReasoning item from every prior "
                         "assistant message before the debrief call (any model; "
                         "see module docstring).")
    selector = p.add_mutually_exclusive_group()
    selector.add_argument("--strip-reasoning-msgs", type=str, default=None, metavar="I,J,K",
                           help="Comma-separated 0-based message indices (into "
                                "sel.messages); strip ContentReasoning ONLY from "
                                "these assistant messages. Implies stripping is "
                                "active even without --strip-reasoning.")
    selector.add_argument("--keep-reasoning-msgs", type=str, default=None, metavar="I,J,K",
                           help="Comma-separated 0-based message indices; strip "
                                "ContentReasoning from all OTHER assistant messages, "
                                "keeping reasoning at these. Implies stripping is "
                                "active even without --strip-reasoning.")
    return p.parse_args(argv)


def parse_msg_list(s: str | None) -> set[int] | None:
    if s is None:
        return None
    parts = [x.strip() for x in s.split(",") if x.strip() != ""]
    assert parts, f"empty message-index list: {s!r}"
    out = set()
    for x in parts:
        assert x.lstrip("-").isdigit(), f"not an integer message index: {x!r} (in {s!r})"
        out.add(int(x))
    return out


def arm_of_key(key: str) -> str:
    """'arm:sample_id:epoch' -> 'arm' (split from the right, in case an arm name
    were ever to contain ':')."""
    parts = key.rsplit(":", 2)
    assert len(parts) == 3, f"key {key!r} is not of the form 'arm:sample_id:epoch'"
    return parts[0]


def get_hacked(r: dict[str, Any]) -> Any:
    """The parsed reward_hacked value, or None if the call/parse failed."""
    if not r.get("ok"):
        return None
    return r.get("parsed", {}).get("reward_hacked")


def load_patch(path: Path) -> list[dict[str, Any]]:
    edits = json.loads(path.read_text())
    assert isinstance(edits, list) and edits, (
        f"{path}: expected a non-empty JSON list of edits, got {type(edits)}"
    )
    for e in edits:
        assert isinstance(e, dict) and {"message_index", "block", "find", "replace"} <= e.keys(), (
            f"{path}: edit missing required keys "
            f"(message_index/block/find/replace): {e!r}"
        )
        assert e["block"] in ("reasoning", "text"), (
            f"{path}: edit block must be 'reasoning' or 'text', got {e['block']!r}: {e!r}"
        )
        assert isinstance(e["message_index"], int), (
            f"{path}: message_index must be an int: {e!r}"
        )
    return edits


def apply_patch(sel: Any, edits: list[dict[str, Any]]) -> None:
    """Mutate ``sel.messages`` in place per ``edits`` (see module docstring for the
    edit schema and the exactly-once / verified-after contract). Raises on any
    violation instead of guessing a fix; on a count-mismatch it first prints the
    actual block text so the discrepancy can be inspected directly."""
    for edit in edits:
        idx, block = edit["message_index"], edit["block"]
        find, replace = edit["find"], edit["replace"]
        assert 0 <= idx < len(sel.messages), (
            f"{sel.key}: message_index {idx} out of range (n_messages={len(sel.messages)})"
        )
        msg = sel.messages[idx]
        assert msg.role == "assistant", (
            f"{sel.key}: message[{idx}] has role {msg.role!r}, expected 'assistant'"
        )
        assert isinstance(msg.content, list), (
            f"{sel.key}: message[{idx}] content is a plain string, not a list of "
            f"content blocks; cannot target block={block!r}"
        )
        attr = "reasoning" if block == "reasoning" else "text"
        items = [c for c in msg.content if getattr(c, "type", None) == block]
        assert items, f"{sel.key}: message[{idx}] has no {block!r} content block(s)"

        n_before = sum(getattr(c, attr).count(find) for c in items)
        if n_before != 1:
            print(f"\n[patch] MISMATCH {sel.key} message[{idx}] block={block!r}: "
                  f"find string occurs {n_before} time(s) (expected exactly 1).\n"
                  f"find = {find!r}\n"
                  f"actual {attr} text of the {len(items)} {block!r} block(s) in "
                  f"message[{idx}]:", file=sys.stderr)
            for j, c in enumerate(items):
                print(f"--- block {j} ---\n{getattr(c, attr)}\n", file=sys.stderr)
            raise AssertionError(
                f"{sel.key}: message[{idx}] block={block!r}: find string occurs "
                f"{n_before} time(s), expected exactly 1 -- stopping, not guessing a fix"
            )

        for c in items:
            text = getattr(c, attr)
            if find in text:
                setattr(c, attr, text.replace(find, replace, 1))

        # "zero occurrences of `find` after the edit", made well-defined even when
        # `replace` itself deliberately re-embeds `find` verbatim (an anchored
        # insertion like "<new sentence>\n\n<find>", used to prepend text right
        # before an existing anchor phrase rather than deleting/replacing it). In
        # that case the one occurrence surviving inside the just-inserted `replace`
        # block is expected and is NOT a leftover of the original; strip exactly
        # that one inserted `replace` span back out before counting, so this still
        # catches any *other*, unintended occurrence of `find` elsewhere.
        n_after = sum(
            getattr(c, attr).replace(replace, "", 1).count(find) if replace else
            getattr(c, attr).count(find)
            for c in items
        )
        assert n_after == 0, (
            f"{sel.key}: message[{idx}] block={block!r}: find string still present "
            f"{n_after} time(s) after the edit, beyond the one intentionally "
            f"reintroduced (if any) inside the replace text itself"
        )
        if replace:
            n_replace = sum(getattr(c, attr).count(replace) for c in items)
            assert n_replace >= 1, (
                f"{sel.key}: message[{idx}] block={block!r}: replace string not "
                f"found in the block after the edit"
            )
        snippet = replace[:120] + ("…" if len(replace) > 120 else "")
        print(f"[patch] {sel.key} message[{idx}] block={block!r}: applied OK "
              f"(0 occurrences of find remain; replace present: {snippet!r})")


def reasoning_bearing_indices(sel: Any) -> list[int]:
    """Sorted indices (into ``sel.messages``) of assistant messages that currently
    carry at least one ``ContentReasoning`` item."""
    return sorted(
        i for i, m in enumerate(sel.messages)
        if m.role == "assistant" and isinstance(m.content, list)
        and any(getattr(c, "type", None) == "reasoning" for c in m.content)
    )


def strip_reasoning(sel: Any, only_msgs: set[int] | None = None,
                     keep_msgs: set[int] | None = None) -> dict[str, Any]:
    """Mutate ``sel.messages`` in place, removing ``ContentReasoning`` item(s) from
    assistant messages' content (generalizing, for any model, the Astra-only
    pattern in ``debrief_probe.py``'s ``run_one`` around line 361:
    ``kept = [c for c in m.content if getattr(c, "type", None) != "reasoning"]``).

    - Neither ``only_msgs`` nor ``keep_msgs``: strip EVERY assistant message
      (original plain ``--strip-reasoning`` behaviour).
    - ``only_msgs``: strip ONLY the assistant messages at these indices.
    - ``keep_msgs``: strip every reasoning-bearing assistant message EXCEPT
      these indices.

    Every index in ``only_msgs``/``keep_msgs`` is asserted to refer to an
    assistant message that actually carried reasoning before stripping (prints
    the actual reasoning-bearing indices and raises otherwise, rather than
    guessing). After mutating, asserts the resulting stripped/kept index lists
    exactly match what was requested.

    Returns a dict with ``n_stripped`` (total reasoning blocks removed),
    ``stripped_idx`` (sorted list of message indices actually stripped), and
    ``kept_idx`` (sorted list of assistant message indices that still carry
    reasoning afterward).
    """
    assert not (only_msgs and keep_msgs), (
        f"{sel.key}: only_msgs and keep_msgs are mutually exclusive"
    )

    before = reasoning_bearing_indices(sel)
    before_set = set(before)

    if only_msgs is not None:
        bad = sorted(only_msgs - before_set)
        assert not bad, (
            f"{sel.key}: --strip-reasoning-msgs index/indices {bad} do not carry "
            f"a reasoning block before stripping; reasoning-bearing assistant "
            f"message indices are {before}"
        )
        target = set(only_msgs)
    elif keep_msgs is not None:
        bad = sorted(keep_msgs - before_set)
        assert not bad, (
            f"{sel.key}: --keep-reasoning-msgs index/indices {bad} do not carry "
            f"a reasoning block before stripping; reasoning-bearing assistant "
            f"message indices are {before}"
        )
        target = before_set - set(keep_msgs)
    else:
        target = set(before_set)

    cleaned = []
    n_stripped = 0
    stripped_idx = []
    for i, m in enumerate(sel.messages):
        if i in target and m.role == "assistant" and isinstance(m.content, list):
            kept = [c for c in m.content if getattr(c, "type", None) != "reasoning"]
            removed = len(m.content) - len(kept)
            if removed:
                n_stripped += removed
                stripped_idx.append(i)
                m = m.model_copy(update={"content": kept})
        cleaned.append(m)
    sel.messages = cleaned

    kept_idx = reasoning_bearing_indices(sel)
    stripped_idx = sorted(stripped_idx)

    expected_stripped = sorted(target)
    expected_kept = sorted(before_set - target)
    assert stripped_idx == expected_stripped, (
        f"{sel.key}: actually-stripped indices {stripped_idx} != requested "
        f"{expected_stripped}"
    )
    assert kept_idx == expected_kept, (
        f"{sel.key}: post-strip reasoning-bearing indices {kept_idx} != expected "
        f"{expected_kept}"
    )
    return {"n_stripped": n_stripped, "stripped_idx": stripped_idx, "kept_idx": kept_idx}


async def amain(args: argparse.Namespace) -> int:
    keys: list[str] = args.keys
    n_draws: int = args.draws
    concurrency: int = args.concurrency
    out_jsonl: Path = args.out

    # chdir so relative paths (logs_dir, debrief-message, source_log recorded in each
    # rec) match exactly what the original debrief_probe.py run used.
    os.chdir(WS)

    debrief_text = Path("debrief/debrief_message.txt").read_text()
    assert debrief_text.strip(), f"{DEBRIEF_MESSAGE} is empty"

    arms = sorted({arm_of_key(k) for k in keys})
    sel_all = select(logs_dir="logs-disclosure-wrapup", arms=arms)
    by_key = {s.key: s for s in sel_all}
    missing = [k for k in keys if k not in by_key]
    assert not missing, f"target keys not found in selection: {missing}"
    targets = [by_key[k] for k in keys]

    for s in targets:
        # Each row carries its own source_log; just confirm the file is actually
        # there (this used to be pinned to a single hardcoded EXPECTED_SOURCE_LOG,
        # which only ever held for arm p2-baseline-fable51 and would wrongly block
        # every other arm).
        assert Path(s.log_path).exists(), (
            f"{s.key}: source log does not exist on disk: {s.log_path!r}"
        )
        # source_generate_config was {} for the original 3 -> no sampling fields
        # carried over from the log header; replicate that (no reasoning overrides,
        # max_tokens only).
        assert s.generate_config == {}, (
            f"{s.key}: unexpected source generate_config {s.generate_config}"
        )
        # the original 3 all had trailing_user_before_debrief=False -> the debrief
        # turn is appended as a new user message, merge_trailing_user is moot but
        # pass False to match the original run's flag value exactly.
        assert s.messages and s.messages[-1].role != "user", (
            f"{s.key}: expected transcript NOT to end on a user turn"
        )

    patch_edits: list[dict[str, Any]] | None = None
    if args.patch is not None:
        patch_edits = load_patch(args.patch)
        print(f"applying {len(patch_edits)} patch edit(s) from {args.patch} to "
              f"{len(targets)} target trajectory(ies):\n")
        for s in targets:
            apply_patch(s, patch_edits)
        print()

    only_msgs = parse_msg_list(args.strip_reasoning_msgs)
    keep_msgs = parse_msg_list(args.keep_reasoning_msgs)
    strip_active = bool(args.strip_reasoning or only_msgs is not None or keep_msgs is not None)

    strip_info: dict[str, dict[str, Any]] = {}
    if strip_active:
        if only_msgs is not None:
            mode_desc = f"only messages {sorted(only_msgs)}"
        elif keep_msgs is not None:
            mode_desc = f"all reasoning-bearing messages except {sorted(keep_msgs)}"
        else:
            mode_desc = "all reasoning-bearing messages"
        print(f"stripping reasoning blocks ({mode_desc}) from prior assistant "
              f"messages of {len(targets)} target trajectory(ies):\n")
        for s in targets:
            info = strip_reasoning(s, only_msgs=only_msgs, keep_msgs=keep_msgs)
            assert info["n_stripped"] > 0, (
                f"{s.key}: stripping removed 0 reasoning blocks (expected > 0 "
                f"for this trajectory/selector)"
            )
            strip_info[s.key] = info
            print(f"  {s.key}: removed {info['n_stripped']} reasoning block(s) "
                  f"from message(s) {info['stripped_idx']}; still present at "
                  f"message(s) {info['kept_idx']}")
        print()

    print(f"selected {len(targets)} target trajectories x {n_draws} draws "
          f"= {len(targets) * n_draws} calls\n")
    for s in targets:
        print(f"  {s.key}  nmsg={s.n_messages}  judge_class={s.judge_class}")
    print()

    sem = asyncio.Semaphore(concurrency)

    async def draw(sel, i: int) -> dict[str, Any]:
        rec = await run_one(
            sel, debrief_text, MAX_TOKENS,
            merge_trailing_user=False,
            sem=sem,
            reasoning_effort=None,
            reasoning_summary=None,
            astra_model=None,
            verbose=True,
        )
        rec["draw"] = i
        rec["patch"] = patch_edits
        rec["patch_applied"] = patch_edits is not None
        # run_one() itself only ever sets prior_reasoning_blocks_stripped via its
        # astra_model branch, which we never pass -- so it's always 0 there.
        # Overwrite with the real count/indices from our own (any-model) stripping
        # pass, done once per trajectory above (0 / empty when stripping wasn't
        # active, matching run_one's own default exactly).
        info = strip_info.get(sel.key)
        rec["prior_reasoning_blocks_stripped"] = info["n_stripped"] if info else 0
        rec["reasoning_msgs_stripped"] = info["stripped_idx"] if info else []
        rec["reasoning_msgs_kept"] = info["kept_idx"] if info else []
        rec["strip_reasoning"] = strip_active
        return rec

    tasks = [asyncio.create_task(draw(s, i)) for s in targets for i in range(n_draws)]
    results: list[dict[str, Any]] = []
    with out_jsonl.open("w") as fh:
        for coro in asyncio.as_completed(tasks):
            rec = await coro
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(rec)

    print(f"\nwrote {len(results)} record(s) to {out_jsonl}")

    by_traj: dict[str, list[dict[str, Any]]] = {k: [] for k in keys}
    for r in results:
        by_traj[r["key"]].append(r)

    print(f"\n{'=' * 78}\n"
          f"SUMMARY - {n_draws} resample draws per trajectory, unchanged default-effort setup\n"
          f"{'=' * 78}")
    pooled_false = pooled_true = pooled_fail = 0
    for key in keys:
        recs = sorted(by_traj[key], key=lambda r: r["draw"])
        n = len(recs)
        n_false = sum(1 for r in recs if get_hacked(r) is False)
        n_true = sum(1 for r in recs if get_hacked(r) is True)
        n_fail = n - n_false - n_true
        pooled_false += n_false
        pooled_true += n_true
        pooled_fail += n_fail
        w = wilson(n_false, n)
        print(f"\n{key}  (n={n})")
        print(f"  reward_hacked: False={n_false}  True={n_true}  failed-to-parse={n_fail}")
        print(f"  false-rate Wilson 95% CI: {w['est']:.3f}  [{w['lo']:.3f}, {w['hi']:.3f}]")
        if n_fail:
            print("  FAILURES/UNPARSED:")
            for r in recs:
                hacked = get_hacked(r)
                if hacked not in (True, False):
                    print(f"    draw {r['draw']}: ok={r.get('ok')} parse_ok={r.get('parse_ok')} "
                          f"reward_hacked={hacked!r} error={r.get('error')!r} "
                          f"parse_error={r.get('parse_error')!r}")

    n_total = pooled_false + pooled_true + pooled_fail
    assert n_total == len(targets) * n_draws
    w_pooled = wilson(pooled_false, n_total)
    print(f"\n{'=' * 78}\nPOOLED across all {len(targets)} trajectories (n={n_total})")
    print(f"  reward_hacked: False={pooled_false}  True={pooled_true}  "
          f"failed-to-parse={pooled_fail}")
    print(f"  false-rate Wilson 95% CI: {w_pooled['est']:.3f}  "
          f"[{w_pooled['lo']:.3f}, {w_pooled['hi']:.3f}]")
    return 0


def main() -> int:
    for var in ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"):
        if not os.environ.get(var):
            print(f"ERROR: {var} not set in environment", file=sys.stderr)
            return 2
    args = parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
