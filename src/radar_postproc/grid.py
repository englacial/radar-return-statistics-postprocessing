"""Full-ice-sheet covariate grid: the prediction domain for the train stage.

Builds a strided regular grid per ice sheet from the BedMachine mask (points sit
exactly on native pixel centres, mirroring the 2020 subsample semantics), then
samples every configured dataset plugin onto the grid points — the same plugins
and sample() interface the augment stage uses at trace locations.

Output: outputs/model/grid.parquet (both sheets, plain parquet — the two sheets
use different projected CRSs, so x/y are per-sheet native coordinates and
lat/lon serve cross-sheet joins). The manifest records each sheet's strided
raster geometry so predictions can be rasterized back into regular 2-D grids.
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from pyproj import Transformer

from .config import config_hash, load_model_config
from .datasets import get_dataset
from .output import write_stage_output
from .provenance import build_stage_manifest
from .sampling import _find_xy_dims

logger = logging.getLogger(__name__)


def _entry_region(entry: dict) -> str:
    return entry.get("region", "global")


def _instantiate(entry: dict):
    kwargs = {k: v for k, v in entry.items() if k != "name"}
    return get_dataset(entry["name"], **kwargs)


def build_grid_points(
    mask_da: xr.DataArray,
    stride: int,
    mask_values: list[int],
    crs: str,
) -> tuple[pd.DataFrame, dict]:
    """Strided ice-only grid points from a BedMachine-style mask DataArray.

    Returns (points, geometry): points has grid_ix/grid_iy (indices into the
    strided raster), x/y (native CRS), latitude/longitude; geometry describes
    the strided raster ({shape, x0, y0, dx, dy, crs, stride}) for rasterizing
    predictions back into 2-D arrays.
    """
    xdim, ydim = _find_xy_dims(mask_da)
    sub = mask_da.isel({xdim: slice(None, None, stride), ydim: slice(None, None, stride)})
    sub = sub.transpose(ydim, xdim)
    xs = np.asarray(sub[xdim].values, dtype="float64")
    ys = np.asarray(sub[ydim].values, dtype="float64")
    ice = np.isin(np.asarray(sub.values), mask_values)
    iy, ix = np.nonzero(ice)

    x = xs[ix]
    y = ys[iy]
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(x, y)
    points = pd.DataFrame({
        "grid_ix": ix.astype("int32"),
        "grid_iy": iy.astype("int32"),
        "x": x,
        "y": y,
        "longitude": lon,
        "latitude": lat,
    })
    geometry = {
        "shape": [len(ys), len(xs)],
        "x0": float(xs[0]),
        "y0": float(ys[0]),
        "dx": float(xs[1] - xs[0]) if len(xs) > 1 else None,
        "dy": float(ys[1] - ys[0]) if len(ys) > 1 else None,
        "crs": crs,
        "stride": stride,
    }
    return points, geometry


def sample_covariates(entries: list[dict], sheet: str, lon: np.ndarray, lat: np.ndarray,
                      cache_dir: Path, preopened: dict | None = None):
    """Sample every dataset entry valid for `sheet` at the given points.

    `preopened` maps id(entry) -> (plugin, path, ds) for datasets already fetched
    (the BedMachine mask source). Returns (columns, infos, sampling_info).
    """
    preopened = preopened or {}
    columns: dict[str, np.ndarray] = {}
    infos: dict[str, dict] = {}
    sampling_info: dict = {}
    for entry in entries:
        if _entry_region(entry) not in ("global", sheet):
            continue
        if id(entry) in preopened:
            plugin, path, ds = preopened[id(entry)]
        else:
            plugin = _instantiate(entry)
            logger.info("Grid %s: fetching %s", sheet, plugin.name)
            path = plugin.fetch(cache_dir)
            ds = plugin.open(path)
        cols = plugin.sample(ds, lon, lat)
        columns.update(cols)
        infos[plugin.name] = plugin.source_info(path)
        sampling_info.update(plugin.sampling_info())
        logger.info("Grid %s: %s -> %s", sheet, plugin.name, list(cols))
    return columns, infos, sampling_info


def run_grid(config_path: str, out_dir: str | None = None, repo_dir: str = ".") -> dict:
    config = load_model_config(config_path)
    out_dir = out_dir or config["output"]["dir"]
    cache_dir = Path(out_dir) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    grid_cfg = config["grid"]

    frames = []
    geometries: dict[str, dict] = {}
    infos: dict[str, dict] = {}
    sampling_info: dict = {}
    for sheet in config["inputs"]:
        entries = [e for e in grid_cfg["datasets"] if _entry_region(e) in ("global", sheet)]
        bm_entry = next((e for e in entries if e["name"] == "bedmachine"), None)
        if bm_entry is None:
            raise ValueError(f"grid.datasets needs a bedmachine entry for sheet {sheet!r} "
                             "(its mask defines the grid)")

        # BedMachine first: its mask defines the grid.
        bm = _instantiate(bm_entry)
        bm_path = bm.fetch(cache_dir)
        bm_ds = bm.open(bm_path)
        if "mask" not in bm_ds:
            raise KeyError(f"BedMachine {sheet} dataset has no 'mask' variable "
                           "(add 'mask' to its variables)")
        mask_da = bm_ds["mask"]
        xdim, _ = _find_xy_dims(mask_da)
        xcoord = np.asarray(mask_da[xdim].values, dtype="float64")
        native_res = float(abs(xcoord[1] - xcoord[0]))
        stride = max(1, round(grid_cfg["resolution_m"] / native_res))

        points, geometry = build_grid_points(mask_da, stride, grid_cfg["mask_values"], bm.crs)
        geometry["native_resolution_m"] = native_res
        geometry["resolution_m"] = native_res * stride
        geometries[sheet] = geometry
        logger.info("Grid %s: %d ice points at %.0f m (stride %d, native %.0f m)",
                    sheet, len(points), native_res * stride, stride, native_res)

        columns, sheet_infos, sheet_sampling = sample_covariates(
            entries, sheet,
            points["longitude"].to_numpy(), points["latitude"].to_numpy(),
            cache_dir, preopened={id(bm_entry): (bm, bm_path, bm_ds)},
        )
        for col, values in columns.items():
            points[col] = values
        infos.update(sheet_infos)
        sampling_info.update(sheet_sampling)
        points.insert(0, "ice_sheet", sheet)
        frames.append(points)
        bm_ds.close()

    df = pd.concat(frames, ignore_index=True)

    section_hash = config_hash({"inputs": config["inputs"], "grid": grid_cfg})
    dataset_infos = list(infos.values())
    dataset_hashes = [i["sha256"] for i in dataset_infos if i.get("sha256")]
    manifest = build_stage_manifest(
        "grid", config, section_hash,
        input_ids=dataset_hashes,
        inputs={"datasets": dataset_infos},
        repo_dir=repo_dir,
        geometry=geometries,
        sampling=sampling_info,
    )
    paths = write_stage_output(df, manifest, Path(out_dir) / "model" / "grid.parquet")
    logger.info("Wrote %s (%d grid points, run_id=%s)",
                paths["parquet"], len(df), manifest["run_id"])
    return {"paths": paths, "manifest": manifest, "n_points": len(df)}
