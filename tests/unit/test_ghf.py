"""Unit test for the ghf plugin's sample() mapping, no network.

Builds a synthetic regridded-style dataset (HF/HFmin/HFmax on an EPSG:3031 x/y
grid) and checks sample() emits the three uniform columns with exact bilinear
values and a coherent lower<=central<=upper envelope.
"""

import numpy as np
import xarray as xr
from pyproj import Transformer

from radar_postproc.datasets import get_dataset

CRS = "EPSG:3031"
X = np.linspace(-1_500_000, -1_000_000, 21)
Y = np.linspace(-500_000, 0, 21)


def _grid(offset):
    xx, yy = np.meshgrid(X, Y)
    # central field linear in projected coords; bounds are central -/+ a margin.
    return xr.DataArray(1e-3 * xx + 2e-3 * yy + offset, coords={"y": Y, "x": X}, dims=("y", "x"))


def _synthetic_ds():
    central = _grid(60.0)
    return xr.Dataset({"HF": central, "HFmin": central - 12.0, "HFmax": central + 12.0})


def _to_lonlat(px, py):
    inv = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    return inv.transform(px, py)


def test_ghf_sample_columns_and_envelope():
    ds = _synthetic_ds()
    plugin = get_dataset("ghf", region="antarctic")
    assert plugin.variables == ["ghf_mW_m2", "ghf_lower_mW_m2", "ghf_upper_mW_m2"]

    px = np.array([-1_300_000.0, -1_111_111.0])
    py = np.array([-300_000.0, -222_222.0])
    lon, lat = _to_lonlat(px, py)
    cols = plugin.sample(ds, lon, lat)

    # Source fields are in W/m^2; the plugin converts to mW/m^2 (x1000).
    expected_central = (1e-3 * px + 2e-3 * py + 60.0) * 1000.0
    np.testing.assert_allclose(cols["ghf_mW_m2"], expected_central, rtol=1e-6, atol=1e-3)
    np.testing.assert_allclose(cols["ghf_lower_mW_m2"], expected_central - 12_000.0, rtol=1e-6, atol=1e-3)
    np.testing.assert_allclose(cols["ghf_upper_mW_m2"], expected_central + 12_000.0, rtol=1e-6, atol=1e-3)
    assert np.all(cols["ghf_lower_mW_m2"] <= cols["ghf_mW_m2"])
    assert np.all(cols["ghf_mW_m2"] <= cols["ghf_upper_mW_m2"])


def test_ghf_region_picks_model_and_crs():
    assert get_dataset("ghf", region="antarctic").crs == "EPSG:3031"
    g = get_dataset("ghf", region="greenland")
    assert g.crs == "EPSG:3413" and g.ngrip is False
    assert "wNGRIP" in get_dataset("ghf", region="greenland", ngrip=True)._member
