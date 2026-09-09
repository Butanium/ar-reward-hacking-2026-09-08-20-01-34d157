#!/usr/bin/env python3
"""Figure for the engine-use ablation: per-arm rates with exact CIs + the mechanism split.

Every arm is its own bar with its own Clopper-Pearson 95% interval -- nothing is pooled
into a single aggregate, so an arm that behaves differently stays visible.

  ./plot_engine_use.py --out engine_use.png
"""
import argparse, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import engine_use_lib as L

ARMS = [
    # (log_dir, display label, model family)
    ("logs-rescored/baseline-astra",   "baseline (original, ph1)",   "astra"),
    ("logs/p2-stopeval-astra",          "stopeval (original)",        "astra"),
    ("logs/p2-believe-astra",           "believe (original + 1 line)", "astra"),
    ("logs/p2-nograding-astra",        "nograding  [same box]",      "astra"),
    ("logs/p2-notools-astra",          "notools",                    "astra"),
    ("logs/p2-nogame-astra",           "nogame  [NEW, same box]",    "astra"),
    ("logs-rescored/baseline-fable51", "baseline (original, ph1)",   "fable51"),
    ("logs/p2-stopeval-fable51",       "stopeval (original)",        "fable51"),
    ("logs/p2-believe-fable51",      "believe (original + 1 line)",                    "fable51"),
    ("logs/p2-nograding-fable51",      "nograding",                  "fable51"),
    ("logs/p2-notools-fable51",        "notools",                    "fable51"),
    ("logs/p2-nogame-fable51",         "nogame  [NEW]",              "fable51"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="engine_use.png")
    ap.add_argument("--mechanism", action="store_true",
                    help="also read transcripts for the discovery/exploitation panel (slower)")
    ap.add_argument("--dpi", type=int, default=150)
    a = ap.parse_args()

    rows = []
    for log_dir, label, family in ARMS:
        if not os.path.isdir(log_dir):
            print(f"  skip (missing): {log_dir}")
            continue
        try:
            d = L.load_arm(log_dir, mechanism=a.mechanism)
        except AssertionError as e:
            print(f"  skip ({e}): {log_dir}")
            continue
        if d["n"] == 0:
            print(f"  skip (0 scored samples): {log_dir}")
            continue
        d.update(label=label, family=family)
        rows.append(d)
    assert rows, "no arms loaded"

    ncols = 2 if a.mechanism else 1
    fig, axes = plt.subplots(1, ncols, figsize=(7.2 * ncols, 5.6), squeeze=False)

    # ---- panel A: engine_driven rate, one bar + exact CI per arm ------------------
    ax = axes[0][0]
    ypos, ylabels, colors = [], [], []
    y = 0
    for family in ("astra", "fable51"):
        fam = [r for r in rows if r["family"] == family]
        if not fam:
            continue
        for r in fam:
            k, n = r["engine_driven"], r["n"]
            lo, hi = L.clopper_pearson(k, n)
            p = k / n
            col = "#c0392b" if "NEW" in r["label"] else ("#7f8c8d" if family == "astra" else "#95a5a6")
            ax.barh(y, 100 * p, color=col, height=0.66,
                    edgecolor="black", linewidth=0.6 if "NEW" in r["label"] else 0)
            ax.plot([100 * lo, 100 * hi], [y, y], color="black", lw=1.4, zorder=3)
            for b in (lo, hi):
                ax.plot([100 * b] * 2, [y - 0.13, y + 0.13], color="black", lw=1.4, zorder=3)
            ax.text(101, y, f"{k}/{n}", va="center", fontsize=8, color="#444")
            ypos.append(y); ylabels.append(r["label"]); colors.append(col)
            y += 1
        ax.axhline(y - 0.5, color="black", lw=0.8, ls=":")
        fam_mid = (ypos[-len(fam)] + ypos[-1]) / 2
        ax.annotate(family, xy=(-0.40, fam_mid), xycoords=("axes fraction", "data"),
                    ha="center", va="center", fontsize=11, fontweight="bold",
                    rotation=90, annotation_clip=False)
        y += 0.6
    ax.set_yticks(ypos)
    ax.set_yticklabels(ylabels, fontsize=9)
    for tick, lab in zip(ax.get_yticklabels(), ylabels):
        if "NEW" in lab:
            tick.set_color("#c0392b"); tick.set_fontweight("bold")
    ax.invert_yaxis()
    ax.set_xlim(0, 112); ax.set_xlabel("games scored `engine_driven`  (%, exact 95% CI)")
    ax.set_title("Engine use by prompt ablation", fontsize=11, fontweight="bold")
    ax.grid(axis="x", alpha=0.25); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    # ---- panel B: discovery vs exploitation --------------------------------------
    if a.mechanism:
        ax2 = axes[0][1]
        fam = [r for r in rows if r["family"] == "astra"]
        yy = range(len(fam))
        for i, r in enumerate(fam):
            for off, key, col, lab in ((-0.19, "read_arena", "#2980b9", "inspected arena.py"),
                                       (0.19, "touched_socket", "#c0392b", "touched the socket")):
                k, n = r[key], r["n"]
                lo, hi = L.clopper_pearson(k, n)
                ax2.barh(i + off, 100 * k / n, height=0.34, color=col,
                         label=lab if i == 0 else None)
                ax2.plot([100 * lo, 100 * hi], [i + off] * 2, color="black", lw=1.2, zorder=3)
                ax2.text(100 * hi + 1.5, i + off, f"{k}/{n}", va="center",
                         fontsize=7.5, color="#444")
        ax2.set_yticks(list(yy))
        ax2.set_yticklabels([f"{r['label']}  (n={r['n']})" for r in fam], fontsize=9)
        ax2.invert_yaxis(); ax2.set_xlim(0, 118)
        ax2.set_xlabel("% of games (exact 95% CI)")
        ax2.set_title("astra: discovery vs exploitation", fontsize=11, fontweight="bold")
        ax2.legend(fontsize=8, loc="lower right", frameon=False)
        ax2.grid(axis="x", alpha=0.25); ax2.set_axisbelow(True)
        for s in ("top", "right"):
            ax2.spines[s].set_visible(False)

    inflight = [r["name"] for r in rows if r["status"] != "success"]
    note = "arms still in flight (preliminary): " + ", ".join(inflight) if inflight else ""
    if note:
        fig.text(0.5, 0.005, note, ha="center", fontsize=7.5, color="#b03a2e")
    fig.tight_layout(rect=(0.055, 0.03 if note else 0, 1, 1))
    fig.savefig(a.out, dpi=a.dpi)
    print(f"wrote {a.out}")
    for r in rows:
        print(f"  {r['family']:8s} {r['label']:26s} n={r['n']:2d} "
              f"driven={r['engine_driven']:2d} status={r['status']}")


if __name__ == "__main__":
    main()
