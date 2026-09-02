"""Old-vs-new model comparison for the radiometric calibration QC filter.

Baseline = a copy of outputs/ taken before the store re-pin + split.calibration_qc
(model/ plus the antarctica/greenland augment parquets). New = outputs/ after
re-running `snakemake model_all`. Writes to outputs/qc_filter/:
  qc_removal_by_season.{csv,md,png}  per-season share of traces each rule rejects
  qc_sensitivity.md                  trace share rejected under threshold variants
  benchmark_comparison.md            metrics.json side by side, both models
  same_points_test.md                old and new posteriors scored on the SAME
                                     held-out test points (old and new test sets)
  cv_folds.png                       per-fold CV RMSE, old vs new
  posterior_comparison.png           atten_refl parameters in physical units
  prediction_difference.png          new - old posterior-mean RSSNR maps + hist
  target_hist.png                    training-target distribution before/after

Usage: uv run python scripts/qc_filter_comparison.py [--baseline outputs/baseline_20260807]
"""

import argparse
import json
from pathlib import Path

import arviz as az
import matplotlib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xarray as xr

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from radar_postproc.config import load_model_config  # noqa: E402
from radar_postproc.models import get_model  # noqa: E402
from radar_postproc.models.normalize import (  # noqa: E402
    apply_normalizer, invert_normalizer, invert_scale)
from radar_postproc.split import calibration_qc_flags  # noqa: E402
from radar_postproc.train import (  # noqa: E402
    _censored_mask, _design_matrix, _metrics, add_indicator_columns)

from plot_style import C_ANT, C_GRL, INK, LS_OBS, SHEET_COLOR, style_axis  # noqa: E402
from posterior_physical import physical_panels  # noqa: E402

SHEETS = {"antarctic": "antarctica", "greenland": "greenland"}  # sheet -> store
MODELS = ["linear", "atten_refl"]
C_OLD, C_NEW = "0.55", "tab:orange"
QC_COLS = ["img_comb_offset_dB", "surface_source_image_index", "surface_ceiling_margin_dB"]


# --- trace-level QC accounting ------------------------------------------------

def load_traces(out_dir: Path) -> pd.DataFrame:
    frames = []
    for sheet, store in SHEETS.items():
        path = out_dir / store / f"{store}.parquet"
        have = set(pq.read_schema(path).names)
        df = pd.read_parquet(path, columns=[c for c in ["collection", *QC_COLS] if c in have])
        for c in QC_COLS:  # pre-calibration parquets: nothing measured
            if c not in df:
                df[c] = np.nan
        df["sheet"] = sheet
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def removal_by_season(traces: pd.DataFrame, qc: dict, excluded: list[str]) -> pd.DataFrame:
    flags = calibration_qc_flags(traces, qc)
    d = traces[["sheet", "collection"]].copy()
    d["seam"] = flags["seam"]
    d["img2"] = flags["img2"] & ~flags["seam"]              # exclusive attribution
    d["saturated"] = flags["saturated"] & ~flags["seam"] & ~flags["img2"]
    d["any"] = flags.any(axis=1)
    d["seam_unmeasured"] = traces["img_comb_offset_dB"].isna()
    d["margin_unmeasured"] = traces["surface_ceiling_margin_dB"].isna()
    g = d.groupby(["sheet", "collection"]).agg(
        n=("any", "size"), **{c: (c, "mean") for c in
                              ["seam", "img2", "saturated", "any",
                               "seam_unmeasured", "margin_unmeasured"]}).reset_index()
    g["excluded"] = g["collection"].isin(excluded)
    return g.sort_values(["sheet", "collection"]).reset_index(drop=True)


def plot_removal(tab: pd.DataFrame, qc: dict, out: Path):
    tab = tab.iloc[::-1]
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(tab) + 1.8))
    y = np.arange(len(tab))
    for sheet_rows, color in ((tab["sheet"] == "antarctic", C_ANT),
                              (tab["sheet"] == "greenland", C_GRL)):
        r = tab[sheet_rows]
        yy = y[sheet_rows.to_numpy()]
        left = np.zeros(len(r))
        for rule, alpha in (("seam", 1.0), ("img2", 0.6), ("saturated", 0.3)):
            ax.barh(yy, 100 * r[rule], left=left, color=color, alpha=alpha,
                    edgecolor="white", linewidth=0.5)
            left += 100 * r[rule].to_numpy()
    labels = [f"{c}{'  (excluded)' if e else ''}" for c, e in zip(tab["collection"], tab["excluded"])]
    ax.set_yticks(y, labels, fontsize=8)
    for yi, (_, row) in zip(y, tab.iterrows()):
        ax.text(100 * row["any"] + 0.5, yi,
                f"{100 * row['any']:.1f}%  (n={row['n']:,}; seam unmeasured "
                f"{100 * row['seam_unmeasured']:.0f}%)", va="center", fontsize=7, color=INK)
    ax.set_xlabel("traces rejected [%]", color=INK)
    ax.set_xlim(0, max(60, 100 * tab["any"].max() + 28))
    handles = [plt.Rectangle((0, 0), 1, 1, color="0.4", alpha=a) for a in (1.0, 0.6, 0.3)]
    ax.legend(handles, [f"seam step |Δ| ≥ {qc['max_seam_offset_dB']:g} dB",
                        "surface from higher-gain image (img2+)",
                        f"surface within {qc['min_ceiling_margin_dB']:g} dB of season ceiling"],
              loc="lower right", fontsize=8, frameon=False)
    ax.set_title("Calibration QC: share of traces rejected per season (exclusive attribution)\n"
                 "blue = Antarctica, green = Greenland; unmeasured seams/ceilings pass",
                 color=INK, fontsize=10)
    style_axis(ax)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)


def sensitivity_table(traces: pd.DataFrame, qc: dict) -> pd.DataFrame:
    variants = [("adopted", {})]
    variants += [(f"max_seam_offset_dB={v}", {"max_seam_offset_dB": v}) for v in (2.0, 5.0)]
    variants += [(f"min_ceiling_margin_dB={v}", {"min_ceiling_margin_dB": v}) for v in (1.0, 3.0)]
    variants += [("require_img1_surface=false", {"require_img1_surface": False}),
                 ("drop_unmeasured_seam=true", {"drop_unmeasured_seam": True})]
    rows = []
    for name, over in variants:
        flags = calibration_qc_flags(traces, {**qc, **over})
        rej = flags.any(axis=1)
        row = {"variant": name}
        for sheet in SHEETS:
            m = traces["sheet"] == sheet
            row[f"{sheet}_rejected_pct"] = round(100 * rej[m].mean(), 1)
        rows.append(row)
    return pd.DataFrame(rows)


# --- model-level comparison ---------------------------------------------------

def load_run(model_dir: Path):
    metrics = json.loads((model_dir / "metrics.json").read_text())
    idata = az.from_netcdf(model_dir / "posterior.nc")
    model = get_model(metrics["model"])
    model.feature_names = json.loads(idata.attrs["features"])
    return metrics, idata, model


def benchmark_rows(runs: dict) -> pd.DataFrame:
    rows = []
    for (version, name), (m, idata, _) in runs.items():
        cv, det = m["pooled_cv"], m["detection"]
        post = idata.posterior
        sy = json.loads(idata.attrs["normalizer"])["required_surface_snr_dB"]["std"]
        rows.append({
            "model": name, "version": version,
            "n_train": cv["n_points"] + cv["n_censored"], "n_censored": cv["n_censored"],
            "n_nondetect": det.get("n_nondetect_used"),
            "cv_rmse_dB": f"{cv['rmse_dB']['mean']:.2f} [{cv['rmse_dB']['min']:.2f}–{cv['rmse_dB']['max']:.2f}]",
            "cv_mae_dB": round(cv["mae_dB"]["mean"], 2),
            "cv_cov1σ": round(cv["coverage_1sigma"]["mean"], 3),
            "cv_logscore": round(cv["logscore_dB"]["mean"], 3),
            "cv_nd_logscore": round(det["cv_nd_logscore"], 3) if "cv_nd_logscore" in det else None,
            "test_rmse_dB": round(m["test"]["rmse_dB"], 2),
            "test_logscore": round(m["test"]["logscore_dB"], 3),
            "σ_dB": round(float(post["sigma"].values.mean() * sy), 2),
            "θ_dB": round(det["theta_mean_dB"], 2), "τ_dB": round(det["tau_mean_dB"], 2),
            "rhat_max": round(m["diagnostics"]["rhat_max"], 4),
            "run_id": m["run_id"],
        })
    return pd.DataFrame(rows)


def score_points(run, points: pd.DataFrame, target: str, censoring: dict) -> dict:
    """Score a fitted posterior on given grid points (its own normalizer)."""
    metrics, idata, model = run
    norm = json.loads(idata.attrs["normalizer"])
    df = points.copy()
    add_indicator_columns(df, metrics["indicators"])
    X = _design_matrix(df, metrics["features"], norm, tuple(metrics["indicators"]),
                       tuple(metrics["interactions"]))
    y = df[target].to_numpy()
    mu, _, pstd = model.predict(idata, X)
    mu_dB = invert_normalizer(mu, norm[target])
    std_dB = invert_scale(pstd, norm[target])
    cens = _censored_mask(df, censoring)
    out = _metrics(y[~cens], mu_dB[~cens], std_dB[~cens])
    out["bias_dB"] = float(np.mean(mu_dB[~cens] - y[~cens]))
    out["logscore_dB"] = (model.logscore(idata, X, apply_normalizer(y, norm[target]), cens)
                          - float(np.log(norm[target]["std"])))
    out["n_censored"] = int(cens.sum())
    return out


def usable_points(split: pd.DataFrame, cfg: dict, mask) -> pd.DataFrame:
    tcfg = cfg["train"]
    ok = split[cfg["split"]["target"]].notna() & split[tcfg["features"]].notna().all(axis=1)
    if tcfg["min_thickness_m"] is not None:
        ok &= ~(split["bedmachine_thickness_m"] < tcfg["min_thickness_m"])
    return split[ok & mask]


def plot_cv_folds(runs: dict, out: Path):
    fig, axes = plt.subplots(1, len(MODELS), figsize=(5 * len(MODELS), 3.6), sharey=True)
    for ax, name in zip(np.atleast_1d(axes), MODELS):
        for i, (version, color) in enumerate((("baseline", C_OLD), ("QC", C_NEW))):
            folds = runs[(version, name)][0]["folds"]
            k = np.array([f["fold"] for f in folds])
            ax.bar(k + (i - 0.5) * 0.38, [f["rmse_dB"] for f in folds], width=0.38,
                   color=color, label=f"{version} (pooled {runs[(version, name)][0]['pooled_cv']['rmse_dB']['mean']:.2f} dB)")
        ax.set_title(name, color=INK)
        ax.set_xlabel("spatial CV fold", color=INK)
        ax.legend(fontsize=8, frameon=False)
        style_axis(ax)
    np.atleast_1d(axes)[0].set_ylabel("out-of-fold RMSE [dB]", color=INK)
    fig.suptitle("Spatially-blocked CV RMSE per fold — before (grey) vs after (orange) calibration QC",
                 color=INK, fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)


def plot_posteriors(old, new, out: Path):
    po, pn = physical_panels(old), physical_panels(new)
    ncol = 5
    nrow = -(-len(po) // ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(3.3 * ncol, 2.9 * nrow))
    for ax, (label, d_old, unit), (_, d_new, _) in zip(axes.ravel(), po, pn):
        lo, hi = np.percentile(np.concatenate([d_old, d_new]), [0.1, 99.9])
        bins = np.linspace(lo, hi, 60)
        ax.hist(d_old, bins=bins, density=True, color=C_OLD, alpha=0.35)
        ax.hist(d_old, bins=bins, density=True, histtype="step", color=C_OLD, linewidth=1.3)
        ax.hist(d_new, bins=bins, density=True, color=C_NEW, alpha=0.35)
        ax.hist(d_new, bins=bins, density=True, histtype="step", color=C_NEW, linewidth=1.5)
        ax.set_title(f"{label}\n{d_old.mean():+.3g} ± {d_old.std():.2g} → "
                     f"{d_new.mean():+.3g} ± {d_new.std():.2g} {unit}", color=INK, fontsize=8.5)
        style_axis(ax)
        ax.set_yticks([])
    for ax in axes.ravel()[len(po):]:
        ax.axis("off")
    axes.ravel()[0].legend([plt.Rectangle((0, 0), 1, 1, color=C_OLD, alpha=0.5),
                            plt.Rectangle((0, 0), 1, 1, color=C_NEW, alpha=0.5)],
                           ["baseline", "with calibration QC"], fontsize=8, frameon=False)
    fig.suptitle("atten_refl posteriors in physical units — baseline (grey) vs calibration QC (orange)\n"
                 "titles: baseline → QC, mean ± sd", color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)


def plot_prediction_difference(old_zarr: Path, new_zarr: Path, out: Path) -> pd.DataFrame:
    diffs, stats = {}, []
    for sheet in SHEETS:
        a = xr.open_zarr(old_zarr, group=sheet)
        b = xr.open_zarr(new_zarr, group=sheet)
        d = (b["pred_mean"] - a["pred_mean"]).load()
        diffs[sheet] = (d.values, d["x"].values, d["y"].values)
        v = d.values[np.isfinite(d.values)]
        s = (b["pred_std"] / a["pred_std"]).values
        stats.append({"sheet": sheet, "n_grid": int(v.size),
                      "median_shift_dB": round(float(np.median(v)), 2),
                      "p5_dB": round(float(np.percentile(v, 5)), 2),
                      "p95_dB": round(float(np.percentile(v, 95)), 2),
                      "median_pred_std_ratio": round(float(np.nanmedian(s)), 3)})
    lim = max(np.nanpercentile(np.abs(v[0]), 99) for v in diffs.values())
    fig = plt.figure(figsize=(16, 7))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.1, 0.75, 0.8])
    axes = [fig.add_subplot(gs[0, i]) for i in range(3)]
    for ax, sheet in zip(axes[:2], SHEETS):
        arr, x, y = diffs[sheet]
        im = ax.imshow(arr, cmap="PRGn", vmin=-lim, vmax=lim,
                       extent=[x[0], x[-1], y[-1], y[0]])
        ax.set_aspect("equal")
        ax.set_axis_off()
        ax.set_title(sheet, color=INK)
    fig.colorbar(im, ax=axes[:2], shrink=0.7, label="new − baseline posterior-mean RSSNR [dB]")
    ax = axes[2]
    bins = np.linspace(-lim, lim, 80)
    for sheet in SHEETS:
        v = diffs[sheet][0]
        v = v[np.isfinite(v)]
        ax.hist(v, bins=bins, density=True, histtype="step", linewidth=1.5,
                color=SHEET_COLOR[sheet], linestyle=LS_OBS,
                label=f"{sheet}: median {np.median(v):+.2f} dB")
    ax.axvline(0, color="0.4", linewidth=0.8)
    ax.set_xlabel("new − baseline predicted RSSNR [dB]", color=INK)
    ax.set_yticks([])
    ax.legend(fontsize=8, frameon=False)
    style_axis(ax)
    fig.suptitle("Change in predicted required surface SNR from the calibration QC filter (atten_refl)",
                 color=INK, fontsize=11)
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return pd.DataFrame(stats)


def plot_target_hist(old_split, new_split, target: str, out: Path):
    fig, ax = plt.subplots(figsize=(7.5, 4))
    bins = np.linspace(-20, 120, 71)
    for sheet in SHEETS:
        for split, lw, alpha, tag in ((old_split, 1.0, 0.5, "baseline"), (new_split, 1.8, 1.0, "QC")):
            v = split.loc[(split["ice_sheet"] == sheet) & (split["fold"] >= 0), target].dropna()
            ax.hist(v, bins=bins, histtype="step", color=SHEET_COLOR[sheet], linewidth=lw,
                    alpha=alpha, linestyle=LS_OBS,
                    label=f"{sheet} {tag}: n={len(v):,}, median {v.median():.1f} dB")
    ax.set_xlabel("training target: required surface SNR [dB]", color=INK)
    ax.set_ylabel("grid points", color=INK)
    ax.legend(fontsize=8, frameon=False)
    ax.set_title("Training targets before (thin) and after (thick) calibration QC", color=INK, fontsize=10)
    style_axis(ax)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="outputs/baseline_20260807")
    ap.add_argument("--new", default="outputs")
    ap.add_argument("--out", default="outputs/qc_filter")
    args = ap.parse_args()
    base, new, out = Path(args.baseline), Path(args.new), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_model_config("config/model.yaml")
    qc, target = cfg["split"]["calibration_qc"], cfg["split"]["target"]
    md = ["# Calibration QC filter: baseline vs new\n",
          f"QC config: `{json.dumps(qc)}`; excluded collections: "
          f"{cfg['split']['exclude_collections']}\n"]

    # 1. trace-level accounting (new parquets carry the calibration columns)
    traces = load_traces(new)
    tab = removal_by_season(traces, qc, cfg["split"]["exclude_collections"])
    tab.to_csv(out / "qc_removal_by_season.csv", index=False)
    show = tab.copy()
    for c in ["seam", "img2", "saturated", "any", "seam_unmeasured", "margin_unmeasured"]:
        show[c] = (100 * show[c]).round(1)
    (out / "qc_removal_by_season.md").write_text(show.to_markdown(index=False) + "\n")
    plot_removal(tab, qc, out / "qc_removal_by_season.png")
    sens = sensitivity_table(traces, qc)
    (out / "qc_sensitivity.md").write_text(sens.to_markdown(index=False) + "\n")
    md += ["## Traces rejected per season (%)\n", show.to_markdown(index=False), "",
           "## Threshold sensitivity (% of traces rejected)\n", sens.to_markdown(index=False), ""]

    # 2. metrics side by side
    runs = {(v, m): load_run(d / "model" / m)
            for v, d in (("baseline", base), ("QC", new)) for m in MODELS}
    bench = benchmark_rows(runs)
    (out / "benchmark_comparison.md").write_text(bench.to_markdown(index=False) + "\n")
    md += ["## Benchmark (each run scored on its own CV folds / test cells)\n",
           bench.to_markdown(index=False), ""]
    plot_cv_folds(runs, out / "cv_folds.png")

    # 3. same held-out points, both posteriors
    old_split = pd.read_parquet(base / "model" / "split.parquet")
    new_split = pd.read_parquet(new / "model" / "split.parquet")
    assert len(old_split) == len(new_split), "grid changed between runs"
    rows = []
    for set_name, split in (("baseline test set", old_split), ("QC test set", new_split)):
        pts = usable_points(split, cfg, split["is_test"])
        for (version, name), run in runs.items():
            s = score_points(run, pts, target, cfg["train"]["censoring"])
            rows.append({"test points": set_name, "n": len(pts), "model": name,
                         "posterior": version, **{k: round(v, 3) for k, v in s.items()}})
    same = pd.DataFrame(rows)
    (out / "same_points_test.md").write_text(same.to_markdown(index=False) + "\n")
    md += ["## Same test points, both posteriors (uncensored RMSE/MAE/coverage; "
           "log score over all points)\n", same.to_markdown(index=False), ""]

    # 4. posteriors, predictions, targets
    plot_posteriors(runs[("baseline", "atten_refl")][1], runs[("QC", "atten_refl")][1],
                    out / "posterior_comparison.png")
    shift = plot_prediction_difference(base / "model/atten_refl/predictions.zarr",
                                       new / "model/atten_refl/predictions.zarr",
                                       out / "prediction_difference.png")
    md += ["## Prediction shift (atten_refl, full grid)\n", shift.to_markdown(index=False), ""]
    plot_target_hist(old_split, new_split, target, out / "target_hist.png")
    n_old = int((old_split[target].notna() & (old_split["fold"] >= 0)).sum())
    n_new = int((new_split[target].notna() & (new_split["fold"] >= 0)).sum())
    changed = int(((old_split[target] != new_split[target]) & old_split[target].notna()
                   & new_split[target].notna()).sum())
    md += [f"Training grid points: {n_old} → {n_new}; {changed} kept points now match a "
           "different (QC-passing) trace.\n"]
    (out / "comparison.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
