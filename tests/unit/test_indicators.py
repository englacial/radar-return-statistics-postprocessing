"""Indicator builders appended raw (un-scaled) to the design matrix."""

import pandas as pd
import pytest

from radar_postproc.train import add_indicator_columns

DF = pd.DataFrame({
    "ice_sheet": ["antarctic", "antarctic", "antarctic", "greenland", "greenland"],
    "bedmachine_mask": [2.0, 3.0, 4.0, 3.0, 4.0],
    "institution": ["CReSIS", "UTIG", "CReSIS", "CReSIS", "CReSIS"],
})


def test_is_floating_is_mask_3_only():
    """Only BedMachine mask 3 is floating — mask 2 and 4 are both grounded."""
    df = DF.copy()
    add_indicator_columns(df, ["is_floating"])
    assert df["is_floating"].tolist() == [0.0, 1.0, 0.0, 1.0, 0.0]


def test_is_greenland_marks_the_sheet():
    df = DF.copy()
    add_indicator_columns(df, ["is_greenland"])
    assert df["is_greenland"].tolist() == [0.0, 0.0, 0.0, 1.0, 1.0]


def test_unknown_indicator_rejected():
    with pytest.raises(ValueError, match="Unknown train.indicators"):
        add_indicator_columns(DF.copy(), ["is_martian"])
