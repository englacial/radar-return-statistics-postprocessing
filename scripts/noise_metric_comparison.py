"""Compare pick-independent noise-floor metrics on the reprocessed ASE store:
pre-surface window vs record-tail (last 5 us) — which better represents the
noise floor that matters for bed detection?

Experiments (population: qc_surface_pass & bed_pick_attempted):
 (a) Agreement with the at-bed ground truth: metric - post_bed_noise_dB on
     picked traces (the post-bed window is closest to where detection happens
     but is pick-dependent, so it can't be used for no-pick traces).
 (b) Contamination check: (record_tail - post_bed) vs bed_twtt — deep beds
     approach the record end, so late-arriving energy can leak into the tail.
 (c) Empirical detection curves per season: detected fraction vs margin, where
     margin uses bed power interpolated along-track over pick gaps (so both
     detected and undetected traces get an x). Steeper, better-overlaying
     sigmoids = better noise metric.

Outputs: printed stats + outputs/model/analysis/noise_metric_comparison.png
Usage: uv run python scripts/noise_metric_comparison.py
"""

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from radar_postproc.config import load_config  # noqa: E402
from radar_postproc.io_icechunk import extract_points  # noqa: E402

from plot_style import INK, style_axis  # noqa: E402

# Seasons are not ice sheets: non-sheet categorical colors per conventions.
SEASON_COLORS = ["tab:orange", "tab:purple", "tab:red", "tab:brown", "tab:pink",
                 "tab:gray", "tab:olive", "tab:cyan"]

CACHE = Path("outputs/cache/ase_reprocessed_traces.parquet")
CARRY = ["frame_id", "slow_time", "latitude", "longitude", "surface_power_dB",
         "bed_power_dB", "required_surface_snr_dB", "surface_twtt", "bed_twtt",
         "pre_surface_noise_dB", "post_bed_noise_dB", "record_tail_noise_dB",
         "qc_pass", "qc_surface_pass", "bed_pick_available", "bed_pick_attempted"]
METRICS = {"pre-surface": "pre_surface_noise_dB", "record-tail": "record_tail_noise_dB"}


def load_traces() -> pd.DataFrame:
    if CACHE.exists():
        return pd.read_parquet(CACHE)
    config = load_config("config/ase.yaml")
    gdf = extract_points(config["store"], snapshot_id=config["icechunk"]["snapshot_id"],
                         carry_columns=CARRY, qc_only=False)
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE, index=False)
    return df


def interp_bed_power(df: pd.DataFrame) -> np.ndarray:
    """Bed power interpolated along-track (per frame) across pick gaps."""
    out = np.full(len(df), np.nan)
    for _, idx in df.groupby("frame_id").indices.items():
        sub = df.iloc[idx].sort_values("slow_time")
        bp = sub["bed_power_dB"]
        if bp.notna().sum() >= 2:
            out[sub.index] = bp.interpolate(method="linear", limit_direction="both").to_numpy()
        elif bp.notna().sum() == 1:
            out[sub.index] = bp.dropna().iloc[0]
    return out


def main():
    df = load_traces().reset_index(drop=True)
    df = df[df["qc_surface_pass"].astype(bool) & df["bed_pick_attempted"].astype(bool)]
    df = df.reset_index(drop=True)
    df["season"] = df["collection"] if "collection" in df else df["frame_id"].str.slice(0, 4)
    det = df["bed_pick_available"].astype(bool)
    print(f"analysis population: {len(df)} traces, detected {det.sum()} "
          f"({det.mean():.1%}), missing {(~det).sum()}")

    picked = df[det & df["post_bed_noise_dB"].notna()]
    print("\n(a) metric - post_bed_noise_dB on picked traces:")
    for name, col in METRICS.items():
        delta = picked[col] - picked["post_bed_noise_dB"]
        print(f"  {name:12s} median={delta.median():+6.2f} dB  "
              f"IQR=[{delta.quantile(.25):+6.2f}, {delta.quantile(.75):+6.2f}]  "
              f"per-season medians: "
              f"{picked.groupby('season').apply(lambda g, c=col: (g[c]-g['post_bed_noise_dB']).median()).round(2).to_dict()}")

    df["bed_power_interp"] = interp_bed_power(df)

    fig, axes = plt.subplots(2, 2, figsize=(15, 11))

    # (a) offset distributions
    ax = axes[0, 0]
    bins = np.linspace(-20, 20, 81)
    for (name, col), color in zip(METRICS.items(), ["tab:orange", "tab:purple"]):
        delta = picked[col] - picked["post_bed_noise_dB"]
        ax.hist(delta, bins=bins, density=True, histtype="step", linewidth=1.8,
                color=color, label=f"{name} − post-bed (median {delta.median():+.1f} dB)")
    ax.axvline(0, color="0.4", linewidth=1, linestyle=":")
    style_axis(ax)
    ax.set_xlabel("noise metric − post-bed noise [dB]", color=INK)
    ax.set_ylabel("density", color=INK)
    ax.set_title("(a) agreement with at-bed noise floor (picked traces)", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    # (b) tail contamination vs bed depth
    ax = axes[0, 1]
    tb = np.linspace(picked["bed_twtt"].quantile(0.01), picked["bed_twtt"].quantile(0.99), 25)
    centers = 0.5 * (tb[:-1] + tb[1:]) * 1e6
    for (name, col), color in zip(METRICS.items(), ["tab:orange", "tab:purple"]):
        med = [(picked.loc[(picked["bed_twtt"] >= lo) & (picked["bed_twtt"] < hi), col]
                - picked.loc[(picked["bed_twtt"] >= lo) & (picked["bed_twtt"] < hi),
                             "post_bed_noise_dB"]).median()
               for lo, hi in zip(tb[:-1], tb[1:])]
        ax.plot(centers, med, color=color, linewidth=2, label=name)
    ax.axhline(0, color="0.4", linewidth=1, linestyle=":")
    style_axis(ax)
    ax.set_xlabel("bed twtt [µs] (deeper → closer to record end)", color=INK)
    ax.set_ylabel("median (metric − post-bed) [dB]", color=INK)
    ax.set_title("(b) offset vs bed depth (tail-contamination check)", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    # (c, d) empirical detection curves per season, one panel per metric
    mbins = np.arange(-15, 40, 2.5)
    mcenters = 0.5 * (mbins[:-1] + mbins[1:])
    seasons = sorted(df["season"].unique())
    for ax, (name, col) in zip(axes[1], METRICS.items()):
        for season, color in zip(seasons, SEASON_COLORS):
            sub = df[df["season"] == season]
            margin = sub["bed_power_interp"] - sub[col]
            frac, ns = [], []
            for lo, hi in zip(mbins[:-1], mbins[1:]):
                sel = (margin >= lo) & (margin < hi)
                n = int(sel.sum())
                frac.append(sub.loc[sel, "bed_pick_available"].astype(bool).mean()
                            if n >= 20 else np.nan)
                ns.append(n)
            ax.plot(mcenters, frac, color=color, linewidth=2, marker="o", markersize=3,
                    label=f"{season} (n={len(sub):,})")
        style_axis(ax)
        ax.set_xlabel(f"interpolated bed power − {name} noise [dB]", color=INK)
        ax.set_ylabel("detected fraction", color=INK)
        ax.set_ylim(-0.02, 1.02)
        ax.set_title(f"detection curve by season — {name}", color=INK, fontsize=11)
        ax.legend(frameon=False, fontsize=9, loc="lower right")

    fig.suptitle("Noise-floor metrics on reprocessed ASE: pre-surface vs record-tail",
                 color=INK, fontsize=13)
    fig.tight_layout()
    out = Path("outputs/model/analysis/noise_metric_comparison.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
