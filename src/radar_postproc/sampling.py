"""CRS-aware point-in-raster sampling, shared by all dataset plugins.

Every plugin reports its own CRS; the sampler builds one pyproj Transformer from
EPSG:4326 (the trace points) to the raster CRS per call, then samples.

  method="bilinear"  -> xarray.interp, for continuous fields (elevation, velocity)
  method="nearest"   -> nearest grid cell, for categorical fields (e.g. mask)

Out-of-bounds points return NaN (bilinear) or NaN-castable fill (nearest).
"""

import numpy as np
import rasterio
import xarray as xr
from pyproj import Transformer


def _find_xy_dims(da: xr.DataArray) -> tuple[str, str]:
    """Return (x_dim, y_dim) names for a 2-D spatial DataArray."""
    candidates = {
        "x": ["x", "easting", "X"],
        "y": ["y", "northing", "Y"],
    }
    xname = next((c for c in candidates["x"] if c in da.dims), None)
    yname = next((c for c in candidates["y"] if c in da.dims), None)
    if xname is None or yname is None:
        # Fall back to rioxarray's notion of spatial dims.
        try:
            xname, yname = da.rio.x_dim, da.rio.y_dim
        except Exception as e:
            raise ValueError(f"Could not identify x/y dims in {da.dims}") from e
    return xname, yname


def sample_raster(
    da: xr.DataArray,
    lon: np.ndarray,
    lat: np.ndarray,
    raster_crs: str,
    method: str = "bilinear",
) -> np.ndarray:
    """Sample a 2-D georeferenced DataArray at lon/lat points (EPSG:4326).

    Parameters
    ----------
    da : 2-D DataArray with projected x/y coordinates in ``raster_crs``.
    lon, lat : 1-D arrays of point coordinates in degrees (EPSG:4326).
    raster_crs : CRS of the raster's x/y coords, e.g. "EPSG:3031".
    method : "bilinear" (continuous) or "nearest" (categorical).
    """
    lon = np.asarray(lon, dtype="float64")
    lat = np.asarray(lat, dtype="float64")

    transformer = Transformer.from_crs("EPSG:4326", raster_crs, always_xy=True)
    px, py = transformer.transform(lon, lat)

    xdim, ydim = _find_xy_dims(da)
    xq = xr.DataArray(px, dims="points")
    yq = xr.DataArray(py, dims="points")

    if method == "bilinear":
        sampled = da.interp({xdim: xq, ydim: yq}, method="linear")
    elif method == "nearest":
        sampled = da.interp({xdim: xq, ydim: yq}, method="nearest")
    else:
        raise ValueError(f"Unknown sampling method: {method!r}")

    return np.asarray(sampled.values, dtype="float64")


def sample_cog(
    src: str,
    lon: np.ndarray,
    lat: np.ndarray,
    method: str = "bilinear",
    band: int = 1,
) -> np.ndarray:
    """Windowed point-sample a (Cloud-Optimized) GeoTIFF, local path or remote URL.

    Reads only the pixels each point needs (rasterio block cache / vsicurl range
    requests), so a multi-GB COG never lands fully in memory. The raster's own CRS
    and nodata are read from the file. Points outside the raster -> NaN.

    method="nearest"  -> one pixel per point
    method="bilinear" -> the 4 surrounding pixel centres, fractionally weighted
    """
    lon = np.asarray(lon, dtype="float64")
    lat = np.asarray(lat, dtype="float64")

    # vsicurl tuning for remote COGs; harmless for local files.
    env = rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                       CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")
    with env, rasterio.open(src) as ds:
        tx = Transformer.from_crs("EPSG:4326", ds.crs.to_string(), always_xy=True)
        x, y = tx.transform(lon, lat)

        # Fractional pixel-centre coordinates (assumes north-up, no rotation).
        a, _, c, _, e, f = ds.transform.a, 0, ds.transform.c, 0, ds.transform.e, ds.transform.f
        col = (x - c) / a - 0.5  # 0 at the centre of pixel 0
        row = (y - f) / e - 0.5

        nodata = ds.nodata
        H, W = ds.height, ds.width

        def read_at(ic, ir):
            ic = np.asarray(ic)
            ir = np.asarray(ir)
            inb = (ic >= 0) & (ic < W) & (ir >= 0) & (ir < H)
            out = np.full(ic.shape, np.nan)
            if inb.any():
                coords = [
                    (c + (cc + 0.5) * a, f + (rr + 0.5) * e)
                    for cc, rr in zip(ic[inb], ir[inb])
                ]
                vals = np.array([v[band - 1] for v in ds.sample(coords, indexes=[band])],
                                dtype="float64")
                if nodata is not None:
                    vals[vals == nodata] = np.nan
                out[inb] = vals
            return out

        if method == "nearest":
            return read_at(np.rint(col).astype(int), np.rint(row).astype(int))

        if method == "bilinear":
            c0 = np.floor(col).astype(int)
            r0 = np.floor(row).astype(int)
            fc = col - c0
            fr = row - r0
            v00 = read_at(c0, r0)
            v10 = read_at(c0 + 1, r0)
            v01 = read_at(c0, r0 + 1)
            v11 = read_at(c0 + 1, r0 + 1)
            top = v00 * (1 - fc) + v10 * fc
            bot = v01 * (1 - fc) + v11 * fc
            return top * (1 - fr) + bot * fr

        raise ValueError(f"Unknown sampling method: {method!r}")
