"""Unit tests for sampling.sample_raster against synthetic CRS-tagged rasters.

A bilinear interpolation of a field that is linear in projected coords is exact,
so we can assert against an analytic value computed via an independent pyproj
transform. Nearest-at-node round-trips the forward+inverse transform.
"""

import numpy as np
import pytest
import xarray as xr
from pyproj import Transformer

from radar_postproc.sampling import sample_raster

CRS = "EPSG:3031"
# A regular grid in projected meters somewhere over Antarctica.
X = np.linspace(-1_500_000, -1_000_000, 11)
Y = np.linspace(-500_000, 0, 11)


def _linear_field(a=1e-3, b=2e-3, c=10.0):
    xx, yy = np.meshgrid(X, Y)  # (y, x)
    return xr.DataArray(a * xx + b * yy + c, coords={"y": Y, "x": X}, dims=("y", "x")), (a, b, c)


def _to_lonlat(px, py):
    inv = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    return inv.transform(px, py)


def test_bilinear_linear_field_is_exact():
    da, (a, b, c) = _linear_field()
    # Sample at interior projected points; bilinear of a linear field is exact.
    px = np.array([-1_300_000.0, -1_111_111.0, -1_050_000.0])
    py = np.array([-300_000.0, -222_222.0, -25_000.0])
    lon, lat = _to_lonlat(px, py)
    got = sample_raster(da, lon, lat, CRS, method="bilinear")
    expected = a * px + b * py + c
    np.testing.assert_allclose(got, expected, rtol=1e-6, atol=1e-6)


def test_nearest_at_nodes_returns_node_value():
    rng = np.random.default_rng(0)
    vals = rng.normal(size=(len(Y), len(X)))
    da = xr.DataArray(vals, coords={"y": Y, "x": X}, dims=("y", "x"))
    # Pick a few exact grid nodes.
    iy, ix = [2, 5, 8], [1, 6, 9]
    px = X[ix]
    py = Y[iy]
    lon, lat = _to_lonlat(px, py)
    got = sample_raster(da, lon, lat, CRS, method="nearest")
    expected = vals[iy, ix]
    np.testing.assert_allclose(got, expected, rtol=1e-9, atol=1e-9)


def test_out_of_bounds_is_nan():
    da, _ = _linear_field()
    # A point near the South Pole projects far outside the grid bounds.
    got = sample_raster(da, np.array([0.0]), np.array([-89.9]), CRS, method="bilinear")
    assert np.isnan(got[0])


def test_unknown_method_raises():
    da, _ = _linear_field()
    with pytest.raises(ValueError):
        sample_raster(da, np.array([0.0]), np.array([-75.0]), CRS, method="cubic")
