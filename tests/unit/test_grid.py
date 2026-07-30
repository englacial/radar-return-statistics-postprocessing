"""Unit tests for the grid stage's pure grid-building logic."""

import numpy as np
import pandas as pd
import xarray as xr

from radar_postproc.grid import build_grid_points


def _mask_da(nx=20, ny=30, res=100.0, x0=-1000.0, y0=2000.0):
    """Synthetic BedMachine-style mask: y descending, ice block in the middle."""
    x = x0 + res * np.arange(nx)
    y = y0 - res * np.arange(ny)  # descending, like BedMachine
    mask = np.zeros((ny, nx), dtype="int8")  # ocean
    mask[5:20, 4:16] = 2   # grounded ice
    mask[20:25, 4:16] = 3  # floating ice
    mask[2, 2] = 1         # ice-free land (excluded)
    return xr.DataArray(mask, dims=("y", "x"), coords={"x": x, "y": y})


def test_stride_one_keeps_all_ice_points():
    da = _mask_da()
    points, geometry = build_grid_points(da, stride=1, mask_values=[2, 3, 4], crs="EPSG:3031")
    assert len(points) == 15 * 12 + 5 * 12
    assert geometry["shape"] == [30, 20]
    assert geometry["dx"] == 100.0
    assert geometry["dy"] == -100.0
    assert geometry["crs"] == "EPSG:3031"


def test_points_sit_on_native_pixel_centres():
    da = _mask_da()
    points, geometry = build_grid_points(da, stride=3, mask_values=[2, 3], crs="EPSG:3031")
    # Every x/y must be an original coordinate value (strided isel, no averaging).
    assert set(points["x"]).issubset(set(da.x.values))
    assert set(points["y"]).issubset(set(da.y.values))
    # grid_ix/grid_iy index into the strided raster.
    xs = da.x.values[::3]
    ys = da.y.values[::3]
    assert np.allclose(xs[points["grid_ix"]], points["x"])
    assert np.allclose(ys[points["grid_iy"]], points["y"])
    assert geometry["shape"] == [len(ys), len(xs)]
    assert geometry["stride"] == 3


def test_mask_values_filter():
    da = _mask_da()
    grounded, _ = build_grid_points(da, stride=1, mask_values=[2], crs="EPSG:3031")
    both, _ = build_grid_points(da, stride=1, mask_values=[2, 3], crs="EPSG:3031")
    assert len(grounded) == 15 * 12
    assert len(both) > len(grounded)
    # Land (1) and ocean (0) never included.
    assert not ((grounded["x"] == da.x.values[2]) & (grounded["y"] == da.y.values[2])).any()


def test_lonlat_roundtrip():
    da = _mask_da(res=10000.0, x0=-100000.0, y0=100000.0)
    points, _ = build_grid_points(da, stride=1, mask_values=[2, 3], crs="EPSG:3031")
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", "EPSG:3031", always_xy=True).transform(
        points["longitude"], points["latitude"])
    assert np.allclose(x, points["x"], atol=1e-6)
    assert np.allclose(y, points["y"], atol=1e-6)


def test_transposed_input_handled():
    da = _mask_da().transpose("x", "y")
    points, geometry = build_grid_points(da, stride=1, mask_values=[2, 3], crs="EPSG:3031")
    assert geometry["shape"] == [30, 20]
    assert len(points) == 15 * 12 + 5 * 12
    assert isinstance(points, pd.DataFrame)
