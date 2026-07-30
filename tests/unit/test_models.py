"""Model plugin tests: normalization round-trip and linear-law recovery."""

import numpy as np
import pandas as pd
import pytest

from radar_postproc.models import get_model, list_models
from radar_postproc.models.normalize import (
    apply_normalizer,
    fit_normalizer,
    invert_normalizer,
    invert_scale,
)


class TestNormalize:
    def test_round_trip(self):
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 10.0]})
        norm = fit_normalizer(df, ["a"])
        z = apply_normalizer(df["a"].to_numpy(), norm["a"])
        assert np.allclose(invert_normalizer(z, norm["a"]), df["a"])
        assert np.isclose(z.mean(), 0) and np.isclose(z.std(), 1)

    def test_stats_from_given_rows_only(self):
        train = pd.DataFrame({"a": [0.0, 2.0]})
        norm = fit_normalizer(train, ["a"])
        assert norm["a"]["mean"] == 1.0
        # Values not in the fit rows are scaled by train stats, not their own.
        assert apply_normalizer(np.array([5.0]), norm["a"])[0] == pytest.approx(4.0)

    def test_constant_column_no_div_by_zero(self):
        norm = fit_normalizer(pd.DataFrame({"a": [3.0, 3.0]}), ["a"])
        assert norm["a"]["std"] == 1.0
        assert np.isfinite(apply_normalizer(np.array([3.0]), norm["a"])).all()

    def test_invert_scale_no_mean_shift(self):
        stats = {"mean": 50.0, "std": 4.0}
        assert invert_scale(np.array([2.0]), stats)[0] == 8.0


class TestRegistry:
    def test_linear_registered(self):
        assert "linear" in list_models()

    def test_unknown_model_raises(self):
        with pytest.raises(KeyError):
            get_model("nope")


class TestLinearModel:
    @pytest.mark.slow
    def test_recovers_known_law(self):
        rng = np.random.default_rng(0)
        n = 250
        X = np.column_stack([rng.normal(size=n), rng.normal(size=n),
                             rng.integers(0, 2, size=n).astype(float)])
        y = 1.0 + 2.0 * X[:, 0] - 1.5 * X[:, 1] + 0.5 * X[:, 2] + rng.normal(0, 0.3, n)

        model = get_model("linear")
        idata = model.fit(X, y, ["f1", "f2", "is_greenland"],
                          draws=300, tune=300, chains=2, seed=0)
        post = model._stacked_posterior(idata)
        beta = post["beta"].values.mean(axis=0)
        assert beta[0] == pytest.approx(2.0, abs=0.15)
        assert beta[1] == pytest.approx(-1.5, abs=0.15)

        mu_mean, mu_std, pred_std = model.predict(idata, X)
        assert np.isfinite(mu_mean).all()
        assert (pred_std >= mu_std).all()
        rmse = np.sqrt(np.mean((y - mu_mean) ** 2))
        assert rmse < 0.5
        # ~68% of residuals inside 1 predictive sigma.
        coverage = np.mean(np.abs(y - mu_mean) <= pred_std)
        assert 0.5 < coverage < 0.9

    @pytest.mark.slow
    def test_censored_fit_recovers_better_than_naive(self):
        """Right-censoring the top of y biases a naive fit low; Tobit shouldn't."""
        rng = np.random.default_rng(2)
        n = 300
        X = rng.normal(size=(n, 1))
        y_true = 2.0 * X[:, 0] + rng.normal(0, 0.3, n)
        ceiling = 1.0
        y_obs = np.minimum(y_true, ceiling)
        censored = y_true > ceiling
        assert censored.sum() > 30

        naive = get_model("linear")
        idata_n = naive.fit(X, y_obs, ["f"], draws=300, tune=300, chains=1, seed=0)
        tobit = get_model("linear")
        upper = np.where(censored, y_obs, np.inf)
        idata_t = tobit.fit(X, y_obs, ["f"], draws=300, tune=300, chains=1, seed=0,
                            upper=upper)

        slope_n = float(naive._stacked_posterior(idata_n)["beta"].values.mean(axis=0)[0])
        slope_t = float(tobit._stacked_posterior(idata_t)["beta"].values.mean(axis=0)[0])
        assert slope_n < 1.9  # naive is biased low
        assert abs(slope_t - 2.0) < abs(slope_n - 2.0)
        assert slope_t == pytest.approx(2.0, abs=0.15)

        # Censoring-aware logscore prefers the tobit fit on the same data.
        ls_n = naive.logscore(idata_n, X, y_obs, censored)
        ls_t = tobit.logscore(idata_t, X, y_obs, censored)
        assert np.isfinite(ls_n) and np.isfinite(ls_t)
        assert ls_t > ls_n

    @pytest.mark.slow
    def test_predict_batching_consistent(self):
        rng = np.random.default_rng(1)
        X = rng.normal(size=(50, 2))
        y = X[:, 0] + rng.normal(0, 0.1, 50)
        model = get_model("linear")
        idata = model.fit(X, y, ["a", "b"], draws=100, tune=100, chains=1, seed=0)
        one = model.predict(idata, X, batch_size=7)
        whole = model.predict(idata, X, batch_size=1000)
        for a, b in zip(one, whole):
            assert np.allclose(a, b)
