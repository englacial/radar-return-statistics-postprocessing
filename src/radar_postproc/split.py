"""Spatially-blocked split: attach the radar target to grid points, assign cells,
hold out hand-picked test cells, and distribute the rest into folds.

Modernizes the 2020 snr_paper preprocessing: same regular-square-cell blocking
(Roberts et al. 2017 rationale) and greedy capacity-limited fold assignment, but
cells are anchored at the projected origin (0, 0) — not the data extent — so
cell IDs are stable across data updates, and IDs are unique across ice sheets
("ant:-3:1", "grl:0:-5").

Target values come from the augment parquets: each grid point takes the nearest
radar observation within split.nn_cutoff_m (KD-tree, per sheet, ase+utig pooled
for the antarctic), NaN beyond — the 2020 nearest-neighbor semantics.
"""

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from pyproj import Transformer
from scipy.spatial import cKDTree

from .config import config_hash, load_model_config
from .datasets.base import REGION_CRS
from .output import read_run_id, write_stage_output
from .provenance import build_stage_manifest

logger = logging.getLogger(__name__)

SHEET_CODE = {"antarctic": "ant", "greenland": "grl"}


def cell_ids(x: np.ndarray, y: np.ndarray, sheet: str, cell_size_m: float) -> np.ndarray:
    """Stable blocking-cell IDs, anchored at the projected origin (0, 0)."""
    code = SHEET_CODE[sheet]
    ix = np.floor(np.asarray(x) / cell_size_m).astype(int)
    iy = np.floor(np.asarray(y) / cell_size_m).astype(int)
    return np.array([f"{code}:{i}:{j}" for i, j in zip(ix, iy)])


def nn_match(grid_xy: np.ndarray, obs_xy: np.ndarray, cutoff_m: float
             ) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-observation index within cutoff for each grid point.

    Returns (idx, dist_m); idx is -1 and dist_m NaN where nothing is in range.
    """
    idx_out = np.full(len(grid_xy), -1, dtype=int)
    dists = np.full(len(grid_xy), np.nan)
    if len(obs_xy) == 0:
        return idx_out, dists
    tree = cKDTree(obs_xy)
    dist, idx = tree.query(grid_xy, k=1, distance_upper_bound=cutoff_m)
    hit = np.isfinite(dist)
    idx_out[hit] = idx[hit]
    dists[hit] = dist[hit]
    return idx_out, dists


def assign_target_nn(
    grid_xy: np.ndarray,
    obs_xy: np.ndarray,
    obs_values: np.ndarray,
    cutoff_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest observation value within cutoff for each grid point, else NaN.

    Returns (values, dist_m); dist_m is NaN where no observation is in range.
    """
    idx, dists = nn_match(grid_xy, obs_xy, cutoff_m)
    values = np.full(len(grid_xy), np.nan)
    hit = idx >= 0
    values[hit] = np.asarray(obs_values)[idx[hit]]
    return values, dists


def assign_folds(cell_counts: dict[str, int], n_folds: int, seed: int) -> dict[str, int]:
    """Greedy capacity-limited random fold assignment at cell granularity.

    The 2020 algorithm: visit cells in a seeded random permutation, assign each
    to a uniformly random open fold; a fold closes once its observation count
    exceeds total/n_folds. One fix over 2020: the last fold never closes, so the
    assignment cannot run out of open folds. Cells with zero observations are
    skipped (left unassigned).
    """
    rng = np.random.default_rng(seed)
    cells = sorted(cell_counts)  # deterministic order before permutation
    total = sum(cell_counts[c] for c in cells)
    capacity = total / n_folds
    fold_sizes = [0] * n_folds
    open_folds = list(range(n_folds))
    assignment: dict[str, int] = {}
    for i in rng.permutation(len(cells)):
        cell = cells[i]
        if cell_counts[cell] == 0:
            continue
        fold = int(rng.choice(open_folds))
        assignment[cell] = fold
        fold_sizes[fold] += cell_counts[cell]
        if fold_sizes[fold] > capacity and len(open_folds) > 1:
            open_folds.remove(fold)
    return assignment


ICE_PERMITTIVITY = 3.17  # matches the upstream processing default
_C = 299_792_458.0


def compute_ceiling(surface_power_dB, surface_twtt, noise_dB, thickness_m,
                    permittivity: float = ICE_PERMITTIVITY):
    """Per-trace measurement ceiling: RSSNR if the bed peak equalled the noise floor.

    C = surface_power - noise + 20*log10(r_surf / r_bed_eff), with the effective
    bed range r_bed_eff = r_surf + thickness/sqrt(eps) (same geometric-spreading
    correction as the upstream RSSNR definition).
    """
    r_surf = _C * np.asarray(surface_twtt, dtype="float64") / 2.0
    r_bed_eff = r_surf + np.asarray(thickness_m, dtype="float64") / np.sqrt(permittivity)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (np.asarray(surface_power_dB, dtype="float64")
                - np.asarray(noise_dB, dtype="float64")
                + 20.0 * np.log10(r_surf / r_bed_eff))


_OBS_COLS = ["latitude", "longitude", "bed_power_dB", "post_bed_noise_dB",
             "post_bed_noise_interp_dB", "post_bed_peak_interp_dB",
             "surface_power_dB", "surface_twtt",
             "bed_pick_available", "bed_pick_attempted"]


def _load_observations(sheet: str, stores: list[str], target: str, out_dir: Path) -> pd.DataFrame:
    """Pooled attempted radar traces for a sheet, in its native projected CRS.

    Rows are picked observations OR non-detections (attempted, no bed pick —
    only present for stores augmented with include_nondetections). Carries:
      margin_dB  = bed_power - at-depth noise (picked traces; the saturation input)
      delta_dB   = at-depth window peak - median (non-detection classifier input)
    The at-depth pick-free reference (post_bed_noise_interp_dB) is preferred,
    falling back to post_bed_noise_dB for stores without it.
    """
    frames = []
    for store in stores:
        path = out_dir / store / f"{store}.parquet"
        have = set(pq.read_schema(path).names)
        cols = [c for c in [target, *_OBS_COLS] if c in have]
        df = pd.read_parquet(path, columns=cols)
        for c in [target, *_OBS_COLS]:
            if c not in df:
                df[c] = np.nan
        df["store"] = store
        frames.append(df)
    obs = pd.concat(frames, ignore_index=True)

    # Old stores carry no pick flags: rows with a target are picked observations.
    avail = obs["bed_pick_available"].astype("float64")
    attempted = obs["bed_pick_attempted"].astype("float64").fillna(0.0) > 0
    obs["picked"] = np.where(np.isnan(avail), obs[target].notna(), avail > 0)
    keep = (obs["picked"] & obs[target].notna()) | (~obs["picked"] & attempted)
    obs = obs[keep].reset_index(drop=True)

    noise_ref = obs["post_bed_noise_interp_dB"].fillna(obs["post_bed_noise_dB"])
    obs["noise_ref_dB"] = noise_ref
    obs["margin_dB"] = obs["bed_power_dB"] - noise_ref
    obs["delta_dB"] = obs["post_bed_peak_interp_dB"] - obs["post_bed_noise_interp_dB"]
    tx = Transformer.from_crs("EPSG:4326", REGION_CRS[sheet], always_xy=True)
    obs["x"], obs["y"] = tx.transform(obs["longitude"].to_numpy(), obs["latitude"].to_numpy())
    return obs


def _cells_summary(df: pd.DataFrame, target: str, cell_size_m: float) -> pd.DataFrame:
    rows = []
    for cell, group in df.groupby("cell_id"):
        _, ix, iy = cell.split(":")
        rows.append({
            "cell_id": cell,
            "ice_sheet": group["ice_sheet"].iloc[0],
            "x_min": int(ix) * cell_size_m,
            "y_min": int(iy) * cell_size_m,
            "n_grid_points": len(group),
            "n_obs": int(group[target].notna().sum()),
            "fold": int(group["fold"].iloc[0]),
            "is_test": bool(group["is_test"].iloc[0]),
        })
    return pd.DataFrame(rows).sort_values("cell_id")


def plot_cell_maps(df: pd.DataFrame, cells: pd.DataFrame, target: str,
                   cell_size_m: float, out_dir: Path) -> list[str]:
    """Per-sheet helper maps for hand-picking test cells."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for sheet, sheet_df in df.groupby("ice_sheet"):
        fig, ax = plt.subplots(figsize=(14, 14))
        no_obs = sheet_df[sheet_df[target].isna()]
        has_obs = sheet_df[sheet_df[target].notna()]
        ax.scatter(no_obs["x"], no_obs["y"], s=1, c="0.85", rasterized=True, label="ice, no obs")
        sc = ax.scatter(has_obs["x"], has_obs["y"], s=2, c=has_obs[target],
                        cmap="viridis", rasterized=True, label=target)
        fig.colorbar(sc, ax=ax, shrink=0.6, label=f"{target}")
        sheet_cells = cells[cells["ice_sheet"] == sheet]
        for _, row in sheet_cells.iterrows():
            x0, y0 = row["x_min"], row["y_min"]
            color = "tab:red" if row["is_test"] else "0.4"
            ax.add_patch(plt.Rectangle((x0, y0), cell_size_m, cell_size_m,
                                       fill=False, edgecolor=color, linewidth=1))
            ax.annotate(f"{row['cell_id']}\nn={row['n_obs']}",
                        (x0 + cell_size_m / 2, y0 + cell_size_m / 2),
                        ha="center", va="center", fontsize=7, color=color)
        ax.set_aspect("equal")
        ax.set_title(f"{sheet}: blocking cells ({cell_size_m/1000:.0f} km), "
                     f"{len(has_obs)} grid points with obs; red = test")
        ax.legend(loc="lower left", markerscale=5)
        path = out_dir / f"cells_{sheet}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        paths.append(str(path))
    return paths


def run_split(config_path: str, out_dir: str | None = None, repo_dir: str = ".") -> dict:
    config = load_model_config(config_path)
    out_dir = Path(out_dir or config["output"]["dir"])
    model_dir = out_dir / "model"
    split_cfg = config["split"]
    target = split_cfg["target"]
    cell_size_m = split_cfg["cell_size_km"] * 1000.0

    grid_path = model_dir / "grid.parquet"
    df = pd.read_parquet(grid_path)
    grid_manifest = json.loads((model_dir / "grid.manifest.json").read_text())

    # Attach the target per sheet (pooled stores, native CRS KD-tree).
    augment_run_ids: dict[str, str] = {}
    values = np.full(len(df), np.nan)
    margins = np.full(len(df), np.nan)
    dists = np.full(len(df), np.nan)
    nondetect = np.zeros(len(df), dtype=bool)
    nd_delta = np.full(len(df), np.nan)
    ceiling = np.full(len(df), np.nan)
    for sheet, stores in config["inputs"].items():
        for store in stores:
            augment_run_ids[store] = read_run_id(out_dir / store / f"{store}.parquet")
        obs = _load_observations(sheet, stores, target, out_dir)
        rows = (df["ice_sheet"] == sheet).to_numpy()
        # Nearest *attempted* trace: a grid point takes whichever attempted trace
        # is closest — a picked observation or a non-detection.
        idx, dist = nn_match(
            df.loc[rows, ["x", "y"]].to_numpy(),
            obs[["x", "y"]].to_numpy(),
            split_cfg["nn_cutoff_m"],
        )
        hit = idx >= 0
        m = obs.iloc[idx[hit]]
        picked = m["picked"].to_numpy()

        vals = np.full(len(idx), np.nan)
        marg = np.full(len(idx), np.nan)
        nd = np.zeros(len(idx), dtype=bool)
        ndd = np.full(len(idx), np.nan)
        ceil = np.full(len(idx), np.nan)
        vals[hit] = np.where(picked, m[target].to_numpy(), np.nan)
        marg[hit] = np.where(picked, m["margin_dB"].to_numpy(), np.nan)
        nd[hit] = ~picked
        ndd[hit] = np.where(~picked, m["delta_dB"].to_numpy(), np.nan)
        # Ceiling: for picked traces it's exactly target + margin; for
        # non-detections it's rebuilt from surface power, geometry (BedMachine
        # thickness at the grid point), and the pick-free at-depth noise.
        thickness = df.loc[rows, "bedmachine_thickness_m"].to_numpy()
        ceil_nd = compute_ceiling(m["surface_power_dB"].to_numpy(),
                                  m["surface_twtt"].to_numpy(),
                                  m["noise_ref_dB"].to_numpy(),
                                  thickness[hit])
        ceil[hit] = np.where(picked,
                             m[target].to_numpy() + m["margin_dB"].to_numpy(),
                             ceil_nd)

        values[rows] = vals
        margins[rows] = marg
        dists[rows] = dist
        nondetect[rows] = nd
        nd_delta[rows] = ndd
        ceiling[rows] = ceil
        logger.info("Split %s: %d/%d grid points matched (%d observed, %d non-detections; "
                    "cutoff %.0f m)", sheet, int(hit.sum()), int(rows.sum()),
                    int((nd[hit] == False).sum()), int(nd.sum()),  # noqa: E712
                    split_cfg["nn_cutoff_m"])
    df[target] = values
    df["obs_margin_dB"] = margins
    df["obs_dist_m"] = dists
    df["is_nondetect"] = nondetect
    df["nd_delta_dB"] = nd_delta
    df["C_dB"] = ceiling

    # Blocking cells, test set, folds.
    df["cell_id"] = ""
    for sheet in config["inputs"]:
        rows = df["ice_sheet"] == sheet
        df.loc[rows, "cell_id"] = cell_ids(
            df.loc[rows, "x"].to_numpy(), df.loc[rows, "y"].to_numpy(), sheet, cell_size_m)

    test_cells = list(split_cfg["test_cells"])
    if not test_cells:
        logger.warning("split.test_cells is empty — no held-out test set. "
                       "Pick cell IDs from %s/cell_maps and re-run split.", model_dir)
    unknown = set(test_cells) - set(df["cell_id"])
    if unknown:
        raise ValueError(f"test_cells not present in the grid: {sorted(unknown)}")
    df["is_test"] = df["cell_id"].isin(test_cells)

    obs_counts = df[df[target].notna() & ~df["is_test"]].groupby("cell_id").size()
    fold_map = assign_folds(obs_counts.to_dict(), split_cfg["n_folds"], split_cfg["seed"])
    df["fold"] = df["cell_id"].map(fold_map).fillna(-1).astype("int8")
    df.loc[df["is_test"], "fold"] = -1

    n_train = int(((df["fold"] >= 0) & df[target].notna()).sum())
    n_test = int((df["is_test"] & df[target].notna()).sum())
    fold_sizes = (df[df[target].notna() & (df["fold"] >= 0)]
                  .groupby("fold").size().to_dict())
    logger.info("Split: %d train points in %d folds %s, %d test points in %d test cells",
                n_train, split_cfg["n_folds"], fold_sizes, n_test, len(test_cells))

    cells = _cells_summary(df, target, cell_size_m)
    cells.to_csv(model_dir / "cells.csv", index=False)
    map_paths = plot_cell_maps(df, cells, target, cell_size_m, model_dir / "cell_maps")

    section_hash = config_hash({"inputs": config["inputs"], "split": split_cfg})
    grid_run_id = grid_manifest["run_id"]
    manifest = build_stage_manifest(
        "split", config, section_hash,
        input_ids=[grid_run_id, *augment_run_ids.values()],
        inputs={"grid_run_id": grid_run_id, "augment_run_ids": augment_run_ids},
        repo_dir=repo_dir,
        geometry=grid_manifest.get("geometry"),
        folds={str(k): int(v) for k, v in fold_sizes.items()},
        n_train_points=n_train,
        n_test_points=n_test,
    )
    paths = write_stage_output(df, manifest, model_dir / "split.parquet")
    paths["cells"] = str(model_dir / "cells.csv")
    paths["cell_maps"] = map_paths
    logger.info("Wrote %s (run_id=%s)", paths["parquet"], manifest["run_id"])
    return {"paths": paths, "manifest": manifest,
            "n_train_points": n_train, "n_test_points": n_test}
