"""Assemble the trace-level modeling table for the range-exponent Bayes fit.

Relaxed crossover-site search (>= 2 passes >= 200 m apart vertically, pairwise
lateral separation <= 1 km via a 500 m ball, single collection), partitioning
traces into DISJOINT sites: qualifying seeds are accepted greedily in order of
descending level count, each claiming the unclaimed traces in its 500 m ball.
A claimed neighborhood must still contain >= 2 separated levels to be accepted.

Surface gate QC: the surface sample is only valid if it comes from image 1 of
the waveform playlist, unblanked — T_blank <= surface_twtt <= T_end(img1) -
T_guard (see reference/OPR_Toolbox_Guide.md "img_comb fields" and
claude_notes/20260831-dc8-gain-investigation.md). Windows are read per
segment/season from outputs/multi_altitude_crossovers/img_comb_windows.csv
(built from the OPR param spreadsheets; _safe columns, guarded by Tpd), with
a global near-range saturation/blanking floor at SAT_FLOOR_S and an empirical
58 us cap for 2012_Antarctica_DC8 (load-time wf_adc_comb boundary invisible
to img_comb). Traces without a resolved window are flagged NOT ok. If the CSV
is absent, falls back to the older heuristic (<= 7.7 us any platform;
<= 72 us DC-8 high-altitude legs).

Usage: uv run python scripts/multi_altitude_crossovers/build_model_table.py
         [--cross-season]
  --cross-season: partition sites across ALL seasons of a store (sites may
  mix seasons; the fit must then include per-season calibration offsets).
  Writes model_table_cross.parquet instead.
Output: outputs/multi_altitude_crossovers/model_table[_cross].parquet
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

LATERAL_TOL_M = 1000.0   # max pairwise lateral separation (ball radius = /2)
MIN_VSEP_M = 200.0       # min height gap between consecutive levels
MIN_LEVELS = 2
H_RANGE_M = (50.0, 15000.0)
HIGH_ALT_M = 3000.0      # high/low altitude regime threshold (AGL)
ICE_N = 1.78             # refractive index of ice
LOW_GATE_S = 7.7e-6      # fallback surface gate, low-altitude configs
HIGH_GATE_S = 72e-6      # fallback surface gate, DC-8 high-altitude config
SAT_FLOOR_S = 2.5e-6     # near-range saturation/blanking guard (~375 m AGL)

STORES = {"antarctica": "EPSG:3031", "greenland": "EPSG:3413"}

COLS = ["latitude", "longitude", "elevation", "surface_elevation",
        "bed_elevation", "frame_id", "collection", "surface_power_dB",
        "bed_power_dB", "post_bed_noise_dB", "post_bed_noise_interp_dB",
        "surface_twtt"]


def greedy_levels(heights: np.ndarray) -> int:
    """Max number of heights with consecutive gaps >= MIN_VSEP_M."""
    n, last = 0, -np.inf
    for v in np.sort(heights):
        if v - last >= MIN_VSEP_M:
            n, last = n + 1, v
    return n


def n_levels_of(frames: np.ndarray, h: np.ndarray) -> int:
    if len(np.unique(frames)) < MIN_LEVELS:
        return 0
    med = pd.Series(h).groupby(frames).median()
    return greedy_levels(med.to_numpy())


def partition_sites(df: pd.DataFrame) -> np.ndarray:
    """Disjoint site labels per trace (-1 = unassigned) within one collection."""
    xy = df[["px", "py"]].to_numpy()
    frames = df["frame_id"].to_numpy()
    h = df["h_agl"].to_numpy()
    tree = cKDTree(xy)
    neighbors = tree.query_ball_point(xy, r=LATERAL_TOL_M / 2.0, workers=-1)

    n_lev = np.array([n_levels_of(frames[nb], h[nb]) for nb in neighbors])
    seeds = np.flatnonzero(n_lev >= MIN_LEVELS)
    labels = np.full(len(df), -1, dtype=int)
    site = 0
    for s in seeds[np.argsort(-n_lev[seeds], kind="stable")]:
        nb = np.array([j for j in neighbors[s] if labels[j] == -1])
        if len(nb) == 0 or n_levels_of(frames[nb], h[nb]) < MIN_LEVELS:
            continue
        labels[nb] = site
        site += 1
    return labels


def gate_qc(t: pd.DataFrame, windows_csv: Path) -> pd.Series:
    """True where the surface pick lies inside image 1's valid window."""
    if not windows_csv.exists():
        print(f"WARNING: {windows_csv} missing — falling back to heuristic gate")
        is_dc8_high = (t["regime"] == "high") & t["season"].str.contains("DC8")
        return ((t["surface_twtt"] <= LOW_GATE_S)
                | (is_dc8_high & (t["surface_twtt"] <= HIGH_GATE_S)))
    w = pd.read_csv(windows_csv)
    cols = ["season", "segment", "surf_valid_min_safe_s", "surf_valid_max_safe_s"]
    seg = t["frame_id"].str.rsplit("_", n=1).str[0]
    m = t[["season"]].assign(segment=seg).merge(w[cols], on=["season", "segment"],
                                               how="left")
    lo = m["surf_valid_min_safe_s"].clip(lower=SAT_FLOOR_S)
    hi = m["surf_valid_max_safe_s"]
    # empirical overrides (validation section of the gain investigation note):
    # 2012 DC-8 has a load-time wf_adc_comb boundary invisible to img_comb
    hi = hi.where(~(m["season"] == "2012_Antarctica_DC8").to_numpy(),
                  hi.clip(upper=58e-6))
    ok = (t["surface_twtt"].to_numpy() >= lo.to_numpy()) \
        & (t["surface_twtt"].to_numpy() <= hi.to_numpy())
    unresolved = lo.isna() | hi.isna()
    if unresolved.any():
        bad = t.loc[unresolved.to_numpy(), "season"].value_counts()
        print(f"no img_comb window (flagged not ok): {bad.to_dict()}")
    return pd.Series(ok & ~unresolved.to_numpy(), index=t.index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cross-season", action="store_true")
    args = ap.parse_args()
    out_dir = Path("outputs/multi_altitude_crossovers")
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = []
    for store, crs in STORES.items():
        df = pd.read_parquet(f"outputs/{store}/{store}.parquet", columns=COLS)
        df["h_agl"] = df["elevation"] - df["surface_elevation"]
        df = df[df["h_agl"].between(*H_RANGE_M)
                & df["latitude"].notna() & df["longitude"].notna()]
        tx = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        df["px"], df["py"] = tx.transform(df["longitude"].to_numpy(),
                                          df["latitude"].to_numpy())
        groups = ([("cross", df)] if args.cross_season
                  else df.groupby("collection"))
        for coll, sub in groups:
            sub = sub.reset_index(drop=True)
            labels = partition_sites(sub)
            sub = sub[labels >= 0].copy()
            if len(sub) == 0:
                continue
            sub["site_id"] = [f"{store}_{coll}_{k:04d}"
                              for k in labels[labels >= 0]]
            tables.append(sub.assign(sheet=store,
                                     season=sub["collection"].to_numpy()))
            print(f"  {store}/{coll}: {sub['site_id'].nunique()} sites, "
                  f"{len(sub)} traces")

    t = pd.concat(tables, ignore_index=True)
    t["r_surf"] = t["h_agl"]
    thickness = t["surface_elevation"] - t["bed_elevation"]
    t["r_bed_geom"] = t["elevation"] - t["bed_elevation"]
    t["r_bed_refr"] = t["r_surf"] + thickness / ICE_N
    noise = t["post_bed_noise_interp_dB"].fillna(t["post_bed_noise_dB"])
    t["bed_margin_dB"] = t["bed_power_dB"] - noise
    t["n_levels"] = t.groupby("site_id")["h_agl"].transform(
        lambda g: n_levels_of(t.loc[g.index, "frame_id"].to_numpy(),
                              g.to_numpy()))
    t["regime"] = np.where(t["r_surf"] >= HIGH_ALT_M, "high", "low")
    t["surface_gate_ok"] = gate_qc(t, out_dir / "img_comb_windows.csv")

    keep = ["site_id", "sheet", "season", "frame_id", "latitude", "longitude",
            "r_surf", "r_bed_geom", "r_bed_refr", "surface_power_dB",
            "bed_power_dB", "bed_margin_dB", "n_levels", "regime",
            "surface_gate_ok"]
    t = t[keep]
    out = out_dir / ("model_table_cross.parquet" if args.cross_season
                     else "model_table.parquet")
    t.to_parquet(out, index=False)

    sites = t.drop_duplicates("site_id")
    print(f"\n{sites.shape[0]} sites, {len(t)} traces -> {out}")
    print("\nper sheet (sites / traces):")
    for sh, g in t.groupby("sheet"):
        print(f"  {sh}: {g['site_id'].nunique()} / {len(g)}")
    print("\nper season (sites / traces):")
    for se, g in t.groupby("season"):
        print(f"  {se}: {g['site_id'].nunique()} / {len(g)}")
    n3 = sites[sites["n_levels"] >= 3].shape[0]
    print(f"\nsites with >= 3 levels: {n3}")
    print(f"traces by regime: {t['regime'].value_counts().to_dict()}")
    print(f"surface_gate_ok: {t['surface_gate_ok'].sum()} of {len(t)}")


if __name__ == "__main__":
    main()
