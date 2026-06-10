"""Integration + reproducibility tests on a synthetic local icechunk store.

Builds a tiny icechunk store with 100 traces along a known Antarctic transect and
a synthetic continuous raster, then exercises extract -> sample -> manifest with a
local dataset plugin. No network.
"""

import numpy as np
import pytest
import xarray as xr
import zarr
from pyproj import Transformer

from radar_postproc.config import config_hash
from radar_postproc.io_icechunk import extract_points
from radar_postproc.provenance import compute_run_id
from radar_postproc.sampling import sample_raster

pytestmark = pytest.mark.integration

N = 100
CRS = "EPSG:3031"


@pytest.fixture
def synthetic_store(tmp_path):
    """A local icechunk store: 100 traces, all qc_pass, with a known lat/lon transect."""
    import icechunk

    # Transect across the Amundsen Sea Embayment.
    lon = np.linspace(-110.0, -100.0, N)
    lat = np.linspace(-75.0, -74.0, N)

    storage = icechunk.local_filesystem_storage(str(tmp_path / "store"))
    repo = icechunk.Repository.create(storage=storage)
    session = repo.writable_session("main")
    root = zarr.open_group(session.store, mode="a")
    for name, data in {
        "latitude": lat,
        "longitude": lon,
        "bed_elevation": np.linspace(-500.0, -1500.0, N),
        "qc_pass": np.ones(N, dtype="int8"),
    }.items():
        root.create_array(name, data=data, chunks=(N,))
    root.create_array("frame_id", data=np.array(["Data_test_001"] * N, dtype="U32"), chunks=(N,))
    snapshot_id = session.commit("synthetic test store")

    store_config = {"backend": "local", "path": str(tmp_path / "store")}
    return store_config, snapshot_id, lon, lat


@pytest.fixture
def synthetic_raster():
    """Continuous raster in EPSG:3031 covering the transect; value = linear ramp."""
    inv = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    px, py = inv.transform(np.array([-115.0, -95.0]), np.array([-76.0, -73.0]))
    x = np.linspace(min(px) - 1e5, max(px) + 1e5, 50)
    y = np.linspace(min(py) - 1e5, max(py) + 1e5, 50)
    xx, yy = np.meshgrid(x, y)
    da = xr.DataArray(1e-3 * xx + 2e-3 * yy, coords={"y": y, "x": x}, dims=("y", "x"))
    return da


def test_extract_and_sample_no_nan(synthetic_store, synthetic_raster):
    store_config, snapshot_id, lon, lat = synthetic_store
    gdf = extract_points(store_config, snapshot_id, carry_columns=["bed_elevation", "qc_pass"])
    assert len(gdf) == N
    assert "bed_elevation" in gdf.columns
    assert gdf.crs.to_epsg() == 4326

    vals = sample_raster(synthetic_raster, gdf.geometry.x, gdf.geometry.y, CRS, method="bilinear")
    assert vals.shape == (N,)
    assert not np.isnan(vals).any()  # all points inside raster bounds


def test_run_id_is_deterministic(synthetic_store):
    store_config, snapshot_id, lon, lat = synthetic_store
    cfg = {"a": 1, "datasets": [{"name": "x"}]}
    h = config_hash(cfg)
    dataset_hashes = ["deadbeef", "cafef00d"]
    r1 = compute_run_id(snapshot_id, h, dataset_hashes)
    r2 = compute_run_id(snapshot_id, h, list(reversed(dataset_hashes)))
    assert r1 == r2  # order-independent
    assert len(r1) == 12
