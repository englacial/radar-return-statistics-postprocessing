"""Unit tests for sampling.sample_cog against a synthetic local GeoTIFF.

Linear field -> bilinear is exact. Nearest-at-centre returns the pixel value.
Out-of-bounds and nodata -> NaN.
"""

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from radar_postproc.sampling import sample_cog

CRS = "EPSG:3031"
RES = 1000.0  # 1 km pixels
ORIGIN_X, ORIGIN_Y = -1_500_000.0, 0.0  # upper-left
NX, NY = 40, 30
NODATA = -9999.0


def _pixel_center_xy(col, row):
    x = ORIGIN_X + (col + 0.5) * RES
    y = ORIGIN_Y - (row + 0.5) * RES
    return x, y


@pytest.fixture
def cog(tmp_path):
    # value = linear in projected coords so bilinear is exact.
    cols, rows = np.meshgrid(np.arange(NX), np.arange(NY))
    xc = ORIGIN_X + (cols + 0.5) * RES
    yc = ORIGIN_Y - (rows + 0.5) * RES
    data = (1e-3 * xc + 2e-3 * yc).astype("float32")
    data[0, 0] = NODATA  # one nodata cell
    path = tmp_path / "synthetic.tif"
    transform = from_origin(ORIGIN_X, ORIGIN_Y, RES, RES)
    with rasterio.open(
        path, "w", driver="GTiff", height=NY, width=NX, count=1,
        dtype="float32", crs=CRS, transform=transform, nodata=NODATA,
    ) as ds:
        ds.write(data, 1)
    return str(path), data


def _to_lonlat(x, y):
    inv = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    return inv.transform(x, y)


def test_nearest_at_centres(cog):
    path, data = cog
    cols, rows = [10, 20, 35], [5, 15, 25]
    xs, ys = _pixel_center_xy(np.array(cols), np.array(rows))
    lon, lat = _to_lonlat(xs, ys)
    got = sample_cog(path, lon, lat, method="nearest")
    np.testing.assert_allclose(got, data[rows, cols], rtol=1e-5)


def test_bilinear_linear_field_exact(cog):
    path, _ = cog
    # Points between cell centres; bilinear of a linear field is exact.
    xs = np.array([ORIGIN_X + 10.3 * RES, ORIGIN_X + 22.7 * RES])
    ys = np.array([ORIGIN_Y - 8.6 * RES, ORIGIN_Y - 14.2 * RES])
    lon, lat = _to_lonlat(xs, ys)
    got = sample_cog(path, lon, lat, method="bilinear")
    expected = 1e-3 * xs + 2e-3 * ys
    np.testing.assert_allclose(got, expected, rtol=1e-4)


def test_out_of_bounds_and_nodata_nan(cog):
    path, _ = cog
    # Far outside the grid + the nodata cell centre.
    xs = np.array([ORIGIN_X - 100 * RES, ORIGIN_X + 0.5 * RES])
    ys = np.array([ORIGIN_Y - 0.5 * RES, ORIGIN_Y - 0.5 * RES])  # second is cell (0,0)=nodata
    lon, lat = _to_lonlat(xs, ys)
    got = sample_cog(path, lon, lat, method="nearest")
    assert np.isnan(got[0])
    assert np.isnan(got[1])
