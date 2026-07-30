"""Train stage: spatially-blocked CV, final fit, full-grid prediction, export.

Fixes the 2020 pipeline's gaps: an actual k-fold CV loop, seeded sampling, and
persisted artifacts — metrics.json, posterior.nc (self-describing ArviZ
InferenceData), predictions.zarr (per-sheet regular grids with CRS metadata,
viewer-ready), and a chained-provenance manifest.
"""

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .config import config_hash, load_model_config
from .models import get_model
from .models.normalize import apply_normalizer, fit_normalizer, invert_normalizer, invert_scale
from .provenance import build_stage_manifest

logger = logging.getLogger(__name__)

TEST_FOLD_CODE = 100  # fold value marking test cells in the zarr fold layer


def _metrics(y_true: np.ndarray, mu_mean: np.ndarray, pred_std: np.ndarray) -> dict:
    err = y_true - mu_mean
    return {
        "rmse_dB": float(np.sqrt(np.mean(err**2))),
        "mae_dB": float(np.mean(np.abs(err))),
        "coverage_1sigma": float(np.mean(np.abs(err) <= pred_std)),
        "n_points": int(len(y_true)),
    }


def _design_matrix(df: pd.DataFrame, features: list[str], norm: dict) -> np.ndarray:
    """Z-scored features + raw is_greenland indicator as the last column."""
    cols = [apply_normalizer(df[f].to_numpy(), norm[f]) for f in features]
    cols.append(df["is_greenland"].to_numpy(dtype="float64"))
    return np.column_stack(cols)


def _censored_mask(df: pd.DataFrame, censoring: dict) -> np.ndarray:
    """True where the matched obs sits too close to the noise floor (saturated)."""
    if not censoring["enabled"]:
        return np.zeros(len(df), dtype=bool)
    if "obs_margin_dB" not in df.columns:
        raise ValueError("train.censoring is enabled but split.parquet has no "
                         "obs_margin_dB column — re-run the split stage")
    margin = df["obs_margin_dB"].to_numpy()
    return np.asarray(margin < censoring["margin_threshold_dB"]) & np.isfinite(margin)


def _fit_and_eval(model, train_df, val_df, features, target, sampler, censoring) -> tuple:
    """Fit on train_df, predict val_df. Returns (idata, norm, metrics|None).

    Saturated (censored) training obs enter the fit as Tobit lower bounds.
    Point metrics (RMSE/MAE/coverage) use only uncensored validation points;
    logscore_dB is the censoring-aware predictive log score over all of them.
    """
    norm = fit_normalizer(train_df, [*features, target])
    feature_names = [*features, "is_greenland"]
    X = _design_matrix(train_df, features, norm)
    y = apply_normalizer(train_df[target].to_numpy(), norm[target])
    train_cens = _censored_mask(train_df, censoring)
    upper = np.where(train_cens, y, np.inf) if train_cens.any() else None
    idata = model.fit(X, y, feature_names, upper=upper, **sampler)

    metrics = None
    if val_df is not None and len(val_df):
        val_cens = _censored_mask(val_df, censoring)
        Xv = _design_matrix(val_df, features, norm)
        y_true = val_df[target].to_numpy()
        mu_mean, _, pred_std = model.predict(idata, Xv)
        mu_dB = invert_normalizer(mu_mean, norm[target])
        std_dB = invert_scale(pred_std, norm[target])
        clean = ~val_cens
        metrics = _metrics(y_true[clean], mu_dB[clean], std_dB[clean])
        metrics["n_censored"] = int(val_cens.sum())
        # Proper score in dB units: z-space log score minus log(target std).
        yz = apply_normalizer(y_true, norm[target])
        metrics["logscore_dB"] = (model.logscore(idata, Xv, yz, val_cens)
                                  - float(np.log(norm[target]["std"])))
        metrics.update(model.diagnostics(idata))
    return idata, norm, metrics


def write_predictions_zarr(df: pd.DataFrame, layers: dict[str, np.ndarray],
                           geometry: dict, zarr_path: Path) -> None:
    """Rasterize per-point layers into per-sheet regular grids with CRS metadata.

    Continuous layers fill with NaN; the int8 `fold` layer fills with -1.
    Written as zarr v2 with consolidated metadata for maximum viewer/QGIS
    compatibility (recorded in the manifest by the caller).
    """
    import rioxarray  # noqa: F401  (registers .rio accessor)
    import xarray as xr
    import zarr

    for i, (sheet, geom) in enumerate(geometry.items()):
        ny, nx = geom["shape"]
        x = geom["x0"] + geom["dx"] * np.arange(nx)
        y = geom["y0"] + geom["dy"] * np.arange(ny)
        rows = df["ice_sheet"] == sheet
        iy = df.loc[rows, "grid_iy"].to_numpy()
        ix = df.loc[rows, "grid_ix"].to_numpy()

        data_vars = {}
        for name, values in layers.items():
            if name == "fold":
                arr = np.full((ny, nx), -1, dtype="int8")
                arr[iy, ix] = values[rows.to_numpy()]
            else:
                arr = np.full((ny, nx), np.nan, dtype="float32")
                arr[iy, ix] = values[rows.to_numpy()]
            data_vars[name] = (("y", "x"), arr)
        ds = xr.Dataset(data_vars, coords={"x": x, "y": y})
        ds["pred_mean"].attrs.update(units="dB", long_name="posterior mean of mu")
        ds["pred_std_mu"].attrs.update(units="dB", long_name="posterior std of mu (parameter uncertainty)")
        ds["pred_std"].attrs.update(units="dB", long_name="posterior predictive std (incl. obs noise)")
        ds["obs_snr_dB"].attrs.update(units="dB", long_name="nearest radar observation (training target)")
        ds["fold"].attrs.update(long_name=f"CV fold (-1 none, {TEST_FOLD_CODE} test)")
        ds = ds.rio.write_crs(geom["crs"])
        for name in ds.data_vars:
            # Keep spatial_ref a *coordinate* through the zarr round-trip so
            # xr.open_zarr(...).rio.crs resolves without manual set_coords.
            ds[name].encoding["coordinates"] = "spatial_ref"
        ds.to_zarr(zarr_path, group=sheet, mode="w" if i == 0 else "a",
                   zarr_format=2, consolidated=True)
    zarr.consolidate_metadata(str(zarr_path))


def run_train(config_path: str, model_name: str, out_dir: str | None = None,
              repo_dir: str = ".") -> dict:
    config = load_model_config(config_path)
    out_dir = Path(out_dir or config["output"]["dir"])
    model_dir = out_dir / "model"
    run_dir = model_dir / model_name
    run_dir.mkdir(parents=True, exist_ok=True)
    tcfg = config["train"]
    target = config["split"]["target"]

    entry = next((m for m in tcfg["models"] if m["name"] == model_name), {"name": model_name})
    model = get_model(model_name, **{k: v for k, v in entry.items() if k != "name"})
    features = list(model.features or tcfg["features"])

    df = pd.read_parquet(model_dir / "split.parquet")
    split_manifest = json.loads((model_dir / "split.manifest.json").read_text())
    df["is_greenland"] = (df["ice_sheet"] == "greenland").astype("float64")

    usable = df[target].notna() & df[features].notna().all(axis=1)
    # No training or prediction on ice BedMachine considers thinner than the cutoff.
    min_thickness = tcfg["min_thickness_m"]
    thick_enough = pd.Series(True, index=df.index)
    if min_thickness is not None:
        thick_enough = ~(df["bedmachine_thickness_m"] < min_thickness)
        logger.info("Excluding %d grid points with bedmachine_thickness_m < %.0f m",
                    int((~thick_enough).sum()), min_thickness)
    usable &= thick_enough
    train_df = df[usable & (df["fold"] >= 0)]
    test_df = df[usable & df["is_test"]]
    dropped = int((df[target].notna() & (df["fold"] >= 0) & ~usable).sum())
    if dropped:
        logger.warning("Train: dropped %d observed points with NaN features", dropped)
    logger.info("Train %s: %d training points, %d test points, features=%s",
                model_name, len(train_df), len(test_df), features)

    # Spatially-blocked k-fold CV (cheaper cv_chains; each fit freshly seeded).
    folds = sorted(int(f) for f in train_df["fold"].unique())
    cv_sampler = dict(draws=tcfg["draws"], tune=tcfg["tune"],
                      chains=tcfg["cv_chains"], seed=tcfg["seed"])
    censoring = tcfg["censoring"]
    n_cens_train = int(_censored_mask(train_df, censoring).sum())
    if censoring["enabled"]:
        logger.info("Censoring enabled: %d/%d training obs flagged (margin < %.0f dB)",
                    n_cens_train, len(train_df), censoring["margin_threshold_dB"])

    fold_metrics = []
    for k in folds:
        _, _, m = _fit_and_eval(
            model, train_df[train_df["fold"] != k], train_df[train_df["fold"] == k],
            features, target, cv_sampler, censoring)
        m["fold"] = k
        fold_metrics.append(m)
        logger.info("CV fold %d: rmse=%.2f dB mae=%.2f dB coverage=%.2f logscore=%.3f "
                    "(n=%d + %d censored)", k, m["rmse_dB"], m["mae_dB"],
                    m["coverage_1sigma"], m["logscore_dB"], m["n_points"], m["n_censored"])

    def _pooled(key, weight_key="n_points"):
        w = np.array([m[weight_key] for m in fold_metrics], dtype="float64")
        v = np.array([m[key] for m in fold_metrics])
        return {"mean": float(np.average(v, weights=w)),
                "min": float(v.min()), "max": float(v.max())}

    pooled_cv = {key: _pooled(key) for key in ("rmse_dB", "mae_dB", "coverage_1sigma")}
    # logscore covers censored + uncensored points, so weight by the full count.
    for m in fold_metrics:
        m["n_total"] = m["n_points"] + m["n_censored"]
    pooled_cv["logscore_dB"] = _pooled("logscore_dB", weight_key="n_total")
    pooled_cv["n_points"] = int(sum(m["n_points"] for m in fold_metrics))
    pooled_cv["n_censored"] = int(sum(m["n_censored"] for m in fold_metrics))

    # Final fit on all training folds; evaluate on held-out test cells if any.
    sampler = dict(draws=tcfg["draws"], tune=tcfg["tune"],
                   chains=tcfg["chains"], seed=tcfg["seed"])
    idata, norm, test_metrics = _fit_and_eval(
        model, train_df, test_df if len(test_df) else None, features, target, sampler,
        censoring)
    diagnostics = model.diagnostics(idata)
    if test_metrics is None and config["split"]["test_cells"]:
        logger.warning("Test cells configured but contain no usable points")

    # Full-grid prediction (NaN where any feature is missing or ice is too thin).
    predictable = df[features].notna().all(axis=1) & thick_enough
    pred_df = df[predictable]
    mu_mean, mu_std, pred_std = model.predict(
        idata, _design_matrix(pred_df, features, norm),
        batch_size=tcfg["predict_batch_size"])
    full = {name: np.full(len(df), np.nan) for name in ("pred_mean", "pred_std_mu", "pred_std")}
    full["pred_mean"][predictable.to_numpy()] = invert_normalizer(mu_mean, norm[target])
    full["pred_std_mu"][predictable.to_numpy()] = invert_scale(mu_std, norm[target])
    full["pred_std"][predictable.to_numpy()] = invert_scale(pred_std, norm[target])
    logger.info("Predicted %d/%d grid points", int(predictable.sum()), len(df))

    fold_layer = df["fold"].to_numpy().astype("int16")
    fold_layer[df["is_test"].to_numpy()] = TEST_FOLD_CODE
    layers = {**full, "obs_snr_dB": df[target].to_numpy(), "fold": fold_layer}
    zarr_path = run_dir / "predictions.zarr"
    write_predictions_zarr(df, layers, split_manifest["geometry"], zarr_path)

    # Self-describing posterior: normalizer + features ride along as attrs.
    idata.attrs["features"] = json.dumps([*features, "is_greenland"])
    idata.attrs["normalizer"] = json.dumps(norm)
    idata.attrs["target"] = target
    posterior_path = run_dir / "posterior.nc"
    idata.to_netcdf(str(posterior_path))

    metrics = {
        "model": model_name,
        "split_run_id": split_manifest["run_id"],
        "features": features,
        "folds": fold_metrics,
        "pooled_cv": pooled_cv,
        "test": test_metrics,
        "sampler": sampler,
        "cv_chains": tcfg["cv_chains"],
        "censoring": {**censoring, "n_train_censored": n_cens_train},
        "diagnostics": diagnostics,
    }
    section_hash = config_hash({"train": {**tcfg, "models": [entry]}, "model": model_name})
    manifest = build_stage_manifest(
        "train", config, section_hash,
        input_ids=[split_manifest["run_id"]],
        inputs={"split_run_id": split_manifest["run_id"], "model": model_name},
        repo_dir=repo_dir,
        metrics=metrics,
        zarr_format=2,
    )
    metrics["run_id"] = manifest["run_id"]
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    paths = {"metrics": str(run_dir / "metrics.json"),
             "posterior": str(posterior_path),
             "zarr": str(zarr_path),
             "manifest": str(run_dir / "manifest.json")}
    logger.info("Train %s done: CV rmse=%.2f dB (run_id=%s)",
                model_name, pooled_cv["rmse_dB"]["mean"], manifest["run_id"])
    return {"paths": paths, "manifest": manifest, "metrics": metrics}


def write_benchmark(metrics_paths: list[str], csv_path: str, md_path: str) -> None:
    """One comparison row per trained model, from their metrics.json files."""
    rows = []
    for path in metrics_paths:
        m = json.loads(Path(path).read_text())
        rows.append({
            "model": m["model"],
            "cv_rmse_dB": round(m["pooled_cv"]["rmse_dB"]["mean"], 3),
            "cv_rmse_min": round(m["pooled_cv"]["rmse_dB"]["min"], 3),
            "cv_rmse_max": round(m["pooled_cv"]["rmse_dB"]["max"], 3),
            "cv_mae_dB": round(m["pooled_cv"]["mae_dB"]["mean"], 3),
            "cv_coverage_1sigma": round(m["pooled_cv"]["coverage_1sigma"]["mean"], 3),
            "cv_logscore_dB": (round(m["pooled_cv"]["logscore_dB"]["mean"], 3)
                               if "logscore_dB" in m["pooled_cv"] else None),
            "censored": m.get("censoring", {}).get("enabled", False),
            "test_rmse_dB": round(m["test"]["rmse_dB"], 3) if m.get("test") else None,
            "n_train": m["pooled_cv"]["n_points"],
            "divergences": m["diagnostics"]["divergences"],
            "rhat_max": (round(m["diagnostics"]["rhat_max"], 4)
                         if m["diagnostics"]["rhat_max"] is not None else None),
            "run_id": m.get("run_id"),
        })
    table = pd.DataFrame(rows).sort_values("cv_rmse_dB")
    table.to_csv(csv_path, index=False)
    Path(md_path).write_text(table.to_markdown(index=False) + "\n")
    logger.info("Benchmark: %s", csv_path)
