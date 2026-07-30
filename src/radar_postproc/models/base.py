"""Bayesian model plugin base: shared fit/predict skeleton.

A model plugin subclasses BaseBayesianModel and implements
  build(X, y, feature_names) -> pm.Model     (the PyMC graph; likelihood "obs")
  mu_draws(posterior, X_new) -> (S, n) array (the mean function, applied
                                              analytically to posterior draws)
The analytic mu_draws duplicates the mean function outside PyMC on purpose: it
makes full-grid prediction a cheap batched matmul instead of a pytensor
recompute, and lets tests assert fit/predict consistency directly. All inputs
are z-scored by the caller (train.py); the trailing feature is the raw 0/1
is_greenland indicator.
"""

import logging

import arviz as az
import numpy as np

logger = logging.getLogger(__name__)


class BaseBayesianModel:
    name: str = ""
    features: list[str] | None = None  # None -> use config train.features
    feature_names: list[str] = []      # column order of X, recorded at fit time

    def __init__(self, features: list[str] | None = None):
        if features is not None:
            self.features = list(features)

    def build(self, X: np.ndarray, y: np.ndarray, feature_names: list[str],
              upper: np.ndarray | None = None):
        """PyMC graph. `upper` (same length as y) makes the likelihood
        right-censored (Tobit): y_i == upper_i contributes P(Y >= y_i)."""
        raise NotImplementedError

    def mu_draws(self, posterior, X_new: np.ndarray) -> np.ndarray:
        """(n_samples, n_points) mean-function values from posterior draws."""
        raise NotImplementedError

    @staticmethod
    def _likelihood(pm, mu, sigma, y: np.ndarray, upper: np.ndarray | None):
        """Normal likelihood, right-censored where `upper` is finite."""
        if upper is None:
            return pm.Normal("obs", mu=mu, sigma=sigma, observed=y)
        return pm.Censored("obs", pm.Normal.dist(mu=mu, sigma=sigma),
                           lower=None, upper=upper, observed=y)

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list[str],
            draws: int, tune: int, chains: int, seed: int,
            upper: np.ndarray | None = None) -> az.InferenceData:
        import pymc as pm

        self.feature_names = list(feature_names)
        model = self.build(X, y, feature_names, upper=upper)
        with model:
            idata = pm.sample(draws=draws, tune=tune, chains=chains,
                              random_seed=seed, progressbar=False)
        idata.attrs["model_name"] = self.name
        return idata

    @staticmethod
    def _group(idata, name: str):
        """A group as a plain Dataset (PyMC>=6 returns a DataTree)."""
        group = idata[name] if isinstance(idata, dict) else getattr(idata, name)
        return group.to_dataset() if hasattr(group, "to_dataset") else group

    @classmethod
    def _stacked_posterior(cls, idata: az.InferenceData, max_samples: int = 1000):
        """Posterior with (chain, draw) stacked to one `sample` dim, thinned."""
        post = cls._group(idata, "posterior").stack(sample=("chain", "draw"))
        n = post.sizes["sample"]
        if n > max_samples:
            post = post.isel(sample=slice(None, None, int(np.ceil(n / max_samples))))
        return post.transpose("sample", ...)

    def predict(self, idata: az.InferenceData, X_new: np.ndarray,
                batch_size: int = 100_000, max_samples: int = 1000):
        """Posterior mean/std of mu plus full predictive std, in z-space.

        Returns (mu_mean, mu_std, pred_std) with
        pred_std = sqrt(mu_std^2 + E[sigma^2]) — parameter uncertainty widened
        by the observation noise.
        """
        post = self._stacked_posterior(idata, max_samples)
        sigma2 = float((post["sigma"].values ** 2).mean())
        n = len(X_new)
        mu_mean = np.empty(n)
        mu_std = np.empty(n)
        for start in range(0, n, batch_size):
            stop = min(start + batch_size, n)
            draws = self.mu_draws(post, X_new[start:stop])  # (S, batch)
            mu_mean[start:stop] = draws.mean(axis=0)
            mu_std[start:stop] = draws.std(axis=0)
        pred_std = np.sqrt(mu_std**2 + sigma2)
        return mu_mean, mu_std, pred_std

    def logscore(self, idata: az.InferenceData, X: np.ndarray, y: np.ndarray,
                 censored: np.ndarray | None = None, max_samples: int = 1000,
                 batch_size: int = 100_000) -> float:
        """Mean held-out predictive log score, censoring-aware (proper score).

        Uncensored points score the posterior-averaged Normal density
        log E_s[phi(y | mu_s, sigma_s)]; censored points (right-censored lower
        bounds) score the survival mass log E_s[P(Y >= y | mu_s, sigma_s)].
        All in the (z-scored) units of y as passed.
        """
        from scipy.special import logsumexp
        from scipy.stats import norm

        post = self._stacked_posterior(idata, max_samples)
        sigma = post["sigma"].values  # (S,)
        censored = (np.zeros(len(y), dtype=bool) if censored is None
                    else np.asarray(censored, dtype=bool))
        scores = np.empty(len(y))
        for start in range(0, len(y), batch_size):
            stop = min(start + batch_size, len(y))
            mu = self.mu_draws(post, np.asarray(X)[start:stop])      # (S, b)
            z = (np.asarray(y)[start:stop][None, :] - mu) / sigma[:, None]
            logp = norm.logpdf(z) - np.log(sigma)[:, None]           # density
            logp_cens = norm.logsf(z)                                # P(Y >= y)
            pointwise = np.where(censored[start:stop][None, :], logp_cens, logp)
            scores[start:stop] = logsumexp(pointwise, axis=0) - np.log(mu.shape[0])
        return float(scores.mean())

    @classmethod
    def diagnostics(cls, idata: az.InferenceData) -> dict:
        diverging = int(cls._group(idata, "sample_stats")["diverging"].sum())
        posterior = cls._group(idata, "posterior")

        def _collect(ds):  # all values of all variables in a stats Dataset
            return np.concatenate([np.atleast_1d(v.values).ravel()
                                   for v in ds.data_vars.values()])

        def _finite(value):  # r_hat is NaN for single-chain fits
            return float(value) if np.isfinite(value) else None

        rhat = _collect(az.rhat(posterior))
        ess = _collect(az.ess(posterior, method="bulk"))
        return {
            "divergences": diverging,
            "rhat_max": _finite(np.nanmax(rhat)) if not np.isnan(rhat).all() else None,
            "ess_bulk_min": _finite(np.nanmin(ess)) if not np.isnan(ess).all() else None,
        }
