"""Export the trained RSSNR model to packed binaries for the web tool.

Joins predictions.zarr (pred_mean/pred_std) with grid.parquet (thickness, mask)
and writes one gzipped, valid-cells-only buffer per ice sheet plus a meta.json.

    uv run python mission_design_tool/build_data.py [--model atten_refl] [--stride 1]
"""

import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from radar_postproc.sampling import _find_xy_dims

REPO = Path(__file__).resolve().parent.parent
SHEETS = ["antarctic", "greenland"]

# Fixed-point scales; keep in sync with app.js
MU_SCALE = 10.0    # int16, dB * 10
SD_SCALE = 4.0     # uint8, dB * 4  (0-63.75 dB)


def load_sheet(model_dir: Path, grid: pd.DataFrame, sheet: str, stride: int):
    ds = xr.open_zarr(model_dir / "predictions.zarr", group=sheet)
    if stride > 1:
        ds = ds.isel(x=slice(None, None, stride), y=slice(None, None, stride))
    mu = ds["pred_mean"].values
    sd = ds["pred_std"].values
    x = ds["x"].values
    y = ds["y"].values
    ny, nx = mu.shape

    # grid.parquet rows carry native (unstrided) raster indices.
    g = grid[grid["ice_sheet"] == sheet]
    ix, iy = g["grid_ix"].to_numpy(), g["grid_iy"].to_numpy()
    thk_r = np.full((ny, nx), np.nan)
    mask_r = np.zeros((ny, nx), dtype="uint8")
    keep = (ix % stride == 0) & (iy % stride == 0)
    jx, jy = ix[keep] // stride, iy[keep] // stride
    thk_r[jy, jx] = g["bedmachine_thickness_m"].to_numpy()[keep]
    mask_r[jy, jx] = np.nan_to_num(g["bedmachine_mask"].to_numpy()[keep]).astype("uint8")

    valid = np.isfinite(mu) & np.isfinite(sd) & np.isfinite(thk_r) & (mask_r > 0)
    fy, fx = np.nonzero(valid)
    idx = (fy.astype("uint32") * nx + fx).astype("uint32")

    buf = pack(idx,
               np.clip(mu[valid] * MU_SCALE, -32768, 32767).astype("int16"),
               np.clip(sd[valid] * SD_SCALE, 0, 255).astype("uint8"),
               np.clip(thk_r[valid], 0, 65535).astype("uint16"),
               mask_r[valid])

    geom = {
        "shape": [int(ny), int(nx)],
        "x0": float(x[0]), "y0": float(y[0]),
        "dx": float(x[1] - x[0]), "dy": float(y[1] - y[0]),
        "crs": "EPSG:3031" if sheet == "antarctic" else "EPSG:3413",
        "n": int(valid.sum()),
    }
    return buf, geom


def pack(idx, mu, sd, thk, mask) -> bytes:
    """Struct-of-arrays: idx(u32) | thk(u16) | mu(i16) | sd(u8) | mask(u8).

    Widest first so every field's byte offset stays naturally aligned for a
    JS TypedArray view regardless of the (possibly odd) cell count.
    """
    return b"".join(a.tobytes() for a in (idx, thk, mu, sd, mask))


# Readable labels for the covariate datasets recorded in the grid manifest.
# Falls back to the raw dataset name, so a new plugin still shows up.
SOURCE_LABELS = {
    "bedmachine_antarctic": "BedMachine Antarctica v{version}",
    "bedmachine_greenland": "BedMachine Greenland v{version}",
    "measures_phase_vel_antarctic": "MEaSUREs phase-based ice velocity",
    "itslive_greenland": "ITS_LIVE ice velocity v{version}",
    "era5_t2m": "ERA5 2 m air temperature ({version})",
    "ghf_antarctic": "Geothermal heat flow, Antarctica",
    "ghf_greenland": "Geothermal heat flow, Greenland",
}


def source_labels(grid_manifest: dict) -> list[str]:
    out = []
    for d in grid_manifest.get("inputs", {}).get("datasets", []):
        name = d.get("name", "")
        label = next((v for k, v in SOURCE_LABELS.items() if name.startswith(k)), name)
        out.append(label.format(version=d.get("version", "")))
    return out


def simplify(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker, iterative so long coastlines can't blow the stack."""
    n = len(pts)
    if n < 3:
        return pts
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        seg = pts[j] - pts[i]
        norm = np.hypot(*seg)
        rel = pts[i + 1:j] - pts[i]
        if norm == 0:
            d = np.hypot(rel[:, 0], rel[:, 1])
        else:
            d = np.abs(rel[:, 0] * seg[1] - rel[:, 1] * seg[0]) / norm
        k = int(np.argmax(d))
        if d[k] > tol:
            k += i + 1
            keep[k] = True
            stack += [(i, k), (k, j)]
    return pts[keep]


def _contours(z: np.ndarray, tol: float = 0.6, min_pts: int = 6,
              min_extent: float = 3.0) -> list[list[float]]:
    """Simplified 0.5-level contours of `z`, as flat [x0,y0,x1,y1,...] lists.

    NaN cells are skipped by contourpy, so masking a region out keeps its
    boundary from producing a contour.
    """
    from contourpy import contour_generator

    out = []
    for line in contour_generator(z=z).lines(0.5):
        pts = simplify(np.asarray(line, dtype="float64"), tol)
        if len(pts) < min_pts:
            continue
        if max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])) < min_extent:   # drop specks
            continue
        out.append([round(float(v), 1) for v in pts.reshape(-1)])
    return out


def outlines(mask_da, stride: int) -> dict:
    """Coastline and grounding line in prediction-raster pixel coordinates.

    The mask is strided exactly as grid.py strides it, so contouring in index
    space lands directly on the raster the browser draws — no projection maths
    in the client, and the overlay cannot drift from the data underneath it.

    coast     contour of `mask > 0`, the convention used by
              scripts/prediction_summary_figures.py.
    grounding boundary between grounded ice (2) and floating ice (3), with ocean,
              rock and Lake Vostok / non-Greenland land (0, 1, 4) masked out so
              the calving front does not come back as a duplicate coastline.
    """
    xdim, ydim = _find_xy_dims(mask_da)
    sub = mask_da.isel({xdim: slice(None, None, stride),
                        ydim: slice(None, None, stride)}).transpose(ydim, xdim)
    m = np.asarray(sub.values)

    ground = np.full(m.shape, np.nan, dtype="float64")
    ground[m == 2] = 1.0
    ground[m == 3] = 0.0
    return {
        "coast": _contours((m > 0).astype("float64")),
        "grounding": _contours(ground, min_extent=2.0),
    }


def build_coastlines(model_config: dict, cache_dir: Path, resolution_m: float) -> dict:
    """One coastline per sheet, from the same BedMachine products the grid used."""
    from radar_postproc.datasets import get_dataset

    region_sheet = {"antarctic": "antarctic", "greenland": "greenland"}
    coast = {}
    for entry in model_config["grid"]["datasets"]:
        if entry.get("name") != "bedmachine":
            continue
        sheet = region_sheet[entry["region"]]
        plugin = get_dataset("bedmachine",
                             **{k: v for k, v in entry.items() if k != "name"})
        ds = plugin.open(plugin.fetch(cache_dir))
        mask = ds["mask"]
        xdim, _ = _find_xy_dims(mask)
        xs = np.asarray(mask[xdim].values, dtype="float64")
        native = float(abs(xs[1] - xs[0]))
        stride = max(1, round(resolution_m / native))
        coast[sheet] = outlines(mask, stride)
        for kind, segs in coast[sheet].items():
            n = sum(len(x) // 2 for x in segs)
            print(f"{sheet}: {kind} {len(segs)} segments, {n:,} points "
                  f"(stride {stride}, native {native:.0f} m)")
        ds.close()
    return coast


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="atten_refl")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-coastlines", dest="coastlines", action="store_false",
                    help="skip the BedMachine coastline / grounding-line overlay")
    args = ap.parse_args()

    model_dir = REPO / "outputs" / "model" / args.model
    out = Path(args.out) if args.out else Path(__file__).parent / "data"
    out.mkdir(parents=True, exist_ok=True)

    grid = pd.read_parquet(REPO / "outputs" / "model" / "grid.parquet")
    metrics = json.loads((model_dir / "metrics.json").read_text())
    manifest = json.loads((model_dir / "manifest.json").read_text())
    grid_manifest = json.loads((REPO / "outputs" / "model" / "grid.manifest.json").read_text())

    meta = {
        "model": args.model,
        "run_id": manifest["run_id"],
        "created_at": manifest["created_at"],
        "stride": args.stride,
        "resolution_m": 5000 * args.stride,
        "mu_scale": MU_SCALE,
        "sd_scale": SD_SCALE,
        "detection": {"theta_dB": metrics["detection"]["theta_mean_dB"],
                      "tau_dB": metrics["detection"]["tau_mean_dB"]},
        "cv_rmse_dB": metrics["pooled_cv"]["rmse_dB"]["mean"],
        "features": metrics["features"],
        "sources": source_labels(grid_manifest),
        "sheets": {},
    }
    for sheet in SHEETS:
        buf, geom = load_sheet(model_dir, grid, sheet, args.stride)
        blob = gzip.compress(buf, 9)
        (out / f"{sheet}.bin.gz").write_bytes(blob)
        meta["sheets"][sheet] = {**geom, "bytes": len(buf), "gz_bytes": len(blob)}
        print(f"{sheet}: {geom['n']:,} cells, {len(buf)/1e6:.2f} MB -> "
              f"{len(blob)/1e6:.2f} MB gz")

    if args.coastlines:
        cache_dir = REPO / "outputs" / "cache"
        coast = build_coastlines(manifest["config"], cache_dir, meta["resolution_m"])
        raw = json.dumps(coast, separators=(",", ":")).encode()
        blob = gzip.compress(raw, 9)
        (out / "coast.json.gz").write_bytes(blob)
        meta["outlines"] = {
            "bytes": len(raw), "gz_bytes": len(blob),
            "segments": {s: {k: len(v) for k, v in kinds.items()}
                         for s, kinds in coast.items()},
        }
        print(f"outlines: {len(raw)/1e6:.2f} MB -> {len(blob)/1e6:.2f} MB gz")

    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote {out}/meta.json")


if __name__ == "__main__":
    main()
