"""End-to-end split -> train on synthetic data (no network).

Builds a fake grid.parquet (two sheets, known linear law), fake augment store
parquets with embedded run_ids, then runs run_split and run_train with a tiny
sampler and checks columns, metrics, the predictions zarr, and run_id chaining.
"""

import json

import numpy as np
import pandas as pd
import pytest
import xarray as xr
import yaml
from pyproj import Transformer

from radar_postproc.output import embed_manifest, write_stage_output
from radar_postproc.split import run_split
from radar_postproc.train import TEST_FOLD_CODE, run_train

pytestmark = pytest.mark.slow

SHEET_CRS = {"antarctic": "EPSG:3031", "greenland": "EPSG:3413"}
FEATURES = ["bedmachine_thickness_m", "era5_t2m_mean_K"]
RES = 1000.0  # 1 km grid, 5 km cells -> plenty of cells


def _make_grid(out_dir):
    rng = np.random.default_rng(0)
    frames = []
    geometry = {}
    for sheet, n in [("antarctic", 40), ("greenland", 20)]:
        # Off-origin block so cell indices are non-trivial.
        x0, y0 = (200e3, -900e3) if sheet == "antarctic" else (-300e3, -2500e3)
        xs = x0 + RES * np.arange(n)
        ys = y0 - RES * np.arange(n)
        ix, iy = np.meshgrid(np.arange(n), np.arange(n))
        ix, iy = ix.ravel(), iy.ravel()
        x, y = xs[ix], ys[iy]
        lon, lat = Transformer.from_crs(SHEET_CRS[sheet], "EPSG:4326",
                                        always_xy=True).transform(x, y)
        df = pd.DataFrame({
            "ice_sheet": sheet, "grid_ix": ix, "grid_iy": iy, "x": x, "y": y,
            "longitude": lon, "latitude": lat,
            "bedmachine_thickness_m": rng.uniform(100, 3000, len(x)),
            "era5_t2m_mean_K": rng.uniform(230, 270, len(x)),
        })
        frames.append(df)
        geometry[sheet] = {"shape": [n, n], "x0": float(xs[0]), "y0": float(ys[0]),
                           "dx": RES, "dy": -RES, "crs": SHEET_CRS[sheet], "stride": 1}
    grid = pd.concat(frames, ignore_index=True)
    manifest = {"run_id": "fakegrid00000", "stage": "grid", "geometry": geometry}
    model_dir = out_dir / "model"
    write_stage_output(grid, manifest, model_dir / "grid.parquet")
    return grid


def _true_law(df):
    return (0.02 * df["bedmachine_thickness_m"] - 0.5 * (df["era5_t2m_mean_K"] - 250)
            + 5.0 * (df["ice_sheet"] == "greenland"))


def _make_stores(out_dir, grid):
    """Observations near a subset of grid points, split across the three stores."""
    rng = np.random.default_rng(1)
    run_ids = {}
    for store, sheet, frac in [("ase", "antarctic", 0.3), ("utig", "antarctic", 0.3),
                               ("greenland", "greenland", 0.5)]:
        sheet_grid = grid[grid["ice_sheet"] == sheet]
        picked = sheet_grid.sample(frac=frac, random_state=abs(hash(store)) % 2**31)
        # Jitter within the NN cutoff, in projected space.
        x = picked["x"] + rng.uniform(-200, 200, len(picked))
        y = picked["y"] + rng.uniform(-200, 200, len(picked))
        lon, lat = Transformer.from_crs(SHEET_CRS[sheet], "EPSG:4326",
                                        always_xy=True).transform(x, y)
        margin = rng.uniform(0, 40, len(picked))  # some obs land under the 10 dB flag
        obs = pd.DataFrame({
            "latitude": lat, "longitude": lon,
            "required_surface_snr_dB": _true_law(picked) + rng.normal(0, 1.0, len(picked)),
            "bed_power_dB": -100.0 + margin,
            "post_bed_noise_dB": -100.0,
        })
        if store == "greenland":
            # Reprocessed-store shape: pick flags, at-depth noise stats, and a
            # tail of attempted-but-unpicked traces (half clean, half high-delta).
            obs["bed_pick_available"] = True
            obs["bed_pick_attempted"] = True
            obs["post_bed_noise_interp_dB"] = -100.0
            obs["post_bed_peak_interp_dB"] = -95.0
            obs["surface_power_dB"] = -40.0
            obs["surface_twtt"] = 4e-6
            nd_src = sheet_grid.sample(n=60, random_state=7)
            jx = nd_src["x"] + rng.uniform(-200, 200, 60)
            jy = nd_src["y"] + rng.uniform(-200, 200, 60)
            nd_lon, nd_lat = Transformer.from_crs(SHEET_CRS[sheet], "EPSG:4326",
                                                  always_xy=True).transform(jx, jy)
            nd = pd.DataFrame({
                "latitude": nd_lat, "longitude": nd_lon,
                "required_surface_snr_dB": np.nan, "bed_power_dB": np.nan,
                "post_bed_noise_dB": np.nan,
                "bed_pick_available": False, "bed_pick_attempted": True,
                "post_bed_noise_interp_dB": -100.0,
                "post_bed_peak_interp_dB": -100.0 + np.where(np.arange(60) % 2, 4.0, 20.0),
                "surface_power_dB": -40.0, "surface_twtt": 4e-6,
            })
            obs = pd.concat([obs, nd], ignore_index=True)
        store_dir = out_dir / store
        store_dir.mkdir(parents=True)
        path = store_dir / f"{store}.parquet"
        obs.to_parquet(path, index=False)
        run_ids[store] = f"fake{store}0000"
        embed_manifest(path, {"run_id": run_ids[store]})
    return run_ids


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("outputs")
    grid = _make_grid(out_dir)
    run_ids = _make_stores(out_dir, grid)
    config = {
        "inputs": {"antarctic": ["ase", "utig"], "greenland": ["greenland"]},
        "split": {"nn_cutoff_m": 400, "cell_size_km": 5, "n_folds": 3, "seed": 42,
                  "test_cells": ["ant:41:-181"]},
        "train": {"draws": 150, "tune": 150, "chains": 1, "cv_chains": 1, "seed": 0,
                  "features": FEATURES,
                  "censoring": {"enabled": True, "margin_threshold_dB": 10},
                  "detection": {"enabled": True,
                                "delta_filter": {"enabled": True, "max_dB": 8}},
                  "models": [{"name": "linear"}, {"name": "atten_refl"}]},
    }
    config_path = out_dir / "model.yaml"
    config_path.write_text(yaml.safe_dump(config))
    split_result = run_split(str(config_path), out_dir=str(out_dir))
    train_result = run_train(str(config_path), "linear", out_dir=str(out_dir))
    return out_dir, config, run_ids, split_result, train_result


class TestSplit:
    def test_columns_and_folds(self, pipeline):
        out_dir, config, _, result, _ = pipeline
        df = pd.read_parquet(out_dir / "model" / "split.parquet")
        for col in ["required_surface_snr_dB", "obs_margin_dB", "obs_dist_m",
                    "cell_id", "fold", "is_test", "is_nondetect", "nd_delta_dB", "C_dB"]:
            assert col in df.columns
        nd = df[df["is_nondetect"]]
        assert len(nd) > 0
        assert nd["required_surface_snr_dB"].isna().all()
        assert np.isfinite(nd["C_dB"]).any()
        # Observed points carry a finite ceiling too (C = target + margin).
        obs_pts = df[df["required_surface_snr_dB"].notna() & df["obs_margin_dB"].notna()]
        assert np.allclose(obs_pts["C_dB"],
                           obs_pts["required_surface_snr_dB"] + obs_pts["obs_margin_dB"])
        observed = df[df["required_surface_snr_dB"].notna()]
        assert len(observed) > 500
        assert (df["obs_dist_m"].dropna() <= 400).all()
        folds = set(observed.loc[~observed["is_test"], "fold"].unique())
        assert folds <= {-1, 0, 1, 2} and len(folds - {-1}) == 3
        # Cell granularity: every cell maps to exactly one fold.
        assert (df.groupby("cell_id")["fold"].nunique() == 1).all()

    def test_test_cells_held_out(self, pipeline):
        out_dir, config, _, result, _ = pipeline
        df = pd.read_parquet(out_dir / "model" / "split.parquet")
        test_rows = df[df["is_test"]]
        assert set(test_rows["cell_id"]) == set(config["split"]["test_cells"])
        assert (test_rows["fold"] == -1).all()
        assert result["n_test_points"] > 0

    def test_manifest_chains_run_ids(self, pipeline):
        out_dir, _, run_ids, result, _ = pipeline
        manifest = json.loads((out_dir / "model" / "split.manifest.json").read_text())
        assert manifest["inputs"]["grid_run_id"] == "fakegrid00000"
        assert manifest["inputs"]["augment_run_ids"] == run_ids

    def test_helper_outputs(self, pipeline):
        out_dir, *_ = pipeline
        cells = pd.read_csv(out_dir / "model" / "cells.csv")
        assert {"cell_id", "n_obs", "fold", "is_test"} <= set(cells.columns)
        assert (out_dir / "model" / "cell_maps" / "cells_antarctic.png").exists()
        assert (out_dir / "model" / "cell_maps" / "cells_greenland.png").exists()


class TestTrain:
    def test_metrics_shape_and_quality(self, pipeline):
        out_dir, _, _, _, result = pipeline
        metrics = json.loads((out_dir / "model" / "linear" / "metrics.json").read_text())
        assert metrics["model"] == "linear"
        assert len(metrics["folds"]) == 3
        for key in ("rmse_dB", "mae_dB", "coverage_1sigma", "logscore_dB"):
            assert key in metrics["pooled_cv"]
        assert metrics["censoring"]["enabled"] is True
        assert metrics["pooled_cv"]["n_censored"] > 0
        assert np.isfinite(metrics["pooled_cv"]["logscore_dB"]["mean"])
        det = metrics["detection"]
        assert det["enabled"] is True
        assert det["n_nondetect_used"] > 0
        assert det["n_nondetect_excluded"] > 0  # the high-delta half
        assert np.isfinite(det["theta_mean_dB"]) and np.isfinite(det["tau_mean_dB"])
        assert metrics["test"] is not None
        # The synthetic law is linear with sd=1 noise; CV RMSE should be close.
        assert metrics["pooled_cv"]["rmse_dB"]["mean"] < 3.0
        rhat = metrics["diagnostics"]["rhat_max"]
        assert rhat is None or rhat < 1.1  # None: r_hat undefined for 1 chain

    def test_zarr_layout(self, pipeline):
        out_dir, *_ = pipeline
        zarr_path = out_dir / "model" / "linear" / "predictions.zarr"
        for sheet in ("antarctic", "greenland"):
            ds = xr.open_zarr(zarr_path, group=sheet)
            assert {"pred_mean", "pred_std_mu", "pred_std", "obs_snr_dB", "fold"} <= set(ds)
            import rioxarray  # noqa: F401

            assert ds.rio.crs is not None
            assert ds.rio.crs.to_string() == SHEET_CRS[sheet]
            expected = (40, 40) if sheet == "antarctic" else (20, 20)
            assert ds["pred_mean"].shape == expected
            # Full-grid prediction: everything with features gets a value.
            assert np.isfinite(ds["pred_mean"].values).all()
        ant = xr.open_zarr(zarr_path, group="antarctic")
        assert (ant["fold"].values == TEST_FOLD_CODE).sum() > 0

    def test_posterior_self_describing(self, pipeline):
        out_dir, *_ = pipeline
        import arviz as az

        idata = az.from_netcdf(out_dir / "model" / "linear" / "posterior.nc")
        attrs = idata.attrs if idata.attrs else idata.posterior.attrs
        assert "normalizer" in attrs and "features" in attrs
        norm = json.loads(attrs["normalizer"])
        assert "required_surface_snr_dB" in norm

    def test_train_manifest_chains_split(self, pipeline):
        out_dir, *_ = pipeline
        split_manifest = json.loads((out_dir / "model" / "split.manifest.json").read_text())
        train_manifest = json.loads((out_dir / "model" / "linear" / "manifest.json").read_text())
        assert train_manifest["inputs"]["split_run_id"] == split_manifest["run_id"]

    def test_atten_refl_and_benchmark(self, pipeline):
        out_dir, config, *_ = pipeline
        from radar_postproc.train import write_benchmark

        config_path = out_dir / "model.yaml"
        run_train(str(config_path), "atten_refl", out_dir=str(out_dir))
        paths = [out_dir / "model" / m / "metrics.json" for m in ("linear", "atten_refl")]
        write_benchmark([str(p) for p in paths],
                        str(out_dir / "model" / "benchmark.csv"),
                        str(out_dir / "model" / "benchmark.md"))
        table = pd.read_csv(out_dir / "model" / "benchmark.csv")
        assert set(table["model"]) == {"linear", "atten_refl"}
        assert (out_dir / "model" / "benchmark.md").exists()
