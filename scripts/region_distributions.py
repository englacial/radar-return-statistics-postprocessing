"""Distribution comparisons: observed target vs model predictions, by region.

Produces (in outputs/model/analysis/):
  cdf_sheet_obs_pred.png     - CDFs for {antarctic, greenland} x {observations, predictions}
  region_histograms.png      - per-region density histograms, training obs vs predictions

Regions are assigned per blocking *cell* (each cell in exactly one region) from
the mean lon/lat of its grid points:
  Antarctic peninsula : lat > -75 and -75 <= lon <= -55
  West Antarctica     : remainder of lon < -30 or lon > 165 (Ross-side wrap)
  East Antarctica     : remainder (-30 <= lon <= 165)
  Northern Greenland  : lat >= 72
  Southern Greenland  : lat < 72

Usage: uv run python scripts/region_distributions.py [--model linear]
"""

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import xarray as xr

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import INK, LS_OBS, LS_PPC, LS_PRED, SHEET_COLOR, style_axis  # noqa: E402

TARGET = "required_surface_snr_dB"


def classify_cell(sheet: str, lon: float, lat: float) -> str:
    if sheet == "greenland":
        return "Northern Greenland" if lat >= 72 else "Southern Greenland"
    if lat > -75 and -75 <= lon <= -55:
        return "Antarctic peninsula"
    if lon < -30 or lon > 165:
        return "West Antarctica"
    return "East Antarctica"


def load_data(out_dir: Path, model: str) -> pd.DataFrame:
    df = pd.read_parquet(out_dir / "model" / "split.parquet")
    geometry = json.loads((out_dir / "model" / "grid.manifest.json").read_text())["geometry"]
    df["pred_mean"] = np.nan
    df["pred_std"] = np.nan
    for sheet in geometry:
        ds = xr.open_zarr(out_dir / "model" / model / "predictions.zarr", group=sheet)
        rows = (df["ice_sheet"] == sheet).to_numpy()
        iy = df.loc[rows, "grid_iy"].to_numpy()
        ix = df.loc[rows, "grid_ix"].to_numpy()
        df.loc[rows, "pred_mean"] = ds["pred_mean"].values[iy, ix]
        df.loc[rows, "pred_std"] = ds["pred_std"].values[iy, ix]
    # One posterior-predictive draw per grid point: mean + full predictive noise.
    rng = np.random.default_rng(0)
    df["pred_ppc"] = df["pred_mean"] + rng.standard_normal(len(df)) * df["pred_std"]

    cell_centers = df.groupby("cell_id").agg(
        ice_sheet=("ice_sheet", "first"),
        lon=("longitude", "mean"),
        lat=("latitude", "mean"),
    )
    region_by_cell = {
        cell: classify_cell(row.ice_sheet, row.lon, row.lat)
        for cell, row in cell_centers.iterrows()
    }
    df["region"] = df["cell_id"].map(region_by_cell)
    return df


def ecdf(values: np.ndarray):
    v = np.sort(values[np.isfinite(values)])
    return v, np.arange(1, len(v) + 1) / len(v)


def plot_cdf(df: pd.DataFrame, model: str, out_path: Path, ppc: bool = False):
    fig, ax = plt.subplots(figsize=(8, 5.5))
    # Conventions: color = ice sheet, linestyle = data source.
    for sheet, label in [("antarctic", "Antarctica"), ("greenland", "Greenland")]:
        rows = df[df["ice_sheet"] == sheet]
        color = SHEET_COLOR[sheet]
        series = [
            (rows[TARGET].to_numpy(), LS_OBS, "observations"),
            (rows["pred_mean"].to_numpy(), LS_PRED, "predictions"),
        ]
        if ppc:
            series.append((rows["pred_ppc"].to_numpy(), LS_PPC, "posterior predictive"))
        for values, ls, src in series:
            x, y = ecdf(values)
            ax.plot(x, y, color=color, linestyle=ls, linewidth=2,
                    label=f"{label} {src} (n={len(x):,})")
    style_axis(ax)
    ax.set_xlabel("required surface SNR [dB]", color=INK)
    ax.set_ylabel("cumulative fraction", color=INK)
    ax.set_ylim(0, 1)
    ax.set_title(f"Observed vs predicted ({model}) required surface SNR",
                 color=INK, fontsize=12)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


REGION_ORDER = ["Antarctic peninsula", "West Antarctica", "East Antarctica",
                "Northern Greenland", "Southern Greenland"]


def plot_region_histograms(df: pd.DataFrame, model: str, out_path: Path, ppc: bool = False):
    fig, axes = plt.subplots(2, 3, figsize=(15, 8.5))
    train = df[(df["fold"] >= 0) & df[TARGET].notna()]
    for ax, region in zip(axes.ravel(), REGION_ORDER):
        obs = train.loc[train["region"] == region, TARGET].dropna()
        pred = df.loc[df["region"] == region, "pred_mean"].dropna()
        lo = min(obs.min(), pred.quantile(0.001)) if len(obs) else pred.quantile(0.001)
        hi = max(obs.max(), pred.quantile(0.999)) if len(obs) else pred.quantile(0.999)
        # Conventions: color = the region's ice sheet, linestyle = data source.
        color = SHEET_COLOR["greenland" if "Greenland" in region else "antarctic"]
        series = [(obs, LS_OBS, 0.3, f"training obs (n={len(obs):,})"),
                  (pred, LS_PRED, 0.0, f"predictions (n={len(pred):,})")]
        if ppc:
            draw = df.loc[df["region"] == region, "pred_ppc"].dropna()
            series.append((draw, LS_PPC, 0.0, f"posterior predictive (n={len(draw):,})"))
            lo = min(lo, draw.quantile(0.001))
            hi = max(hi, draw.quantile(0.999))
        bins = np.linspace(lo, hi, 40)
        for values, ls, fill_alpha, label in series:
            if len(values):
                if fill_alpha:
                    ax.hist(values, bins=bins, density=True, histtype="stepfilled",
                            alpha=fill_alpha, color=color)
                ax.hist(values, bins=bins, density=True, histtype="step",
                        linewidth=1.8, color=color, linestyle=ls, label=label)
        style_axis(ax)
        ax.set_title(region, color=INK, fontsize=11)
        ax.set_xlabel("required surface SNR [dB]", color=INK, fontsize=9)
        ax.set_ylabel("density", color=INK, fontsize=9)
        ax.legend(frameon=False, fontsize=8)
    axes.ravel()[-1].axis("off")  # 5 regions, 6 slots
    fig.suptitle(f"Training observations vs full-region predictions ({model})",
                 color=INK, fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="linear")
    parser.add_argument("--out-dir", default="outputs")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    analysis_dir = out_dir / "model" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    df = load_data(out_dir, args.model)
    print(df.groupby("region")[[TARGET, "pred_mean"]].count().rename(
        columns={TARGET: "n_obs", "pred_mean": "n_pred"}))

    cdf_path = analysis_dir / f"cdf_sheet_obs_pred_{args.model}.png"
    hist_path = analysis_dir / f"region_histograms_{args.model}.png"
    plot_cdf(df, args.model, cdf_path)
    plot_region_histograms(df, args.model, hist_path)
    plot_cdf(df, args.model, cdf_path.with_stem(cdf_path.stem + "_ppc"), ppc=True)
    plot_region_histograms(df, args.model, hist_path.with_stem(hist_path.stem + "_ppc"),
                           ppc=True)
    print(f"wrote {cdf_path} and {hist_path} (+ _ppc versions)")


if __name__ == "__main__":
    main()
