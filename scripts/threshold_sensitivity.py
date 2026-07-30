"""Sensitivity of the censoring margin threshold (train.censoring.margin_threshold_dB).

For thresholds [off, 5, 10, 15, 20] dB, fit atten_refl with spatially-blocked
5-fold CV and a final full fit, then compare on subsets that are FIXED across
thresholds (so numbers are comparable):
  - clean subset  (obs_margin > 20 dB): held-out RMSE + bias — should not degrade
  - saturated set (obs_margin < 10 dB): mean held-out prediction — shows how far
    the correction lifts predictions where saturation lives
  - full-grid predictions: mean per region + S. Greenland prediction CDF

Outputs: printed table + outputs/model/analysis/threshold_sensitivity.{json,png}
Usage: uv run python scripts/threshold_sensitivity.py
"""

import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from radar_postproc.config import load_model_config  # noqa: E402
from radar_postproc.models import get_model  # noqa: E402
from radar_postproc.models.normalize import (  # noqa: E402
    apply_normalizer,
    fit_normalizer,
    invert_normalizer,
)
from radar_postproc.train import _design_matrix  # noqa: E402

from plot_style import C_ANT, C_GRL, INK, LS_PRED, style_axis  # noqa: E402

TARGET = "required_surface_snr_dB"
MODEL = "atten_refl"
THRESHOLDS = [0.0, 5.0, 10.0, 15.0, 20.0]  # 0 = censoring off
CLEAN_MARGIN = 20.0    # fixed evaluation subset (trustworthy obs)
SATURATED_MARGIN = 10.0  # fixed probe subset (saturation lives here)
SAMPLER = dict(draws=500, tune=500, chains=2, seed=0)


def main():
    config = load_model_config("config/model.yaml")
    features = list(config["train"]["features"])
    feature_names = [*features, "is_greenland"]
    df = pd.read_parquet("outputs/model/split.parquet")
    df["is_greenland"] = (df["ice_sheet"] == "greenland").astype("float64")

    thick_ok = ~(df["bedmachine_thickness_m"] < config["train"]["min_thickness_m"])
    usable = df[TARGET].notna() & df[features].notna().all(axis=1) & thick_ok
    train_df = df[usable & (df["fold"] >= 0)].copy()
    predictable = df[df[features].notna().all(axis=1) & thick_ok].copy()
    margin = train_df["obs_margin_dB"].to_numpy()
    clean = margin > CLEAN_MARGIN
    saturated = margin < SATURATED_MARGIN
    s_grl = (predictable["ice_sheet"] == "greenland") & (predictable["latitude"] < 72)
    print(f"train n={len(train_df)}  clean(margin>{CLEAN_MARGIN:.0f}) n={clean.sum()}  "
          f"saturated(margin<{SATURATED_MARGIN:.0f}) n={saturated.sum()}")

    results = []
    grid_preds = {}
    for thr in THRESHOLDS:
        cens_all = (margin < thr) & np.isfinite(margin)
        oof = np.full(len(train_df), np.nan)
        for k in sorted(train_df["fold"].unique()):
            tr_idx = (train_df["fold"] != k).to_numpy()
            tr, va = train_df[tr_idx], train_df[~tr_idx]
            norm = fit_normalizer(tr, [*features, TARGET])
            y = apply_normalizer(tr[TARGET].to_numpy(), norm[TARGET])
            cens = cens_all[tr_idx]
            upper = np.where(cens, y, np.inf) if cens.any() else None
            model = get_model(MODEL)
            idata = model.fit(_design_matrix(tr, features, norm), y, feature_names,
                              upper=upper, **SAMPLER)
            mu, _, _ = model.predict(idata, _design_matrix(va, features, norm))
            oof[~tr_idx] = invert_normalizer(mu, norm[TARGET])

        # Final fit on all folds -> full-grid predictions.
        norm = fit_normalizer(train_df, [*features, TARGET])
        y = apply_normalizer(train_df[TARGET].to_numpy(), norm[TARGET])
        upper = np.where(cens_all, y, np.inf) if cens_all.any() else None
        model = get_model(MODEL)
        idata = model.fit(_design_matrix(train_df, features, norm), y, feature_names,
                          upper=upper, **SAMPLER)
        mu, _, _ = model.predict(idata, _design_matrix(predictable, features, norm),
                                 batch_size=200_000)
        grid_pred = invert_normalizer(mu, norm[TARGET])
        grid_preds[thr] = grid_pred

        err_clean = oof[clean] - train_df.loc[clean, TARGET].to_numpy()
        row = {
            "threshold_dB": thr,
            "n_censored_train": int(cens_all.sum()),
            "clean_rmse_dB": float(np.sqrt(np.mean(err_clean**2))),
            "clean_bias_dB": float(err_clean.mean()),
            "mean_oof_pred_saturated_dB": float(np.nanmean(oof[saturated])),
            "mean_grid_pred_antarctica_dB": float(
                grid_pred[(predictable["ice_sheet"] == "antarctic").to_numpy()].mean()),
            "mean_grid_pred_greenland_dB": float(
                grid_pred[(predictable["ice_sheet"] == "greenland").to_numpy()].mean()),
            "mean_grid_pred_s_greenland_dB": float(grid_pred[s_grl.to_numpy()].mean()),
        }
        results.append(row)
        print(f"thr={thr:4.0f}  censored={row['n_censored_train']:5d}  "
              f"clean rmse={row['clean_rmse_dB']:.2f} bias={row['clean_bias_dB']:+.2f}  "
              f"sat-pred={row['mean_oof_pred_saturated_dB']:.1f}  "
              f"S.Grl grid={row['mean_grid_pred_s_greenland_dB']:.1f}")

    table = pd.DataFrame(results)
    out_dir = Path("outputs/model/analysis")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "threshold_sensitivity.json").write_text(json.dumps(
        {"model": MODEL, "sampler": SAMPLER, "clean_margin_dB": CLEAN_MARGIN,
         "saturated_margin_dB": SATURATED_MARGIN, "results": results}, indent=2))

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    thr = table["threshold_dB"]

    ax = axes[0]
    ax.plot(thr, table["clean_rmse_dB"], color=INK, linewidth=2, marker="o",
            label="held-out RMSE (clean subset)")
    ax.plot(thr, table["clean_bias_dB"], color=INK, linewidth=2, marker="o",
            linestyle="--", label="held-out bias (clean subset)")
    ax.axhline(0, color="0.8", linewidth=1)
    style_axis(ax)
    ax.set_xlabel("margin threshold [dB]", color=INK)
    ax.set_ylabel("dB", color=INK)
    ax.set_title("(a) fixed clean subset: no degradation allowed", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    ax = axes[1]
    ax.plot(thr, table["mean_grid_pred_antarctica_dB"], color=C_ANT, linewidth=2,
            linestyle=LS_PRED, marker="o", label="Antarctica grid mean")
    ax.plot(thr, table["mean_grid_pred_greenland_dB"], color=C_GRL, linewidth=2,
            linestyle=LS_PRED, marker="o", label="Greenland grid mean")
    ax.plot(thr, table["mean_grid_pred_s_greenland_dB"], color=C_GRL, linewidth=2,
            linestyle=LS_PRED, marker="s", alpha=0.55, label="S. Greenland grid mean")
    ax.plot(thr, table["mean_oof_pred_saturated_dB"], color=INK, linewidth=2,
            linestyle=LS_PRED, marker="^",
            label=f"held-out pred, saturated obs (margin<{SATURATED_MARGIN:.0f})")
    style_axis(ax)
    ax.set_xlabel("margin threshold [dB]", color=INK)
    ax.set_ylabel("mean predicted required SNR [dB]", color=INK)
    ax.set_title("(b) prediction shift vs threshold", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=8)

    ax = axes[2]
    greens = plt.cm.Greens(np.linspace(0.35, 0.95, len(THRESHOLDS)))
    for color, t in zip(greens, THRESHOLDS):
        v = np.sort(grid_preds[t][s_grl.to_numpy()])
        ax.plot(v, np.arange(1, len(v) + 1) / len(v), color=color, linewidth=2,
                linestyle=LS_PRED, label=f"threshold {t:.0f} dB")
    style_axis(ax)
    ax.set_xlabel("predicted required SNR [dB]", color=INK)
    ax.set_ylabel("cumulative fraction", color=INK)
    ax.set_title("(c) S. Greenland grid prediction CDF by threshold", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9)

    fig.tight_layout()
    fig.savefig(out_dir / "threshold_sensitivity.png", dpi=150, bbox_inches="tight")
    print(f"wrote {out_dir}/threshold_sensitivity.json and threshold_sensitivity.png")


if __name__ == "__main__":
    main()
