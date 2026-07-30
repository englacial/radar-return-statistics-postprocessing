"""Diagnose SNR saturation: bed power approaching the post-bed noise floor.

Observed required_surface_snr_dB is right-censored when the picked bed power
bottoms out at the noise floor; the censoring distance is exactly
margin = bed_power_dB - post_bed_noise_dB. Produces
outputs/model/analysis/saturation_diagnostics.png and prints flag fractions.

Usage: uv run python scripts/saturation_diagnostics.py
"""

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import C_GRL, C_OTHER, INK, SHEET_COLOR, style_axis as style  # noqa: E402

C_STORE = SHEET_COLOR
TARGET = "required_surface_snr_dB"
COLS = [TARGET, "bed_power_dB", "post_bed_noise_dB", "surface_power_dB", "latitude"]


def main():
    out_dir = Path("outputs")
    frames = []
    for store in ["antarctica", "greenland"]:
        df = pd.read_parquet(out_dir / store / f"{store}.parquet", columns=COLS)
        df["store"] = store
        frames.append(df)
    df = pd.concat(frames, ignore_index=True).dropna(subset=[TARGET, "bed_power_dB",
                                                             "post_bed_noise_dB"])
    df["margin_dB"] = df["bed_power_dB"] - df["post_bed_noise_dB"]

    print("bed-power margin over post-bed noise floor (dB):")
    for store, g in df.groupby("store"):
        q = g["margin_dB"].quantile([0.01, 0.05, 0.25, 0.5])
        flags = {t: float((g['margin_dB'] < t).mean()) for t in (3, 5, 10)}
        print(f"  {store:9s} n={len(g):6d}  q1%={q.iloc[0]:6.1f}  q5%={q.iloc[1]:6.1f}  "
              f"q25%={q.iloc[2]:6.1f}  med={q.iloc[3]:6.1f}   "
              f"frac<3dB={flags[3]:.3f}  <5dB={flags[5]:.3f}  <10dB={flags[10]:.3f}")

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))

    # (a) margin distributions
    ax = axes[0]
    bins = np.linspace(-5, 60, 66)
    for store, g in df.groupby("store"):
        ax.hist(g["margin_dB"], bins=bins, density=True, histtype="step",
                linewidth=1.8, color=C_STORE[store], label=f"{store} (n={len(g):,})")
    ax.axvline(0, color="0.4", linewidth=1, linestyle=":")
    style(ax)
    ax.set_xlabel("bed power − post-bed noise [dB]", color=INK)
    ax.set_ylabel("density", color=INK)
    ax.set_title("(a) margin over noise floor", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    # (b) required SNR vs margin, binned median per store
    ax = axes[1]
    mbins = np.arange(-2, 50, 2)
    centers = 0.5 * (mbins[:-1] + mbins[1:])
    for store, g in df.groupby("store"):
        med = [g.loc[(g["margin_dB"] >= lo) & (g["margin_dB"] < hi), TARGET].median()
               for lo, hi in zip(mbins[:-1], mbins[1:])]
        ax.plot(centers, med, color=C_STORE[store], linewidth=2, label=store)
    style(ax)
    ax.set_xlabel("margin over noise floor [dB]", color=INK)
    ax.set_ylabel(f"median {TARGET}", color=INK)
    ax.set_title("(b) observed required SNR vs margin", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    # (c) the Greenland high-SNR edge: margin distribution for obs near the ceiling
    ax = axes[2]
    grl = df[df["store"] == "greenland"]
    near = grl[grl[TARGET] > 95]
    rest = grl[grl[TARGET] <= 95]
    bins = np.linspace(-5, 40, 46)
    ax.hist(rest["margin_dB"], bins=bins, density=True, histtype="stepfilled",
            alpha=0.3, color=C_GRL)
    ax.hist(rest["margin_dB"], bins=bins, density=True, histtype="step",
            linewidth=1.8, color=C_GRL, label=f"RSSNR ≤ 95 dB (n={len(rest):,})")
    ax.hist(near["margin_dB"], bins=bins, density=True, histtype="stepfilled",
            alpha=0.3, color=C_OTHER)
    ax.hist(near["margin_dB"], bins=bins, density=True, histtype="step",
            linewidth=1.8, color=C_OTHER, label=f"RSSNR > 95 dB (n={len(near):,})")
    style(ax)
    ax.set_xlabel("margin over noise floor [dB]", color=INK)
    ax.set_ylabel("density", color=INK)
    ax.set_title("(c) greenland: margin of near-ceiling obs", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    out = out_dir / "model" / "analysis" / "saturation_diagnostics.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
