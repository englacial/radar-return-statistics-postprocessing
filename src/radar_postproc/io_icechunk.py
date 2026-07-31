"""Read the input icechunk store at a pinned snapshot and build base trace points.

Ports the make_storage pattern from radar_return_statistics/store.py:19. Reads are
always pinned to an explicit snapshot_id (never branch="main") so output is
reproducible.
"""

import logging

import geopandas as gpd
import icechunk
import numpy as np
import pandas as pd
import zarr
from xarray.coding.times import decode_cf_datetime

logger = logging.getLogger(__name__)


def make_storage(store_config: dict) -> icechunk.Storage:
    """Create icechunk Storage from config (local or S3).

    The opr-radar-metrics stores are public, and this is a read-only consumer, so
    S3 reads are **anonymous** by default — no AWS credentials required. Set
    ``store.anonymous: false`` to fall back to the AWS credential chain (from_env)
    for a private store.
    """
    backend = store_config.get("backend", "local")
    if backend == "s3":
        auth = {"anonymous": True} if store_config.get("anonymous", True) else {"from_env": True}
        return icechunk.s3_storage(
            bucket=store_config["s3_bucket"],
            prefix=store_config.get("s3_prefix"),
            region=store_config.get("s3_region"),
            **auth,
        )
    return icechunk.local_filesystem_storage(str(store_config["path"]))


def resolve_snapshot(store_config: dict, branch: str = "main") -> str:
    """Return the latest snapshot id on a branch (helper for pinning a config)."""
    repo = icechunk.Repository.open(storage=make_storage(store_config))
    return next(iter(repo.ancestry(branch=branch))).id


def _open_root(store_config: dict, snapshot_id: str) -> zarr.Group:
    repo = icechunk.Repository.open(storage=make_storage(store_config))
    session = repo.readonly_session(snapshot_id=snapshot_id)
    return zarr.open_group(session.store, mode="r")


def _decode_slow_time(root: zarr.Group) -> np.ndarray:
    st = root["slow_time"]
    units = st.attrs.get("units", "seconds since 1970-01-01")
    calendar = st.attrs.get("calendar", "proleptic_gregorian")
    return decode_cf_datetime(st[:], units, calendar)


def extract_points(
    store_config: dict,
    snapshot_id: str,
    carry_columns: list[str],
    qc_only: bool = True,
    max_traces: int | None = None,
    include_nondetections: bool = False,
) -> gpd.GeoDataFrame:
    """Open the pinned snapshot and return per-trace points as an EPSG:4326 GeoDataFrame.

    Each row is one decimated trace. ``carry_columns`` are radar variables copied
    straight into the output for context / sanity checks. Geometry is the lon/lat
    point; downstream samplers reproject from EPSG:4326 to each raster's CRS.
    """
    if not snapshot_id:
        raise ValueError("snapshot_id is required — reads must be pinned, not branch reads")

    root = _open_root(store_config, snapshot_id)

    lat = root["latitude"][:]
    lon = root["longitude"][:]
    n = len(lat)

    data: dict[str, np.ndarray] = {}
    for col in carry_columns:
        if col == "slow_time":
            data[col] = _decode_slow_time(root)
        elif col in root:
            arr = root[col][:]
            if arr.shape and arr.shape[0] == n:
                data[col] = arr
            else:
                logger.warning("Column %s shape %s != n_traces %d, skipping", col, arr.shape, n)
        else:
            logger.warning("Carry column %s not in store, skipping", col)

    # Derive the per-trace OPR season/campaign name. The store keeps it as a
    # `frame_collections` root attribute (parallel to `frame_names`), indexed by
    # the per-trace `frame_index` array — so frame_id alone doesn't carry it.
    collections = list(root.attrs.get("frame_collections", []) or [])
    if collections and "frame_index" in root:
        idx = root["frame_index"][:]
        data["collection"] = np.array(
            [collections[i] if i < len(collections) else "" for i in idx]
        )

    df = pd.DataFrame(data)
    if "latitude" not in df:
        df["latitude"] = lat
    if "longitude" not in df:
        df["longitude"] = lon

    if qc_only and "qc_pass" in root:
        mask = root["qc_pass"][:].astype(bool)
        if include_nondetections and all(
            k in root for k in ("qc_surface_pass", "bed_pick_attempted", "bed_pick_available")
        ):
            # Attempted-but-unpicked bed traces (non-detections) ride along with
            # the QC-passing picked traces; stores without the flags are
            # unaffected (all traces treated as picked).
            nondetect = (
                root["qc_surface_pass"][:].astype(bool)
                & root["bed_pick_attempted"][:].astype(bool)
                & ~root["bed_pick_available"][:].astype(bool)
            )
            logger.info("Including %d non-detection traces", int(nondetect.sum()))
            mask = mask | nondetect
        df = df[mask].reset_index(drop=True)
        lon, lat = lon[mask], lat[mask]

    if max_traces is not None and len(df) > max_traces:
        df = df.iloc[:max_traces].reset_index(drop=True)
        lon, lat = lon[:max_traces], lat[:max_traces]

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(lon, lat),
        crs="EPSG:4326",
    )
    logger.info("Extracted %d points from snapshot %s", len(gdf), snapshot_id)
    return gdf
