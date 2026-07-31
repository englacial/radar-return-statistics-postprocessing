"""Attenuation-rate x thickness structure from the 2020 snr_paper:

    mu = atten_rate * thickness - refl
    atten_rate = alpha_a + beta_a . covariates
    refl       = alpha_r + beta_r . covariates

The multiplicative interaction models total attenuation as a depth-averaged
*rate* times thickness; reflectivity enters as an additive offset. Thickness and
covariates are z-scored (signs/scales absorbed into the coefficients); the
covariates include the raw is_greenland indicator appended by train.py.
"""

import numpy as np

from . import register_model
from .base import BaseBayesianModel


@register_model
class AttenReflModel(BaseBayesianModel):
    name = "atten_refl"

    def __init__(self, features: list[str] | None = None,
                 thickness_feature: str = "bedmachine_thickness_m"):
        super().__init__(features)
        self.thickness_feature = thickness_feature

    def _split_columns(self) -> tuple[int, list[int]]:
        names = self.feature_names  # recorded by BaseBayesianModel.fit
        if self.thickness_feature not in names:
            raise ValueError(f"atten_refl needs {self.thickness_feature!r} among features {names}")
        t = names.index(self.thickness_feature)
        return t, [i for i in range(len(names)) if i != t]

    def build(self, X: np.ndarray, y: np.ndarray, feature_names: list[str],
              upper: np.ndarray | None = None, detection: dict | None = None):
        import pymc as pm

        self.feature_names = list(feature_names)
        t_idx, cov_idx = self._split_columns()
        coords = {"covariate": [feature_names[i] for i in cov_idx]}
        with pm.Model(coords=coords) as model:
            thickness = pm.Data("thickness", X[:, t_idx])
            covariates = pm.Data("covariates", X[:, cov_idx])
            alpha_a = pm.Normal("alpha_atten", mu=0, sigma=1)
            beta_a = pm.Normal("beta_atten", mu=0, sigma=1, dims="covariate")
            alpha_r = pm.Normal("alpha_refl", mu=0, sigma=1)
            beta_r = pm.Normal("beta_refl", mu=0, sigma=1, dims="covariate")
            atten_rate = alpha_a + covariates @ beta_a
            refl = alpha_r + covariates @ beta_r
            sigma = pm.HalfNormal("sigma", sigma=1)
            self._apply_likelihood(pm, atten_rate * thickness - refl, sigma, y, upper,
                                   detection)
        return model

    def mu_draws(self, posterior, X_new: np.ndarray) -> np.ndarray:
        X_new = np.asarray(X_new, dtype="float64")
        t_idx, cov_idx = self._split_columns()
        thickness = X_new[:, t_idx]
        cov = X_new[:, cov_idx]
        atten = posterior["alpha_atten"].values[:, None] + posterior["beta_atten"].values @ cov.T
        refl = posterior["alpha_refl"].values[:, None] + posterior["beta_refl"].values @ cov.T
        return atten * thickness[None, :] - refl
