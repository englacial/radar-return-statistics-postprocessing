"""Validate the record-tail noise window against the record end (ASE v3 store).

record_end_twtt lets us measure per-trace headroom = record_end - bed twtt.
If deep returns approach the record end, the last-5-us tail window catches bed
coda and the tail noise estimate is biased high (conservative for censoring,
but worth quantifying before trusting it on deep-bed surveys like Greenland).

Checks:
 (a) headroom distributions per season (picked traces; interp bed for missing)
 (b) tail-noise elevation vs headroom: per season, median record_tail relative
     to that season's median at large headroom (>15 us). A rise at low
     headroom = contamination.

Outputs: printed stats + outputs/model/analysis/record_end_check.png
Usage: uv run python scripts/record_end_check.py
"""

from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from detection_curves import interp_bed_power, load_traces  # noqa: E402
from plot_style import INK, style_axis  # noqa: E402

SEASON_COLORS = ["tab:orange", "tab:purple", "tab:red", "tab:brown", "tab:pink"]
TAIL_US = 5.0


def main():
    df = load_traces()
    df = df[df["qc_surface_pass"].astype(bool) & df["bed_pick_attempted"].astype(bool)]
    df = df.reset_index(drop=True)
    df["season"] = df["collection"]
    det = df["bed_pick_available"].astype(bool)
    # headroom for missing traces uses the along-track interpolated bed twtt
    # (interp_bed_power interpolates whatever sits in bed_power_dB, so feed it twtt)
    tmp = df.copy()
    tmp["bed_power_dB"] = df["bed_twtt"]
    bed_eff = np.where(det, df["bed_twtt"], interp_bed_power(tmp))
    df["headroom_us"] = (df["record_end_twtt"] - bed_eff) * 1e6

    print("headroom = record end - bed twtt [us]:")
    for season, g in df.groupby("season"):
        h = g["headroom_us"].dropna()
        print(f"  {season}: median={h.median():6.1f}  q5%={h.quantile(.05):6.1f}  "
              f"min={h.min():6.1f}  frac<{TAIL_US:.0f}us={(h < TAIL_US).mean():.2%}  "
              f"frac<{2*TAIL_US:.0f}us={(h < 2*TAIL_US).mean():.2%}")

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    ax = axes[0]
    bins = np.linspace(0, 60, 61)
    for (season, g), color in zip(df.groupby("season"), SEASON_COLORS):
        ax.hist(g["headroom_us"].dropna(), bins=bins, density=True, histtype="step",
                linewidth=1.8, color=color, label=season)
    ax.axvline(TAIL_US, color="0.3", linewidth=1.2, linestyle=":",
               label=f"tail window ({TAIL_US:.0f} µs)")
    style_axis(ax)
    ax.set_xlabel("headroom: record end − bed twtt [µs]", color=INK)
    ax.set_ylabel("density", color=INK)
    ax.set_title("(a) bed headroom above record end", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    ax = axes[1]
    hbins = np.arange(0, 40, 2.0)
    centers = 0.5 * (hbins[:-1] + hbins[1:])
    for (season, g), color in zip(df.groupby("season"), SEASON_COLORS):
        ref = g.loc[g["headroom_us"] > 15, "record_tail_noise_dB"].median()
        med = [g.loc[(g["headroom_us"] >= lo) & (g["headroom_us"] < hi),
                     "record_tail_noise_dB"].median() - ref
               if ((g["headroom_us"] >= lo) & (g["headroom_us"] < hi)).sum() >= 25
               else np.nan for lo, hi in zip(hbins[:-1], hbins[1:])]
        ax.plot(centers, med, color=color, linewidth=2, marker="o", markersize=3,
                label=season)
    ax.axhline(0, color="0.8", linewidth=1)
    ax.axvline(TAIL_US, color="0.3", linewidth=1.2, linestyle=":")
    style_axis(ax)
    ax.set_xlabel("headroom [µs]", color=INK)
    ax.set_ylabel("tail noise − season baseline [dB]", color=INK)
    ax.set_title("(b) tail-noise elevation vs headroom", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    out = Path("outputs/model/analysis/record_end_check.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
