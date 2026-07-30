"""Bayesian linear regression baseline: mu = alpha + beta . X.

X is the z-scored feature matrix with the raw is_greenland indicator appended
(so its beta is the pooled model's per-sheet offset). Weakly-informative
Normal(0, 1) priors on the z-scored scale, HalfNormal(1) noise — the 2020
model_bayesian_linear structure, ported to PyMC v5.
"""

import numpy as np

from . import register_model
from .base import BaseBayesianModel


@register_model
class LinearModel(BaseBayesianModel):
    name = "linear"

    def build(self, X: np.ndarray, y: np.ndarray, feature_names: list[str],
              upper: np.ndarray | None = None):
        import pymc as pm

        coords = {"feature": feature_names}
        with pm.Model(coords=coords) as model:
            Xd = pm.Data("X", X)
            alpha = pm.Normal("alpha", mu=0, sigma=1)
            beta = pm.Normal("beta", mu=0, sigma=1, dims="feature")
            sigma = pm.HalfNormal("sigma", sigma=1)
            self._likelihood(pm, alpha + Xd @ beta, sigma, y, upper)
        return model

    def mu_draws(self, posterior, X_new: np.ndarray) -> np.ndarray:
        alpha = posterior["alpha"].values          # (S,)
        beta = posterior["beta"].values            # (S, k)
        return alpha[:, None] + beta @ np.asarray(X_new, dtype="float64").T
