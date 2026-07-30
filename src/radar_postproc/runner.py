"""In-process orchestration: extract -> fetch -> sample -> merge -> manifest.

This is the reference path the Snakemake rules also call into (one function per
stage), so the same code runs whether driven directly or by Snakemake.
"""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np

from .config import load_config
from .datasets import get_dataset
from .datasets.base import REGION_CRS  # noqa: F401  (kept for plugins/tests)
from .io_icechunk import extract_points
from .output import write_output
from .provenance import build_manifest

logger = logging.getLogger(__name__)


def _store_name(config: dict) -> str:
    prefix = config.get("store", {}).get("s3_prefix") or config.get("store", {}).get("path", "store")
    return Path(str(prefix)).name


def filter_min_thickness(gdf: gpd.GeoDataFrame, min_thickness_m: float | None) -> gpd.GeoDataFrame:
    """Drop traces with radar-derived thickness (surface - bed elevation) below the cutoff.

    Traces with NaN thickness are kept (no evidence they are thin).
    """
    if min_thickness_m is None:
        return gdf
    thickness = gdf["surface_elevation"] - gdf["bed_elevation"]
    keep = ~(thickness < min_thickness_m)
    dropped = int((~keep).sum())
    if dropped:
        logger.info("Dropped %d/%d traces with thickness < %.0f m",
                    dropped, len(gdf), min_thickness_m)
    return gdf[keep]


def extract_stage(config: dict) -> gpd.GeoDataFrame:
    snapshot_id = config["icechunk"]["snapshot_id"]
    ex = config["extract"]
    gdf = extract_points(
        config["store"],
        snapshot_id=snapshot_id,
        carry_columns=ex["carry_columns"],
        qc_only=ex["qc_only"],
        max_traces=ex["max_traces"],
    )
    return filter_min_thickness(gdf, ex["min_thickness_m"])


def sample_stage(config: dict, gdf: gpd.GeoDataFrame, cache_dir: Path):
    """Fetch + sample each configured dataset. Returns (columns, infos, sampling_info)."""
    lon = gdf.geometry.x.to_numpy()
    lat = gdf.geometry.y.to_numpy()
    columns: dict[str, np.ndarray] = {}
    infos: list[dict] = []
    sampling_info: dict = {}

    for entry in config["datasets"]:
        name = entry["name"]
        kwargs = {k: v for k, v in entry.items() if k != "name"}
        plugin = get_dataset(name, **kwargs)
        logger.info("Dataset %s: fetching", plugin.name)
        path = plugin.fetch(cache_dir)
        ds = plugin.open(path)
        cols = plugin.sample(ds, lon, lat)
        columns.update(cols)
        infos.append(plugin.source_info(path))
        sampling_info.update(plugin.sampling_info())
        logger.info("Dataset %s: produced columns %s", plugin.name, list(cols))

    return columns, infos, sampling_info


def run_pipeline(config_path: str, out_dir: str | None = None, repo_dir: str = ".") -> dict:
    config = load_config(config_path)
    if not config["icechunk"]["snapshot_id"]:
        raise ValueError("config.icechunk.snapshot_id must be set (pin the snapshot)")

    out_dir = out_dir or config["output"]["dir"]
    cache_dir = Path(out_dir) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    gdf = extract_stage(config)
    columns, infos, sampling_info = sample_stage(config, gdf, cache_dir)
    for col, values in columns.items():
        gdf[col] = values

    manifest = build_manifest(
        config,
        snapshot_id=config["icechunk"]["snapshot_id"],
        dataset_infos=infos,
        sampling_info=sampling_info,
        repo_dir=repo_dir,
    )
    # Record which OPR seasons/campaigns the traces came from (provenance).
    if "collection" in gdf:
        manifest["icechunk"]["collections"] = sorted(gdf["collection"].unique().tolist())
    paths = write_output(gdf, manifest, out_dir, _store_name(config))
    logger.info("Wrote %s (%d traces, run_id=%s)", paths["parquet"], len(gdf), manifest["run_id"])
    return {"paths": paths, "manifest": manifest, "n_traces": len(gdf)}
