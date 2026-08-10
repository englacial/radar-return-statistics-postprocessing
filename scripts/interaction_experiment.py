"""Does giving Greenland its own covariate slopes fix the missing north-south gradient?

Compares a shared-slope control run (`outputs/exp_shared/`, sheet indicator as a
level offset only) against the canonical fit (`outputs/model/`, which sets
`train.interactions: [is_greenland]` and so gives Greenland its own slopes on every
non-thickness covariate).

Build the control first:
    uv run python scripts/interaction_experiment.py --build-control

Reports headline accuracy, the Greenland latitude gradient each run reproduces,
and the fitted per-sheet slopes in physical units.

Usage: uv run python scripts/interaction_experiment.py
"""

import argparse
import json
import shutil
from pathlib import Path

import arviz as az
import matplotlib
import numpy as np
import pandas as pd
import scipy.stats as st
import xarray as xr

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import (  # noqa: E402
    C_ANT, C_GRL, C_OTHER, C_OTHER2, INK, LS_OBS, LS_PPC, LS_PRED, style_axis,
)

RUNS = [("shared slopes", Path("outputs/exp_shared/model"), C_OTHER),
        ("sheet-specific slopes", Path("outputs/model"), C_OTHER2)]
CONTROL = Path("outputs/exp_shared")
MODEL = "atten_refl"
OUT = Path("outputs/model/analysis")


def load(root: Path) -> pd.DataFrame:
    df = pd.read_parquet(root / "split.parquet")
    for sheet in ("antarctic", "greenland"):
        ds = xr.open_zarr(root / MODEL / "predictions.zarr", group=sheet)
        rows = (df["ice_sheet"] == sheet).to_numpy()
        iy, ix = df.loc[rows, "grid_iy"].to_numpy(), df.loc[rows, "grid_ix"].to_numpy()
        df.loc[rows, "pred"] = ds["pred_mean"].values[iy, ix]
    return df


def metrics_row(tag: str, root: Path) -> dict:
    m = json.load(open(root / MODEL / "metrics.json"))
    cv = m["pooled_cv"]
    return {
        "run": tag,
        # alpha_a + alpha_r + sigma + theta + tau, plus beta_atten/beta_refl for
        # every covariate (all design columns except thickness, which multiplies).
        "n_params": (len(json.loads(
            az.from_netcdf(root / MODEL / "posterior.nc").attrs["features"])) - 1) * 2 + 5,
        "CV RMSE": round(cv["rmse_dB"]["mean"], 3),
        "CV range": f"{cv['rmse_dB']['min']:.2f}-{cv['rmse_dB']['max']:.2f}",
        "CV cov": round(cv["coverage_1sigma"]["mean"], 3),
        "test RMSE": round(m["test"]["rmse_dB"], 3),
        "test cov": round(m["test"]["coverage_1sigma"], 3),
        "ND logscore": round(m["detection"].get("cv_nd_logscore", float("nan")), 3),
        "divergences": sum(f["divergences"] for f in m["folds"]) + m["test"]["divergences"],
        "rhat": round(max([f["rhat_max"] for f in m["folds"]] + [m["test"]["rhat_max"]]), 4),
    }


def per_sheet_rmse(tag: str, df: pd.DataFrame) -> list[dict]:
    rows = []
    o = df[df["required_surface_snr_dB"].notna() & df["pred"].notna()]
    for sheet, s in o.groupby("ice_sheet"):
        for held, sub in s.groupby("is_test"):
            e = sub["pred"] - sub["required_surface_snr_dB"]
            rows.append({"run": tag, "sheet": sheet,
                         "rows": "held-out test" if held else "other",
                         "n": len(sub), "RMSE": round(float(np.sqrt((e ** 2).mean())), 3),
                         "bias": round(float(e.mean()), 3)})
    return rows


def gradients(preds: dict) -> pd.DataFrame:
    rows = []
    ref = preds[RUNS[0][0]]
    o = ref[(ref.ice_sheet == "greenland") & ref.required_surface_snr_dB.notna()]
    r = st.linregress(o.latitude, o.required_surface_snr_dB)
    rows.append({"series": "observed", "slope_dB_per_deg": round(r.slope, 3),
                 "r": round(r.rvalue, 3), "captured": "—"})
    for tag, _, _ in RUNS:
        d = preds[tag]
        g = d[(d.ice_sheet == "greenland") & d.required_surface_snr_dB.notna() & d.pred.notna()]
        rr = st.linregress(g.latitude, g.pred)
        rows.append({"series": f"predicted, {tag}", "slope_dB_per_deg": round(rr.slope, 3),
                     "r": round(rr.rvalue, 3),
                     "captured": f"{100 * rr.slope / r.slope:.0f}%"})
    return pd.DataFrame(rows)


def sheet_slopes(root: Path) -> pd.DataFrame | None:
    """Per-sheet covariate slopes in physical units, if the run fitted them."""
    idata = az.from_netcdf(root / MODEL / "posterior.nc")
    names = json.loads(idata.attrs["features"])
    inter = [n for n in names if n.startswith("is_greenland_x_")]
    if not inter:
        return None
    norm = json.loads(idata.attrs["normalizer"])
    sy = norm["required_surface_snr_dB"]["std"]
    st_ = norm["bedmachine_thickness_m"]["std"]
    post = idata.posterior
    post = (post.to_dataset() if hasattr(post, "to_dataset") else post).stack(
        sample=("chain", "draw"))
    rate = sy / st_ * 1000.0
    rows = []
    for name in inter:
        base = name.removeprefix("is_greenland_x_")
        sx = norm[base]["std"]
        for side, arr, scale, unit in [("atten", "beta_atten", rate, "dB/km"),
                                       ("refl", "beta_refl", -sy, "dB")]:
            b = post[arr].sel(covariate=base).values * scale / sx
            d = post[arr].sel(covariate=name).values * scale / sx
            rows.append({"covariate": base, "side": side,
                         "Antarctica": round(float(b.mean()), 4),
                         "Greenland": round(float((b + d).mean()), 4),
                         "offset": round(float(d.mean()), 4),
                         "P(offset>0)": round(float((d > 0).mean()), 3),
                         "unit": f"{unit}/{base}"})
    return pd.DataFrame(rows)


def ppc_figure(out_path: Path, seed: int = 0):
    """Observed vs full-grid posterior predictive per sheet, one panel per run.

    Same construction as prediction_summary_figures.py: one draw per predictable
    grid cell, mu + N(0,1)*pred_std. Colour encodes sheet, solid = observations,
    dotted = posterior predictive.
    """
    obs = pd.read_parquet("outputs/model/split.parquet",
                          columns=["ice_sheet", "required_surface_snr_dB"])
    bins = np.linspace(-40, 140, 90)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.6), sharey=True)
    stats = []
    for ax, (tag, root, _) in zip(axes, RUNS):
        rng = np.random.default_rng(seed)
        for sheet, color, label in [("antarctic", C_ANT, "Antarctica"),
                                    ("greenland", C_GRL, "Greenland")]:
            ds = xr.open_zarr(root / MODEL / "predictions.zarr", group=sheet)
            mu, sd = ds["pred_mean"].values, ds["pred_std"].values
            ok = np.isfinite(mu) & np.isfinite(sd)
            ppc = mu[ok] + rng.standard_normal(int(ok.sum())) * sd[ok]
            o = obs.loc[obs["ice_sheet"] == sheet, "required_surface_snr_dB"].dropna()
            ax.hist(o, bins=bins, density=True, histtype="step", linewidth=1.9,
                    color=color, linestyle=LS_OBS,
                    label=f"{label} observations (n={len(o):,})")
            ax.hist(ppc, bins=bins, density=True, histtype="step", linewidth=1.9,
                    color=color, linestyle=LS_PPC,
                    label=f"{label} posterior predictive (n={len(ppc):,})")
            q = np.percentile(ppc, [10, 50, 90])
            qo = np.percentile(o, [10, 50, 90])
            stats.append({"run": tag, "sheet": sheet,
                          "obs p10/p50/p90": f"{qo[0]:.0f} / {qo[1]:.0f} / {qo[2]:.0f}",
                          "ppc p10/p50/p90": f"{q[0]:.0f} / {q[1]:.0f} / {q[2]:.0f}",
                          "ppc-obs median": round(q[1] - qo[1], 1),
                          "ppc sd": round(float(ppc.std()), 1),
                          "obs sd": round(float(o.std()), 1)})
        style_axis(ax)
        ax.set_xlabel("required surface SNR [dB]", color=INK)
        ax.set_title(tag, color=INK, fontsize=11)
        ax.legend(frameon=False, fontsize=8)
    axes[0].set_ylabel("density", color=INK)
    fig.suptitle("Observed training data vs full-grid posterior predictive, by ice sheet",
                 color=INK, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out_path}")
    return pd.DataFrame(stats)


def figure(preds: dict, out_path: Path):
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    bins = np.arange(60, 84, 2.0)
    mid = bins[:-1] + 1.0
    ref = preds[RUNS[0][0]]
    o = ref[(ref.ice_sheet == "greenland") & ref.required_surface_snr_dB.notna()]
    obs = [o.required_surface_snr_dB[(o.latitude >= a) & (o.latitude < b)].mean()
           for a, b in zip(bins[:-1], bins[1:])]
    ax.plot(mid, obs, color=C_GRL, linestyle=LS_OBS, linewidth=2.2, marker="o",
            label="observed (Greenland)")
    for tag, _, color in RUNS:
        d = preds[tag]
        g = d[(d.ice_sheet == "greenland") & d.required_surface_snr_dB.notna() & d.pred.notna()]
        pr = [g.pred[(g.latitude >= a) & (g.latitude < b)].mean()
              for a, b in zip(bins[:-1], bins[1:])]
        ax.plot(mid, pr, color=color, linestyle=LS_PRED, linewidth=2.0, marker="s",
                label=f"predicted, {tag}")
    style_axis(ax)
    ax.set_xlabel("latitude [°N]", color=INK)
    ax.set_ylabel("required surface SNR [dB]", color=INK)
    ax.legend(frameon=False, fontsize=9)
    fig.suptitle("Greenland north-south gradient: does a sheet-specific slope recover it?\n"
                 "green = Greenland observations (solid); dashed = model predictions,\n"
                 "orange = shared slopes, purple = sheet-specific slopes (current)",
                 color=INK, fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out_path}")


def build_control():
    """Refit atten_refl with interactions disabled, into a side out_dir."""
    import yaml

    from radar_postproc.train import run_train
    (CONTROL / "model").mkdir(parents=True, exist_ok=True)
    for f in ("split.parquet", "split.manifest.json", "grid.manifest.json"):
        shutil.copy(Path("outputs/model") / f, CONTROL / "model" / f)
    cfg = yaml.safe_load(open("config/model.yaml"))
    cfg["train"]["interactions"] = []
    cfg["train"]["models"] = [{"name": MODEL}]
    (CONTROL / "model.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    run_train(str(CONTROL / "model.yaml"), model_name=MODEL, out_dir=str(CONTROL))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-control", action="store_true",
                    help="refit the shared-slope control before comparing")
    args = ap.parse_args()
    if args.build_control:
        build_control()
    OUT.mkdir(parents=True, exist_ok=True)
    preds = {tag: load(root) for tag, root, _ in RUNS}

    print("=== headline accuracy ===")
    print(pd.DataFrame([metrics_row(tag, root) for tag, root, _ in RUNS]).to_markdown(index=False))
    print("\n=== per-sheet fit against observations ===")
    rows = [r for tag, _, _ in RUNS for r in per_sheet_rmse(tag, preds[tag])]
    print(pd.DataFrame(rows).pivot_table(
        index=["sheet", "rows", "n"], columns="run", values=["RMSE", "bias"]).to_markdown())
    print("\n=== Greenland latitude gradient ===")
    print(gradients(preds).to_markdown(index=False))
    slopes = sheet_slopes(RUNS[1][1])
    if slopes is not None:
        print("\n=== fitted per-sheet covariate slopes (physical units) ===")
        print(slopes.to_markdown(index=False))
    figure(preds, OUT / "greenland_gradient_interactions.png")
    s = ppc_figure(OUT / "ppc_sheets_interactions.png")
    print("\n=== posterior predictive vs observed, by sheet ===")
    print(s.to_markdown(index=False))


if __name__ == "__main__":
    main()
