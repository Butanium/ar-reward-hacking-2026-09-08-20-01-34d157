#!/usr/bin/env python3
"""Cross-arm comparison of the engine_use_judge categorical score.

Tabulates every arm with exact (Clopper-Pearson) 95% CIs and runs two-sided Fisher exact
tests against a chosen reference. Statistics live in engine_use_lib so this table and
plot_engine_use.py cannot disagree.

  ./analyze_engine_use.py --model astra --ref p2-nograding-astra
"""
import argparse, csv, glob, os
from engine_use_lib import (CATS, CONTACT, clopper_pearson, fisher_p, load_arm)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="*", default=["logs", "logs-rescored"],
                    help="parent dirs holding one subdir per arm")
    ap.add_argument("--model", default=None, help="substring filter on arm name, e.g. fable51")
    ap.add_argument("--ref", default=None, help="arm name used as the Fisher reference")
    ap.add_argument("--out", default="engine_use_summary.csv")
    a = ap.parse_args()

    arms = {}
    for parent in a.dirs:
        for d in sorted(glob.glob(os.path.join(parent, "*"))):
            if not os.path.isdir(d):
                continue
            name = os.path.basename(d)
            if a.model and a.model not in name:
                continue
            try:
                info = load_arm(d)
            except AssertionError:
                continue
            if info["n"]:
                arms[f"{parent}/{name}"] = info

    rows = []
    print(f"{'arm':44s} {'variant':11s} {'scored':>6s} {'engine_driven':>24s} {'any contact':>24s}")
    for name, info in arms.items():
        n, kd, kc = info["n"], info["engine_driven"], info["any_contact"]
        variants, ntot = {info["variant"]}, info["n"]
        l1, h1 = clopper_pearson(kd, n)
        l2, h2 = clopper_pearson(kc, n)
        var = ",".join(str(x) for x in variants)
        print(f"{name:44s} {var:11s} {n:3d}/{ntot:<3d} "
              f"{kd:2d} {100*kd/n:5.1f}% [{100*l1:4.1f},{100*h1:5.1f}] "
              f"{kc:2d} {100*kc/n:5.1f}% [{100*l2:4.1f},{100*h2:5.1f}]")
        row = dict(arm=name, variant=var, n_scored=n, n_samples=ntot,
                   n_engine_driven=kd, n_any_contact=kc,
                   driven_lo=l1, driven_hi=h1, contact_lo=l2, contact_hi=h2)
        row.update({f"cat_{c}": info["counts"].get(c, 0) for c in CATS})
        rows.append(row)

    if a.ref:
        ref = next((k for k in arms if a.ref in k), None)
        assert ref, f"--ref {a.ref!r} matched no arm among {list(arms)}"
        ri = arms[ref]
        rd, rc, rn = ri["engine_driven"], ri["any_contact"], ri["n"]
        print(f"\nFisher exact vs reference {ref}  ({rd}/{rn} driven, {rc}/{rn} contact)")
        for name, info in arms.items():
            if name == ref:
                continue
            n, kd, kc = info["n"], info["engine_driven"], info["any_contact"]
            print(f"  {name:44s} driven p={fisher_p(rd, rn-rd, kd, n-kd):.4f}   "
                  f"contact p={fisher_p(rc, rn-rc, kc, n-kc):.4f}")
            for r in rows:
                if r["arm"] == name:
                    r["p_driven_vs_ref"] = fisher_p(rd, rn-rd, kd, n-kd)
                    r["p_contact_vs_ref"] = fisher_p(rc, rn-rc, kc, n-kc)

    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {a.out}  ({len(rows)} arms)")


if __name__ == "__main__":
    main()
