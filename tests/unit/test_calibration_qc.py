"""Unit tests for the split-stage radiometric calibration QC rules."""

import numpy as np
import pandas as pd

from radar_postproc.config import load_model_config
from radar_postproc.split import apply_calibration_qc, calibration_qc_flags


def _qc(**overrides):
    qc = {"enabled": True, "max_seam_offset_dB": 3.0, "drop_unmeasured_seam": False,
          "require_img1_surface": True, "min_ceiling_margin_dB": 2.0}
    qc.update(overrides)
    return qc


def _obs():
    return pd.DataFrame({
        #                        clean  seam+  seam-  seamNaN  img2  img-1  sat   marginNaN
        "img_comb_offset_dB":      [0.5, 3.0,  -4.0,  np.nan,  1.0,  1.0,  1.0,  1.0],
        "surface_source_image_index": [1, 1,    1,     1,       2,    -1,   1,    1],
        "surface_ceiling_margin_dB": [5.0, 5.0, 5.0,   5.0,     5.0,  5.0,  1.9,  np.nan],
    })


def test_rules_flag_expected_rows():
    flags = calibration_qc_flags(_obs(), _qc())
    assert flags["seam"].tolist() == [False, True, True, False, False, False, False, False]
    assert flags["img2"].tolist() == [False, False, False, False, True, False, False, False]
    assert flags["saturated"].tolist() == [False] * 6 + [True, False]


def test_unmeasured_passes_by_default_and_can_be_dropped():
    kept, counts = apply_calibration_qc(_obs(), _qc())
    assert len(kept) == 4 and counts == {"seam": 2, "img2": 1, "saturated": 1,
                                         "any": 4, "n_before": 8}
    kept, counts = apply_calibration_qc(_obs(), _qc(drop_unmeasured_seam=True))
    assert len(kept) == 3 and counts["seam"] == 3


def test_img1_rule_optional():
    kept, _ = apply_calibration_qc(_obs(), _qc(require_img1_surface=False))
    assert len(kept) == 5


def test_missing_columns_reject_nothing():
    obs = pd.DataFrame({"bed_power_dB": [1.0, 2.0]})
    kept, counts = apply_calibration_qc(obs, _qc())
    assert len(kept) == 2 and counts["any"] == 0


def test_disabled_is_passthrough():
    obs = _obs()
    kept, counts = apply_calibration_qc(obs, _qc(enabled=False))
    assert kept is obs and counts == {}


def test_config_defaults(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("split: {calibration_qc: {enabled: true}}\n")
    qc = load_model_config(path)["split"]["calibration_qc"]
    assert qc == {"enabled": True, "max_seam_offset_dB": 3.0, "drop_unmeasured_seam": False,
                  "require_img1_surface": True, "min_ceiling_margin_dB": 2.0}
