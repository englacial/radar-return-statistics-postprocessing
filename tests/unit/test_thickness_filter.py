import geopandas as gpd
import numpy as np

from radar_postproc.runner import filter_min_thickness


def _gdf():
    return gpd.GeoDataFrame({
        "surface_elevation": [1000.0, 1000.0, 1000.0, np.nan],
        "bed_elevation": [950.0, 900.0, 0.0, 0.0],
    }, geometry=gpd.points_from_xy([0, 1, 2, 3], [0, 0, 0, 0]))


def test_drops_thin_ice():
    out = filter_min_thickness(_gdf(), 100.0)
    assert len(out) == 3  # 50 m dropped; 100 m kept (boundary); 1000 m kept; NaN kept
    assert (out["bed_elevation"] != 950.0).all()


def test_none_keeps_all():
    assert len(filter_min_thickness(_gdf(), None)) == 4
