"""Train-set z-score normalization, mirroring the 2020 build_normalizer_function.

Stats are computed only from the rows passed to fit_normalizer (the training
folds), so validation/test/grid predictions never leak into the scaling.
"""

import numpy as np
import pandas as pd


def fit_normalizer(df: pd.DataFrame, columns: list[str]) -> dict[str, dict[str, float]]:
    """Per-column mean/std from the given (training) rows."""
    norm = {}
    for col in columns:
        values = df[col].to_numpy(dtype="float64")
        std = float(np.nanstd(values))
        norm[col] = {"mean": float(np.nanmean(values)), "std": std if std > 0 else 1.0}
    return norm


def apply_normalizer(values: np.ndarray, stats: dict[str, float]) -> np.ndarray:
    return (np.asarray(values, dtype="float64") - stats["mean"]) / stats["std"]


def invert_normalizer(values: np.ndarray, stats: dict[str, float]) -> np.ndarray:
    """Map z-scored values back to physical units."""
    return np.asarray(values, dtype="float64") * stats["std"] + stats["mean"]


def invert_scale(values: np.ndarray, stats: dict[str, float]) -> np.ndarray:
    """Map a z-scored *spread* (std) back to physical units (no mean shift)."""
    return np.asarray(values, dtype="float64") * stats["std"]
