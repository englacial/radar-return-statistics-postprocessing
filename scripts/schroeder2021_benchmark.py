"""Benchmark the current Bayesian RSSNR model against Schroeder et al. (2021) / IGARSS 2021.

Reproduces the two Antarctic CDF figures of the 2021 paper (its Fig. 2a and Fig. 3)
with our data and overlays the 2021 model's own exported grid.

The 2021 export (`reference/rssnr_igarss_exported_data_20241215.csv`, checked in) is a
~50k-cell random subsample of a 5 km EPSG:3031 Antarctic grid with columns:
  x, y            EPSG:3031 cell centres (offset half a cell from our grid)
  snr             the 2021 training observation of required surface SNR [dB] (n=1.8k)
  snr_pred        the 2021 model's predicted required surface SNR [dB]
  snr_pred_std    its predictive 1-sigma [dB] (~14.5 dB, near-constant)
  mask            BedMachine mask (2 grounded, 3 floating)
  v, thickness, smb, surf_temp, base_temp  covariates
The fast/slow velocity split is 50 m/yr, the 2021 convention: applying it to this
export reproduces the published Fig. 3 medians (25.5 / 58.2 / 72.1 dB for shelf /
fast / slow, against ~25 / 57 / 72 dB read off the figure).

Produces (in outputs/model/analysis/):
  cdf_antarctica_vs_2021.png          Fig. 2a analogue (predicted CDFs, full extent)
  cdf_antarctica_icemask_vs_2021.png  Fig. 3 analogue (ice shelf / fast / slow ice)
  schroeder2021_benchmark.csv         percentiles + resolvable-bed fractions

Usage: uv run python scripts/schroeder2021_benchmark.py [--model atten_refl]
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

from plot_style import C_ANT, C_GRL, C_OTHER, C_OTHER2, INK, style_axis  # noqa: E402

CSV_2021 = Path("reference/rssnr_igarss_exported_data_20241215.csv")
V_THRESHOLD = 50.0  # m/yr, fast vs slow grounded ice (2021 convention)
CLASSES = ["Ice shelf", "Fast-moving ice", "Slow-moving ice"]
CLASS_COLOR = {  # non-reserved colours: these panels split by class, not ice sheet
    "Ice shelf": C_OTHER,
    "Fast-moving ice": C_OTHER2,
    "Slow-moving ice": "tab:brown",
}
# All series here are model predictions, so the repo convention makes them all
# dashed; the dash *pattern* carries the series identity so colour is not the only
# cue (colour-blind readers, greyscale printing).
DASH_ANT = (0, (7, 2.5))            # long dash
DASH_GRL = (0, (3.5, 2))            # short dash (kept long enough not to read as dotted,
                                    # which the repo reserves for posterior-predictive draws)
DASH_2021 = (0, (9, 2, 1.5, 2))     # long-short (dash-dot)
CLASS_DASH = {"Ice shelf": DASH_ANT, "Fast-moving ice": DASH_GRL,
              "Slow-moving ice": DASH_2021}
SNR_LEVELS = [40, 60, 80, 100]  # dB, "fraction of bed resolvable at ..."
PCTS = [10, 25, 50, 75, 90]


def classify(mask, v, threshold=V_THRESHOLD):
    """Ice shelf / fast / slow. BedMachine mask 3 = floating; 2 and 4 = grounded."""
    return np.where(mask == 3, CLASSES[0],
                    np.where(v > threshold, CLASSES[1], CLASSES[2]))


def load_ours(model: str, sheet: str = "antarctic") -> pd.DataFrame:
    """Grid points for one ice sheet with the model's posterior predictions joined on."""
    import pyarrow.parquet as pq
    path = "outputs/model/split.parquet"
    names = pq.ParquetFile(path).schema_arrow.names
    vcol = next(c for c in ("surface_v_m_yr", "itslive_v_m_yr") if c in names)
    df = pd.read_parquet(path, columns=[
        "ice_sheet", "grid_ix", "grid_iy", "x", "y", "latitude", "bedmachine_mask",
        vcol, "required_surface_snr_dB", "is_nondetect"])
    df = df[df["ice_sheet"] == sheet].rename(columns={vcol: "v_m_yr"}).copy()
    df.attrs["velocity_column"] = vcol
    ds = xr.open_zarr(f"outputs/model/{model}/predictions.zarr", group=sheet)
    iy, ix = df["grid_iy"].to_numpy(), df["grid_ix"].to_numpy()
    df["pred_mean"] = ds["pred_mean"].values[iy, ix]
    df["pred_std"] = ds["pred_std"].values[iy, ix]
    df["class"] = classify(df["bedmachine_mask"], df["v_m_yr"])
    return df


def load_2021() -> pd.DataFrame:
    """2021 export, deduplicated onto unique grid cells (the export resamples cells)."""
    raw = pd.read_csv(CSV_2021)
    df = raw.groupby(["x", "y"]).agg(
        snr_pred=("snr_pred", "mean"), snr_pred_std=("snr_pred_std", "mean"),
        snr=("snr", "mean"), mask=("mask", "first"), v=("v", "first"),
    ).reset_index()
    df["class"] = classify(df["mask"], df["v"])
    return df


def tag_footprint(df21: pd.DataFrame, ours: pd.DataFrame) -> pd.DataFrame:
    """Flag 2021 cells that fall on a grid cell where we have a finite prediction."""
    geom = json.loads(Path("outputs/model/grid.manifest.json").read_text())["geometry"]["antarctic"]
    ny, nx = geom["shape"]
    ix = np.round((df21["x"] - geom["x0"]) / geom["dx"]).astype(int)
    iy = np.round((df21["y"] - geom["y0"]) / geom["dy"]).astype(int)
    have = np.zeros((ny, nx), bool)
    ok = ours["pred_mean"].notna().to_numpy()
    have[ours["grid_iy"].to_numpy()[ok], ours["grid_ix"].to_numpy()[ok]] = True
    inside = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    df21 = df21.copy()
    df21["in_our_footprint"] = inside & have[np.clip(iy, 0, ny - 1), np.clip(ix, 0, nx - 1)]
    return df21


def ecdf(values):
    """Sorted values and their cumulative percent (0-100, as in the 2021 figures)."""
    v = np.sort(np.asarray(values, float)[np.isfinite(values)])
    return v, 100.0 * np.arange(1, len(v) + 1) / len(v)


def plot_cdf(ax, values, color, ls, label, lw=2.0, alpha=1.0):
    x, y = ecdf(values)
    ax.plot(x, y, color=color, linestyle=ls, linewidth=lw, alpha=alpha,
            label=f"{label} (n={len(x):,})")
    return len(x)


def plot_band(ax, mu, sd, color, label=None):
    """68% predictive band: the CDFs of mu-sigma and mu+sigma, filled between.

    Same construction as the 2021 notebook (fill_betweenx on sorted low/high).
    """
    mu, sd = np.asarray(mu, float), np.asarray(sd, float)
    ok = np.isfinite(mu) & np.isfinite(sd)
    lo, p = ecdf(mu[ok] - sd[ok])
    hi, _ = ecdf(mu[ok] + sd[ok])
    ax.fill_betweenx(p, lo, hi, color=color, alpha=0.18, linewidth=0, label=label)


def gap_caption(ours) -> str:
    """One-line description of the Antarctic cells our grid cannot predict."""
    gap = ours["pred_mean"].isna()
    if not gap.any():
        return "Our grid predicts every Antarctic cell in the ice mask."
    return (f"our grid is missing {gap.mean():.1%} of Antarctic cells "
            f"({(ours.loc[gap, 'latitude'] < -84).mean():.0%} poleward of 84S)")


def figure_overall(ours, grl, df21, model, out_path):
    """Fig. 2a analogue: predicted-RSSNR CDFs over the full extent of each product.

    Predictions only — the observation curves are sampled along flight lines and on
    the 2021 side are a ~10% subsample of 1.7k cells, so they add scatter without
    adding a comparison. The footprint-restricted variant lives in the printed
    summary and `schroeder2021_benchmark.csv` instead of a second panel.

    Our Greenland prediction is drawn for context; the 2021 work is Antarctica-only,
    so there is no 2021 counterpart to compare it against.
    """
    fig, ax = plt.subplots(figsize=(9, 6))
    plot_band(ax, ours["pred_mean"], ours["pred_std"], C_ANT,
              "ours, Antarctica: 68% predictive interval")
    plot_band(ax, grl["pred_mean"], grl["pred_std"], C_GRL,
              "ours, Greenland: 68% predictive interval")
    plot_band(ax, df21["snr_pred"], df21["snr_pred_std"], C_OTHER,
              "2021: 68% predictive interval")
    plot_cdf(ax, ours["pred_mean"], C_ANT, DASH_ANT, f"ours, Antarctica ({model})")
    plot_cdf(ax, grl["pred_mean"], C_GRL, DASH_GRL, f"ours, Greenland ({model})")
    plot_cdf(ax, df21["snr_pred"], C_OTHER, DASH_2021,
             "Schroeder et al. (2021), Antarctica")
    style_axis(ax)
    ax.set_xlim(0, 140)
    ax.set_ylim(0, 100)
    ax.set_xlabel("required surface SNR [dB]", color=INK)
    ax.set_ylabel("cumulative percent of the bed [%]", color=INK)
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")

    fig.suptitle(
        "CDF of predicted required surface SNR — ours vs Schroeder et al. (2021)\n"
        "blue = Antarctica, green = Greenland, orange = the 2021 export\n"
        "(external reference, Antarctica only); dashed = prediction,\n"
        f"shading = 68% predictive interval; {gap_caption(ours)}",
        color=INK, fontsize=9.5, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out_path}")


def figure_classes(ours, df21, model, out_path):
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.6), sharey=True)
    panels = [
        (axes[0], f"ours ({model})", ours, "class", "pred_mean", "pred_std"),
        (axes[1], "Schroeder et al. (2021)", df21, "class", "snr_pred", "snr_pred_std"),
    ]
    for ax, title, df, ckey, mu_col, sd_col in panels:
        for cls in CLASSES:
            sub = df[df[ckey] == cls]
            color = CLASS_COLOR[cls]
            plot_band(ax, sub[mu_col], sub[sd_col], color)
            plot_cdf(ax, sub[mu_col], color, CLASS_DASH[cls], cls)
        style_axis(ax)
        ax.set_xlim(0, 140)
        ax.set_ylim(0, 100)
        ax.set_xlabel("required surface SNR [dB]", color=INK)
        ax.set_title(title, color=INK, fontsize=11)
        ax.legend(frameon=False, fontsize=9, loc="lower right")
    axes[0].set_ylabel("cumulative percent [%]", color=INK)

    fig.suptitle(
        "Required surface SNR by flotation mask and surface velocity — ours vs 2021\n"
        "Colour encodes class, not ice sheet (both panels are Antarctica only): "
        "orange = ice shelf, purple = fast, brown = slow. Dashed = model prediction, "
        "shading = 68% predictive interval.\n"
        f"Fast/slow split at {V_THRESHOLD:.0f} m/yr (the 2021 threshold); {gap_caption(ours)}",
        color=INK, fontsize=10.5)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out_path}")


def summarize(name, values, cls):
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    row = {"source": name, "class": cls, "n": len(v)}
    row.update({f"p{p}_dB": (np.percentile(v, p) if len(v) else np.nan) for p in PCTS})
    row.update({f"frac_le_{s}dB": (np.mean(v <= s) if len(v) else np.nan) for s in SNR_LEVELS})
    return row


def build_table(ours, df21):
    series = [
        ("ours_prediction", ours, "pred_mean", "class"),
        ("ours_observations", ours, "required_surface_snr_dB", "class"),
        ("2021_prediction", df21, "snr_pred", "class"),
        ("2021_prediction_our_footprint", df21[df21["in_our_footprint"]], "snr_pred", "class"),
        ("2021_training_data", df21, "snr", "class"),
    ]
    rows = []
    for name, df, col, ckey in series:
        rows.append(summarize(name, df[col], "All Antarctica"))
        for cls in CLASSES:
            rows.append(summarize(name, df.loc[df[ckey] == cls, col], cls))
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="atten_refl")
    args = parser.parse_args()
    out_dir = Path("outputs/model/analysis")
    out_dir.mkdir(parents=True, exist_ok=True)

    ours = load_ours(args.model)
    grl = load_ours(args.model, sheet="greenland")
    df21 = tag_footprint(load_2021(), ours)

    figure_overall(ours, grl, df21, args.model, out_dir / "cdf_antarctica_vs_2021.png")
    figure_classes(ours, df21, args.model, out_dir / "cdf_antarctica_icemask_vs_2021.png")

    table = build_table(ours, df21)
    table.to_csv(out_dir / "schroeder2021_benchmark.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(table.round(3).to_string(index=False))
    print(f"\nwrote {out_dir / 'schroeder2021_benchmark.csv'}")

    # Caveats and sensitivities worth reporting alongside the table.
    gap = ours["pred_mean"].isna()
    print(f"\nAntarctic grid points: {len(ours):,}; no prediction: {gap.sum():,} ({gap.mean():.1%})"
          f" — of those, {(ours.loc[gap, 'latitude'] < -84).mean():.0%} are poleward of 84S")
    print(f"velocity ({ours.attrs['velocity_column']}) missing on "
          f"{ours['v_m_yr'].isna().mean():.1%} of our grid")
    print(f"2021 cells inside our footprint: {df21['in_our_footprint'].mean():.1%}; "
          f"median 2021 prediction {df21['snr_pred'].median():.2f} dB full vs "
          f"{df21.loc[df21['in_our_footprint'], 'snr_pred'].median():.2f} dB restricted")
    print(f"Our non-detections excluded from the observed CDF: "
          f"{int(ours['is_nondetect'].sum()):,} Antarctic grid points")
    for thr in (25.0, 50.0, 100.0, 200.0):
        o = ours.assign(c=classify(ours["bedmachine_mask"], ours["v_m_yr"], thr))
        t = df21.assign(c=classify(df21["mask"], df21["v"], thr))
        med = lambda d, col, c: d.loc[d["c"] == c, col].median()  # noqa: E731
        print(f"v threshold {thr:6.0f} m/yr | ours fast {med(o, 'pred_mean', CLASSES[1]):6.2f} "
              f"slow {med(o, 'pred_mean', CLASSES[2]):6.2f} | 2021 fast "
              f"{med(t, 'snr_pred', CLASSES[1]):6.2f} slow {med(t, 'snr_pred', CLASSES[2]):6.2f} dB")


if __name__ == "__main__":
    main()
