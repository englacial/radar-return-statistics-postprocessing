"""Unit tests for detection-model pieces: ceiling formula and the delta filter."""

import numpy as np
import pandas as pd

from radar_postproc.split import compute_ceiling
from radar_postproc.train import select_nondetects


class TestComputeCeiling:
    def test_reduces_to_power_ratio_at_zero_thickness(self):
        # No ice -> no extra spreading: C = surface_power - noise.
        c = compute_ceiling(-50.0, 10e-6, -160.0, 0.0)
        assert np.isclose(c, 110.0)

    def test_thicker_ice_lowers_ceiling(self):
        thin = compute_ceiling(-50.0, 10e-6, -160.0, 500.0)
        thick = compute_ceiling(-50.0, 10e-6, -160.0, 3000.0)
        assert thick < thin < 110.0

    def test_known_geometry(self):
        # r_surf = 1500 m (10 us), 1000 m ice at eps=3.17 -> r_bed_eff = 1500 + 561.7
        surface_twtt = 2 * 1500.0 / 299_792_458.0
        c = compute_ceiling(0.0, surface_twtt, -100.0, 1000.0, permittivity=3.17)
        expected = 100.0 + 20 * np.log10(1500.0 / (1500.0 + 1000.0 / np.sqrt(3.17)))
        assert np.isclose(c, expected)

    def test_vectorized_and_nan(self):
        c = compute_ceiling(np.array([-50.0, -50.0]), np.array([10e-6, 10e-6]),
                            np.array([-160.0, np.nan]), np.array([1000.0, 1000.0]))
        assert np.isfinite(c[0]) and np.isnan(c[1])


class TestSelectNondetects:
    def _nd(self):
        return pd.DataFrame({
            "C_dB": [80.0, 90.0, 100.0, np.nan],
            "nd_delta_dB": [4.0, 12.0, np.nan, 5.0],
        })

    def test_filter_on_keeps_low_delta_only(self):
        cfg = {"delta_filter": {"enabled": True, "max_dB": 8.0}}
        used, excluded = select_nondetects(self._nd(), cfg)
        assert list(used["C_dB"]) == [80.0]  # low delta, finite ceiling
        assert excluded == 3  # high delta, NaN delta, NaN ceiling

    def test_filter_off_keeps_all_with_ceiling(self):
        cfg = {"delta_filter": {"enabled": False, "max_dB": 8.0}}
        used, excluded = select_nondetects(self._nd(), cfg)
        assert len(used) == 3  # only the NaN-ceiling row drops
        assert excluded == 1
