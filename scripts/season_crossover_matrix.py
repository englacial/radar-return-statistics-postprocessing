"""Season-by-season crossover difference matrices for RSSNR, per ice sheet.

For every pair of seasons within a sheet, match traces at (near-)crossovers
(nearest neighbour within 500 m, EPSG:3031/3413), keep pairs where BOTH members
are unsaturated (margin > 15 dB over the at-depth noise floor), and summarize
delta = RSSNR_row - RSSNR_col as median and standard deviation. The diagonal is
within-season repeatability (matches restricted to a different frame).

Rendered as one annotated matrix per sheet: cell colour = median (PRGn
diverging, centred at 0), gray = fewer than MIN_PAIRS pairs.

Usage: uv run python scripts/season_crossover_matrix.py [--calibration-qc]
  --calibration-qc applies config/model.yaml's split.calibration_qc rules to the
  traces first (output suffix _qc), for a model-free before/after check of
  inter-season agreement.
"""

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from radar_postproc.config import load_model_config  # noqa: E402
from radar_postproc.split import apply_calibration_qc  # noqa: E402

from plot_style import INK  # noqa: E402

RADIUS_M = 500.0
MIN_MARGIN_DB = 15.0
MIN_PAIRS = 30
TARGET = "required_surface_snr_dB"
SHEETS = {"antarctica": ("outputs/antarctica/antarctica.parquet", "EPSG:3031"),
          "greenland": ("outputs/greenland/greenland.parquet", "EPSG:3413")}


def load_sheet(path: str, crs: str, qc: dict | None = None) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if qc is not None:
        df, counts = apply_calibration_qc(df, qc)
        print(f"{path}: calibration QC dropped {counts['any']}/{counts['n_before']} traces")
    noise = df.get("post_bed_noise_interp_dB")
    if noise is None or noise.isna().all():
        noise = df["post_bed_noise_dB"]
    noise = noise.fillna(df["post_bed_noise_dB"])
    df["margin"] = df["bed_power_dB"] - noise
    df = df[df[TARGET].notna() & (df["margin"] > MIN_MARGIN_DB)].copy()
    tx = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    df["px"], df["py"] = tx.transform(df["longitude"].to_numpy(), df["latitude"].to_numpy())
    return df.reset_index(drop=True)


def pair_deltas(a: pd.DataFrame, b: pd.DataFrame, same_season: bool) -> np.ndarray:
    """delta = RSSNR_a(nearest-matched) - RSSNR_b, one match per a-trace."""
    if len(a) == 0 or len(b) == 0:
        return np.array([])
    tree = cKDTree(b[["px", "py"]].to_numpy())
    if same_season:
        # exclude trivial along-track self matches: nearest neighbour in a
        # DIFFERENT frame; query several and take the first cross-frame hit
        dist, idx = tree.query(a[["px", "py"]].to_numpy(), k=8,
                               distance_upper_bound=RADIUS_M)
        af = a["frame_id"].to_numpy()
        bf = b["frame_id"].to_numpy()
        out = []
        for i in range(len(a)):
            for d, j in zip(np.atleast_1d(dist[i]), np.atleast_1d(idx[i])):
                if np.isfinite(d) and bf[j] != af[i]:
                    out.append(a[TARGET].iloc[i] - b[TARGET].iloc[j])
                    break
        return np.array(out)
    dist, idx = tree.query(a[["px", "py"]].to_numpy(), k=1, distance_upper_bound=RADIUS_M)
    hit = np.isfinite(dist)
    return a.loc[hit, TARGET].to_numpy() - b[TARGET].to_numpy()[idx[hit]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibration-qc", action="store_true")
    ap.add_argument("--out-dir", default="outputs/model/analysis")
    args = ap.parse_args()
    qc = None
    if args.calibration_qc:
        qc = {**load_model_config("config/model.yaml")["split"]["calibration_qc"], "enabled": True}
    suffix = "_qc" if qc else ""
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for sheet, (path, crs) in SHEETS.items():
        df = load_sheet(path, crs, qc)
        seasons = sorted(df["collection"].unique())
        n = len(seasons)
        med = np.full((n, n), np.nan)
        sd = np.full((n, n), np.nan)
        cnt = np.zeros((n, n), dtype=int)
        groups = {s: df[df["collection"] == s] for s in seasons}
        for i, si in enumerate(seasons):
            for j, sj in enumerate(seasons):
                if j < i:
                    continue  # fill antisymmetric half afterwards
                d = pair_deltas(groups[si], groups[sj], same_season=(i == j))
                if len(d) >= MIN_PAIRS:
                    med[i, j], sd[i, j], cnt[i, j] = np.median(d), np.std(d), len(d)
                    if i != j:
                        med[j, i], sd[j, i], cnt[j, i] = -med[i, j], sd[i, j], len(d)
                else:
                    cnt[i, j] = cnt[j, i] = len(d)

        fig, ax = plt.subplots(figsize=(1.1 * n + 3.5, 1.0 * n + 2.5))
        cmap = plt.get_cmap("PRGn").copy()
        cmap.set_bad("0.82")
        lim = np.nanmax(np.abs(med)) if np.isfinite(med).any() else 1.0
        im = ax.imshow(np.ma.masked_invalid(med), cmap=cmap, vmin=-lim, vmax=lim)
        for i in range(n):
            for j in range(n):
                if np.isfinite(med[i, j]):
                    ax.text(j, i, f"{med[i, j]:+.1f}\n±{sd[i, j]:.1f}",
                            ha="center", va="center", fontsize=8, color=INK)
                else:
                    ax.text(j, i, "—", ha="center", va="center", fontsize=8, color="0.55")
        short = [s.split("_", 1)[0] + " " + s.split("_")[-1] for s in seasons]
        ax.set_xticks(range(n), short, rotation=45, ha="right", fontsize=9)
        ax.set_yticks(range(n), short, fontsize=9)
        ax.set_title(f"{sheet}: RSSNR at season crossovers{' (calibration QC applied)' if qc else ''}"
                     " — median(row − col) ± sd [dB]\n"
                     f"pairs within {RADIUS_M:.0f} m, both margins > {MIN_MARGIN_DB:.0f} dB, "
                     f"min {MIN_PAIRS} pairs; diagonal = within-season (cross-frame)",
                     color=INK, fontsize=11)
        fig.colorbar(im, ax=ax, shrink=0.8, label="median Δ RSSNR [dB]")
        fig.tight_layout()
        out = out_dir / f"season_crossover_matrix_{sheet}{suffix}.png"
        fig.savefig(out, dpi=140, bbox_inches="tight")
        plt.close(fig)
        total_pairs = int(cnt[np.triu_indices(n)].sum())
        print(f"{sheet}: {n} seasons, {total_pairs} pairs total -> {out}")
        # Long-format table of the upper triangle (incl. diagonal) for numeric summaries.
        pd.DataFrame([{"row": seasons[i], "col": seasons[j], "median_dB": med[i, j],
                       "sd_dB": sd[i, j], "n_pairs": int(cnt[i, j])}
                      for i in range(n) for j in range(i, n)]
                     ).to_csv(out_dir / f"season_crossover_pairs_{sheet}{suffix}.csv", index=False)


if __name__ == "__main__":
    main()
