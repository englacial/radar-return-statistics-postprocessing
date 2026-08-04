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


INDICATOR_BUILDERS = {
    # Raw 0/1 columns appended (un-scaled) after the z-scored features. At
    # full-grid prediction time is_utig is 0 everywhere: institution is a
    # property of the *measurement*, so maps are referenced to CReSIS.
    "is_greenland": lambda df: (df["ice_sheet"] == "greenland"),
    "is_utig": lambda df: (df.get("institution", pd.Series(index=df.index)) == "UTIG"),
}


def add_indicator_columns(df: pd.DataFrame, indicators: list[str]) -> None:
    unknown = [i for i in indicators if i not in INDICATOR_BUILDERS]
    if unknown:
        raise ValueError(f"Unknown train.indicators {unknown}; "
                         f"supported: {sorted(INDICATOR_BUILDERS)}")
    for name in indicators:
        df[name] = INDICATOR_BUILDERS[name](df).astype("float64")


def _design_matrix(df: pd.DataFrame, features: list[str], norm: dict,
                   indicators: tuple[str, ...] = ("is_greenland",)) -> np.ndarray:
    """Z-scored features + raw 0/1 indicator columns appended."""
    cols = [apply_normalizer(df[f].to_numpy(), norm[f]) for f in features]
    for name in indicators:
        cols.append(df[name].to_numpy(dtype="float64"))
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


def select_nondetects(nd_df: pd.DataFrame, detection: dict) -> tuple[pd.DataFrame, int]:
    """Non-detection rows entering the likelihood, per the optional delta filter.

    Returns (used_rows, n_excluded). With the filter on, rows whose at-depth
    window shows energy (nd_delta_dB >= max_dB, or NaN delta) are excluded as
    plausible clutter/mislocated-bed gaps. Rows always need a finite ceiling.
    """
    usable = nd_df[np.isfinite(nd_df["C_dB"].to_numpy())]
    flt = detection["delta_filter"]
    if not flt["enabled"]:
        return usable, len(nd_df) - len(usable)
    delta = usable["nd_delta_dB"].to_numpy()
    keep = np.isfinite(delta) & (delta < flt["max_dB"])
    return usable[keep], len(nd_df) - int(keep.sum())


def _detection_block(train_df, nd_df, features, norm, target, detection):
    """Assemble the detection dict + stacked non-detect design matrix rows."""
    sel = train_df["obs_margin_dB"].to_numpy(dtype="float64")
    return {
        "sel_margin_dB": np.where(np.isfinite(sel), sel, np.inf),
        "C_nd_dB": nd_df["C_dB"].to_numpy(dtype="float64"),
        "target_norm": (norm[target]["mean"], norm[target]["std"]),
        "theta_prior": detection["theta_prior"],
        "tau_prior_sigma": detection["tau_prior_sigma"],
    }


def _nd_logscore(model, idata, nd_df, features, norm, target,
                 indicators=("is_greenland",)) -> float:
    """Mean held-out log P(no detection) over non-detect grid points (proper score)."""
    from scipy.special import logsumexp
    from scipy.stats import norm as norm_dist

    post = model._stacked_posterior(idata)
    mean, std = norm[target]["mean"], norm[target]["std"]
    mu_dB = model.mu_draws(post, _design_matrix(nd_df, features, norm, indicators)) * std + mean
    theta = post["theta"].values[:, None]
    tau = post["tau"].values[:, None]
    s = np.sqrt(tau**2 + (post["sigma"].values[:, None] * std) ** 2)
    z = (mu_dB - (nd_df["C_dB"].to_numpy()[None, :] - theta)) / s
    logp = norm_dist.logcdf(z)
    return float((logsumexp(logp, axis=0) - np.log(logp.shape[0])).mean())


def _fit_and_eval(model, train_df, val_df, features, target, sampler, censoring,
                  detection=None, nd_train=None,
                  indicators=("is_greenland",)) -> tuple:
    """Fit on train_df (+ optional non-detections), predict val_df.

    Saturated (censored) training obs enter the fit as Tobit lower bounds.
    Point metrics (RMSE/MAE/coverage) use only uncensored validation points;
    logscore_dB is the censoring-aware predictive log score over all of them.
    """
    norm = fit_normalizer(train_df, [*features, target])
    feature_names = [*features, *indicators]
    X = _design_matrix(train_df, features, norm, indicators)
    y = apply_normalizer(train_df[target].to_numpy(), norm[target])
    train_cens = _censored_mask(train_df, censoring)
    upper = np.where(train_cens, y, np.inf) if train_cens.any() else None
    det = None
    if detection is not None and detection["enabled"]:
        nd_train = nd_train if nd_train is not None else train_df.iloc[0:0]
        det = _detection_block(train_df, nd_train, features, norm, target, detection)
        if len(nd_train):
            X = np.vstack([X, _design_matrix(nd_train, features, norm, indicators)])
    idata = model.fit(X, y, feature_names, upper=upper, detection=det, **sampler)

    metrics = None
    if val_df is not None and len(val_df):
        val_cens = _censored_mask(val_df, censoring)
        Xv = _design_matrix(val_df, features, norm, indicators)
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
    indicators = tuple(tcfg["indicators"])
    if "is_utig" in indicators and "institution" not in df.columns:
        raise ValueError("train.indicators includes is_utig but split.parquet has no "
                         "institution column — re-run the split stage")
    add_indicator_columns(df, list(indicators))

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

    detection = tcfg["detection"]
    nd_used = df.iloc[0:0]
    n_nd_excluded = 0
    if detection["enabled"]:
        if "is_nondetect" not in df.columns:
            raise ValueError("train.detection is enabled but split.parquet has no "
                             "non-detection columns — re-run the split stage")
        nd_all = df[df["is_nondetect"] & df[features].notna().all(axis=1)
                    & thick_enough & (df["fold"] >= 0)]
        nd_used, n_nd_excluded = select_nondetects(nd_all, detection)
        logger.info("Detection enabled: %d non-detection grid points used, %d excluded "
                    "(delta filter %s)", len(nd_used), n_nd_excluded,
                    "on" if detection["delta_filter"]["enabled"] else "off")

    fold_metrics = []
    for k in folds:
        fold_idata, fold_norm, m = _fit_and_eval(
            model, train_df[train_df["fold"] != k], train_df[train_df["fold"] == k],
            features, target, cv_sampler, censoring,
            detection=detection, nd_train=nd_used[nd_used["fold"] != k],
            indicators=indicators)
        m["n_nd_train"] = int((nd_used["fold"] != k).sum()) if detection["enabled"] else 0
        nd_val = nd_used[nd_used["fold"] == k]
        if detection["enabled"] and len(nd_val):
            m["nd_logscore"] = _nd_logscore(model, fold_idata, nd_val, features,
                                            fold_norm, target, indicators)
            m["n_nd_val"] = len(nd_val)
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
        censoring, detection=detection, nd_train=nd_used, indicators=indicators)
    diagnostics = model.diagnostics(idata)

    detection_info = {**detection}
    if detection["enabled"]:
        post = model._stacked_posterior(idata)
        detection_info.update(
            n_nondetect_used=len(nd_used), n_nondetect_excluded=int(n_nd_excluded),
            theta_mean_dB=float(post["theta"].values.mean()),
            theta_sd_dB=float(post["theta"].values.std()),
            tau_mean_dB=float(post["tau"].values.mean()),
            tau_sd_dB=float(post["tau"].values.std()),
        )
        nd_scores = [(m["nd_logscore"], m["n_nd_val"]) for m in fold_metrics
                     if "nd_logscore" in m]
        if nd_scores:
            w = np.array([n for _, n in nd_scores], dtype="float64")
            detection_info["cv_nd_logscore"] = float(
                np.average([s for s, _ in nd_scores], weights=w))
    if test_metrics is None and config["split"]["test_cells"]:
        logger.warning("Test cells configured but contain no usable points")

    # Full-grid prediction (NaN where any feature is missing or ice is too thin).
    predictable = df[features].notna().all(axis=1) & thick_enough
    pred_df = df[predictable]
    mu_mean, mu_std, pred_std = model.predict(
        idata, _design_matrix(pred_df, features, norm, indicators),
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
    idata.attrs["features"] = json.dumps([*features, *indicators])
    idata.attrs["normalizer"] = json.dumps(norm)
    idata.attrs["target"] = target
    posterior_path = run_dir / "posterior.nc"
    idata.to_netcdf(str(posterior_path))

    metrics = {
        "model": model_name,
        "split_run_id": split_manifest["run_id"],
        "features": features,
        "indicators": list(indicators),
        "folds": fold_metrics,
        "pooled_cv": pooled_cv,
        "test": test_metrics,
        "sampler": sampler,
        "cv_chains": tcfg["cv_chains"],
        "censoring": {**censoring, "n_train_censored": n_cens_train},
        "detection": detection_info,
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
