"""Empirical detection curves on the reprocessed ASE store (v2 snapshot).

The v2 store adds pick-free post-bed-window noise statistics anchored at the
along-track interpolated bed twtt (verified: post_bed_noise_interp_dB equals
post_bed_noise_dB wherever a pick exists):
  post_bed_noise_interp_dB  window median  -> at-depth noise floor, pick-free
  post_bed_peak_interp_dB   window peak    -> per-trace peak-over-median (delta)
  post_bed_std_interp_dB    window std     -> texture

Margin (x-axis): bed power over the pick-free at-depth noise floor,
    margin_i = bed_power_i (picked) or along-track-interpolated bed power
               (missing traces; optimistic proxy)  -  post_bed_noise_interp_dB

Panels:
 (a) window peak-over-median (delta) for picked vs missing traces
 (b) margin distributions, detected vs missing (mixture check)
 (c) detection curves per season + pooled mixture-probit fit:
       P(detect | m) = (1 - pi) * Phi((m - theta) / tau)

Outputs: printed fit + outputs/model/analysis/detection_curves.{json,png}
Usage: uv run python scripts/detection_curves.py
"""

import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from radar_postproc.config import load_config  # noqa: E402
from radar_postproc.io_icechunk import extract_points  # noqa: E402

from plot_style import INK, style_axis  # noqa: E402

SEASON_COLORS = ["tab:orange", "tab:purple", "tab:red", "tab:brown", "tab:pink"]
CACHE = Path("outputs/cache/ase_reprocessed_traces_v2.parquet")
CARRY = ["frame_id", "slow_time", "surface_power_dB", "bed_power_dB",
         "required_surface_snr_dB", "surface_twtt", "bed_twtt",
         "pre_surface_noise_dB", "post_bed_noise_dB", "record_tail_noise_dB",
         "post_bed_noise_interp_dB", "post_bed_peak_interp_dB", "post_bed_std_interp_dB",
         "bed_pick_quality", "qc_pass", "qc_surface_pass",
         "bed_pick_available", "bed_pick_attempted"]


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


def fit_mixture_probit(margin: np.ndarray, detected: np.ndarray):
    """MLE of P(detect|m) = (1-pi) * Phi((m-theta)/tau)."""
    m, d = margin[np.isfinite(margin)], detected[np.isfinite(margin)].astype(float)

    def nll(params):
        theta, log_tau, logit_pi = params
        p = (1 - _sigmoid(logit_pi)) * norm.cdf((m - theta) / np.exp(log_tau))
        p = np.clip(p, 1e-9, 1 - 1e-9)
        return -(d * np.log(p) + (1 - d) * np.log(1 - p)).sum()

    best = min((minimize(nll, x0, method="Nelder-Mead") for x0 in
                [(8, np.log(3), -4), (15, np.log(5), -3), (5, np.log(2), -5)]),
               key=lambda r: r.fun)
    theta, log_tau, logit_pi = best.x
    return {"theta_dB": float(theta), "tau_dB": float(np.exp(log_tau)),
            "pi": float(_sigmoid(logit_pi)), "n": int(len(m)), "nll": float(best.fun)}


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def interp_bed_power(df: pd.DataFrame) -> np.ndarray:
    """Bed power interpolated along-track (per frame) across pick gaps."""
    out = np.full(len(df), np.nan)
    for _, idx in df.groupby("frame_id").indices.items():
        sub = df.iloc[idx].sort_values("slow_time")
        bp = sub["bed_power_dB"]
        if bp.notna().sum() >= 2:
            out[sub.index] = bp.interpolate(method="linear", limit_direction="both").to_numpy()
    return out


def main():
    df = load_traces()
    df = df[df["qc_surface_pass"].astype(bool) & df["bed_pick_attempted"].astype(bool)]
    df = df.reset_index(drop=True)
    df["season"] = df["collection"]
    det = df["bed_pick_available"].astype(bool)
    bed_eff = np.where(det, df["bed_power_dB"], interp_bed_power(df))
    df["margin"] = bed_eff - df["post_bed_noise_interp_dB"].to_numpy()
    df["delta"] = df["post_bed_peak_interp_dB"] - df["post_bed_noise_interp_dB"]
    have = df["margin"].notna()
    print(f"population: {len(df)} traces, detected {det.sum()}, missing {(~det).sum()}; "
          f"margin defined for {have.mean():.1%} (missing traces: {have[~det].mean():.1%})")
    print(f"\n(a) window peak-over-median delta: picked median="
          f"{df.loc[det, 'delta'].median():+.2f} dB, "
          f"missing median={df.loc[~det, 'delta'].median():+.2f} dB")

    pooled = fit_mixture_probit(df["margin"].to_numpy(), det.to_numpy())
    print(f"\n(c) pooled mixture-probit fit: theta={pooled['theta_dB']:.1f} dB, "
          f"tau={pooled['tau_dB']:.1f} dB, pi={pooled['pi']:.4f}")
    season_fits = {}
    for season, g in df.groupby("season"):
        season_fits[season] = fit_mixture_probit(
            g["margin"].to_numpy(), g["bed_pick_available"].astype(bool).to_numpy())
        f = season_fits[season]
        print(f"    {season}: theta={f['theta_dB']:.1f}, tau={f['tau_dB']:.1f}, "
              f"pi={f['pi']:.4f} (n={f['n']})")

    fig, axes = plt.subplots(1, 3, figsize=(17.5, 5.5))

    ax = axes[0]
    bins = np.linspace(-2, 40, 64)
    ax.hist(df.loc[det, "delta"].dropna(), bins=bins, density=True, histtype="step",
            linewidth=1.8, color="tab:orange", label="picked traces")
    ax.hist(df.loc[~det, "delta"].dropna(), bins=bins, density=True, histtype="step",
            linewidth=1.8, color="tab:purple", label="missing traces")
    style_axis(ax)
    ax.set_xlabel("window peak − median (delta) [dB]", color=INK)
    ax.set_ylabel("density", color=INK)
    ax.set_title("(a) peak-over-median of at-depth noise window", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    ax = axes[1]
    bins = np.linspace(-10, 120, 66)
    ax.hist(df.loc[det, "margin"].dropna(), bins=bins, density=True, histtype="step",
            linewidth=1.8, color="tab:orange", label=f"detected (n={det.sum():,})")
    ax.hist(df.loc[~det, "margin"].dropna(), bins=bins, density=True, histtype="step",
            linewidth=1.8, color="tab:purple", label=f"missing (n={(~det).sum():,})")
    style_axis(ax)
    ax.set_xlabel("margin: bed power (interp for missing) − at-depth noise [dB]", color=INK)
    ax.set_ylabel("density", color=INK)
    ax.set_title("(b) margins, detected vs missing", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    ax = axes[2]
    mbins = np.arange(-5, 45, 2.0)
    centers = 0.5 * (mbins[:-1] + mbins[1:])
    for (season, g), color in zip(df.groupby("season"), SEASON_COLORS):
        frac = [g.loc[(g["margin"] >= lo) & (g["margin"] < hi), "bed_pick_available"]
                .astype(bool).mean() if ((g["margin"] >= lo) & (g["margin"] < hi)).sum() >= 15
                else np.nan for lo, hi in zip(mbins[:-1], mbins[1:])]
        ax.plot(centers, frac, color=color, linewidth=1.6, marker="o", markersize=3,
                alpha=0.8, label=season)
    mm = np.linspace(-5, 45, 200)
    ax.plot(mm, (1 - pooled["pi"]) * norm.cdf((mm - pooled["theta_dB"]) / pooled["tau_dB"]),
            color=INK, linewidth=2.5,
            label=(f"pooled fit: θ={pooled['theta_dB']:.1f}, τ={pooled['tau_dB']:.1f}, "
                   f"π={pooled['pi']:.3f}"))
    style_axis(ax)
    ax.set_xlabel("margin over at-depth noise floor [dB]", color=INK)
    ax.set_ylabel("detected fraction", color=INK)
    ax.set_ylim(-0.02, 1.02)
    ax.set_title("(c) detection curves + mixture-probit fit", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=8, loc="lower right")

    out_dir = Path("outputs/model/analysis")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "detection_curves.json").write_text(json.dumps(
        {"pooled": pooled, "per_season": season_fits,
         "delta_median_picked_dB": float(df.loc[det, "delta"].median()),
         "delta_median_missing_dB": float(df.loc[~det, "delta"].median())}, indent=2))
    fig.tight_layout()
    fig.savefig(out_dir / "detection_curves.png", dpi=150, bbox_inches="tight")
    print(f"\nwrote {out_dir}/detection_curves.json and detection_curves.png")


if __name__ == "__main__":
    main()
