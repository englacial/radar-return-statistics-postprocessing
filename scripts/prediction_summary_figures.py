"""Summary figures from the trained model's predictions.zarr.

Produces (in outputs/model/analysis/):
  map_pred_mean.png            posterior-mean RSSNR, both sheets, shared colorscale
  map_q80.png                  80th-percentile posterior predictive, shared colorscale
  hist_obs_vs_ppc_sheets.png   observed training data vs posterior-predictive draws

Coastlines are contoured from the cached BedMachine masks (native CRS — no
cartopy dependency). Requires the pipeline outputs (split.parquet + the model's
predictions.zarr) and the cached BedMachine netCDFs in outputs/cache/.

Usage: uv run python scripts/prediction_summary_figures.py [--model atten_refl]
"""

import argparse
import warnings
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import rioxarray  # noqa: F401  (registers .rio accessor)
import xarray as xr

warnings.filterwarnings("ignore")
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import C_ANT, C_GRL, INK, LS_OBS, LS_PPC, style_axis  # noqa: E402

Z80 = 0.8416  # standard normal 80th percentile
BEDMACHINE = {
    "antarctic": ("outputs/cache/NSIDC-0756_BedMachineAntarctica_19700101-20191001_V04.1.nc", 10),
    "greenland": ("outputs/cache/BedMachineGreenland-v6.nc", 33),
}


def coastlines():
    out = {}
    for sheet, (nc, stride) in BEDMACHINE.items():
        ds = xr.open_dataset(nc)
        m = ds["mask"][::stride, ::stride]
        out[sheet] = (m["x"].values, m["y"].values, (m.values > 0).astype(float))
        ds.close()
    return out


def draw_maps(layers: dict, coast: dict, label: str, out_path: Path):
    allv = np.concatenate([v[0][np.isfinite(v[0])] for v in layers.values()])
    vmin, vmax = np.percentile(allv, [2, 98])
    fig, axes = plt.subplots(1, 2, figsize=(15.5, 7))
    for ax, sheet in zip(axes, ["antarctic", "greenland"]):
        arr, x, y = layers[sheet]
        im = ax.imshow(arr, cmap="viridis", vmin=vmin, vmax=vmax,
                       extent=[x[0], x[-1], y[-1], y[0]])
        cx, cy, cm = coast[sheet]
        ax.contour(cx, cy, cm, levels=[0.5], colors="0.25", linewidths=0.5)
        ax.set_aspect("equal")
        ax.set_title(sheet, color=INK, fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
    fig.colorbar(im, ax=axes, shrink=0.75, label=f"{label} [dB]")
    fig.suptitle(f"{label} — shared colorscale [{vmin:.0f}, {vmax:.0f}] dB",
                 color=INK, fontsize=12)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="atten_refl")
    args = parser.parse_args()
    zarr_path = f"outputs/model/{args.model}/predictions.zarr"
    out_dir = Path("outputs/model/analysis")
    out_dir.mkdir(parents=True, exist_ok=True)
    coast = coastlines()

    mean_layers, q80_layers, ppc = {}, {}, {}
    rng = np.random.default_rng(0)
    for sheet in ["antarctic", "greenland"]:
        ds = xr.open_zarr(zarr_path, group=sheet)
        mu, sd = ds["pred_mean"].values, ds["pred_std"].values
        x, y = ds["x"].values, ds["y"].values
        mean_layers[sheet] = (mu, x, y)
        q80_layers[sheet] = (mu + Z80 * sd, x, y)
        ok = np.isfinite(mu) & np.isfinite(sd)
        ppc[sheet] = mu[ok] + rng.standard_normal(int(ok.sum())) * sd[ok]

    draw_maps(mean_layers, coast, "posterior-mean required surface SNR",
              out_dir / "map_pred_mean.png")
    draw_maps(q80_layers, coast, "80th-percentile required surface SNR",
              out_dir / "map_q80.png")

    df = pd.read_parquet("outputs/model/split.parquet",
                         columns=["ice_sheet", "required_surface_snr_dB"])
    fig, ax = plt.subplots(figsize=(10, 6))
    bins = np.linspace(-40, 140, 90)
    for sheet, color, label in [("antarctic", C_ANT, "Antarctica"),
                                ("greenland", C_GRL, "Greenland")]:
        obs = df.loc[df["ice_sheet"] == sheet, "required_surface_snr_dB"].dropna()
        ax.hist(obs, bins=bins, density=True, histtype="step", linewidth=1.9,
                color=color, linestyle=LS_OBS,
                label=f"{label} observations (n={len(obs):,})")
        ax.hist(ppc[sheet], bins=bins, density=True, histtype="step", linewidth=1.9,
                color=color, linestyle=LS_PPC,
                label=f"{label} posterior predictive (n={len(ppc[sheet]):,})")
    style_axis(ax)
    ax.set_xlabel("required surface SNR [dB]", color=INK)
    ax.set_ylabel("density", color=INK)
    ax.set_title("Observed training data vs full-grid posterior predictive, by ice sheet",
                 color=INK, fontsize=12)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(out_dir / "hist_obs_vs_ppc_sheets.png", dpi=150, bbox_inches="tight")
    print(f"wrote {out_dir / 'hist_obs_vs_ppc_sheets.png'}")


if __name__ == "__main__":
    main()
