"""Unit tests for the era5 plugin's coordinate handling, no network.

ERA5 fields come on longitude 0..360 with DESCENDING latitude. to_xy_field must
convert to -180..180, sort ascending, and rename to x/y so sample_raster samples
correctly at native trace lon/lat. A field linear in (lon_-180_180, lat) makes
bilinear interpolation exact, so we can assert analytically.
"""

import numpy as np
import xarray as xr

from radar_postproc.datasets.era5 import to_xy_field
from radar_postproc.sampling import sample_raster

# Synthetic global-ish grid: longitude 0..360 ascending, latitude DESCENDING.
LON360 = np.arange(0, 360, 30.0)            # 0,30,...,330
LAT = np.arange(80, -81, -20.0)             # 80,60,...,-80 (descending)


def _wrap180(lon360):
    return ((lon360 + 180) % 360) - 180


def _field(a=0.01, b=0.02, c=250.0):
    """value = a*lon(-180..180) + b*lat + c, on a 0..360 / descending-lat grid."""
    lon180 = _wrap180(LON360)
    vals = a * lon180[None, :] + b * LAT[:, None] + c
    return xr.DataArray(vals, coords={"latitude": LAT, "longitude": LON360},
                        dims=("latitude", "longitude")), (a, b, c)


def test_to_xy_field_normalizes_axes():
    da, _ = _field()
    out = to_xy_field(da)
    assert out.dims == ("y", "x")
    # x now spans -180..180 and is ascending; y ascending.
    assert out.x.min() >= -180 and out.x.max() < 180
    assert np.all(np.diff(out.x.values) > 0)
    assert np.all(np.diff(out.y.values) > 0)


def test_sampling_after_normalization_is_exact():
    da, (a, b, c) = _field()
    field = to_xy_field(da)
    # Sample at points in -180..180 / lat, away from the grid edges.
    lon = np.array([-75.0, -10.0, 100.0])
    lat = np.array([72.0, -33.0, 10.0])
    got = sample_raster(field, lon, lat, "EPSG:4326", method="bilinear")
    expected = a * lon + b * lat + c
    np.testing.assert_allclose(got, expected, rtol=1e-6, atol=1e-6)


def test_negative_longitudes_match_eastern_values():
    # A trace at lon -75 (Greenland) must read the same cell as 285 in 0..360.
    da, (a, b, c) = _field()
    field = to_xy_field(da)
    got = sample_raster(field, np.array([-75.0]), np.array([40.0]), "EPSG:4326")
    assert np.isclose(got[0], a * (-75.0) + b * 40.0 + c, rtol=1e-6)
