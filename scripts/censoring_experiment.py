"""Semi-synthetic censoring experiment: does the Tobit likelihood recover truth?

Ground truth = training obs with a comfortable noise-floor margin
(obs_margin_dB > 20), where the recorded RSSNR is trustworthy. We artificially
right-censor them at a per-sheet ceiling (75th percentile of the true values),
fit the linear model both ways on the corrupted data — naive (censored values
treated as exact) and Tobit (censored values as lower bounds) — with the usual
spatially-blocked folds, and score held-out predictions against the KNOWN truth.

Outputs: printed table + outputs/model/analysis/censoring_experiment.{json,png}

Usage: uv run python scripts/censoring_experiment.py
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

from plot_style import C_OTHER, C_OTHER2  # noqa: E402

C_NAIVE = C_OTHER
C_TOBIT = C_OTHER2
INK = "#3a3f45"

TARGET = "required_surface_snr_dB"
TRUTH_MARGIN_DB = 20.0
CEILING_QUANTILE = 0.75
SAMPLER = dict(draws=500, tune=500, chains=2, seed=0)


def main():
    config = load_model_config("config/model.yaml")
    features = list(config["train"]["features"])
    df = pd.read_parquet("outputs/model/split.parquet")
    df["is_greenland"] = (df["ice_sheet"] == "greenland").astype("float64")

    usable = (df[TARGET].notna() & df[features].notna().all(axis=1)
              & (df["fold"] >= 0)
              & ~(df["bedmachine_thickness_m"] < config["train"]["min_thickness_m"]))
    truth = df[usable & (df["obs_margin_dB"] > TRUTH_MARGIN_DB)].copy()
    print(f"truth set: {len(truth)} high-margin obs "
          f"(of {int(usable.sum())} usable training points)")

    # Artificial right-censoring at a per-sheet ceiling.
    truth["y_true"] = truth[TARGET]
    ceiling = truth.groupby("ice_sheet")["y_true"].transform(
        lambda s: s.quantile(CEILING_QUANTILE))
    truth["y_obs"] = np.minimum(truth["y_true"], ceiling)
    truth["censored"] = truth["y_true"] > ceiling
    print(f"artificially censored: {int(truth['censored'].sum())} "
          f"({truth['censored'].mean():.1%}) at per-sheet ceiling "
          f"q{CEILING_QUANTILE:.0%}")

    feature_names = [*features, "is_greenland"]
    rows = []
    preds = {"naive": [], "tobit": []}
    truths, cens_flags = [], []
    for k in sorted(truth["fold"].unique()):
        tr = truth[truth["fold"] != k]
        va = truth[truth["fold"] == k]
        norm = fit_normalizer(tr.assign(**{TARGET: tr["y_obs"]}), [*features, TARGET])
        X = _design_matrix(tr, features, norm)
        y = apply_normalizer(tr["y_obs"].to_numpy(), norm[TARGET])
        Xv = _design_matrix(va, features, norm)

        for mode in ("naive", "tobit"):
            model = get_model("linear")
            upper = (np.where(tr["censored"].to_numpy(), y, np.inf)
                     if mode == "tobit" else None)
            idata = model.fit(X, y, feature_names, upper=upper, **SAMPLER)
            mu, _, _ = model.predict(idata, Xv)
            pred = invert_normalizer(mu, norm[TARGET])
            preds[mode].append(pred)
            err = pred - va["y_true"].to_numpy()
            cens = va["censored"].to_numpy()
            rows.append({
                "fold": int(k), "mode": mode, "n": len(va),
                "n_censored": int(cens.sum()),
                "rmse_all": float(np.sqrt(np.mean(err**2))),
                "bias_all": float(err.mean()),
                "rmse_censored": float(np.sqrt(np.mean(err[cens] ** 2))),
                "bias_censored": float(err[cens].mean()),
            })
        truths.append(va["y_true"].to_numpy())
        cens_flags.append(va["censored"].to_numpy())

    results = pd.DataFrame(rows)
    summary = results.groupby("mode")[
        ["rmse_all", "bias_all", "rmse_censored", "bias_censored"]].mean().round(2)
    print("\nheld-out metrics vs KNOWN truth (mean over folds):")
    print(summary.to_string())

    out_dir = Path("outputs/model/analysis")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"truth_margin_dB": TRUTH_MARGIN_DB, "ceiling_quantile": CEILING_QUANTILE,
               "sampler": SAMPLER, "n_truth": len(truth),
               "n_censored": int(truth["censored"].sum()),
               "folds": rows, "summary": summary.to_dict()}
    (out_dir / "censoring_experiment.json").write_text(json.dumps(payload, indent=2))

    # Truth vs prediction for the artificially censored held-out points.
    y_true = np.concatenate(truths)
    cens = np.concatenate(cens_flags)
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), sharex=True, sharey=True)
    for ax, mode, color in [(axes[0], "naive", C_NAIVE), (axes[1], "tobit", C_TOBIT)]:
        pred = np.concatenate(preds[mode])
        ax.scatter(y_true[~cens], pred[~cens], s=4, alpha=0.25, color="0.75",
                   rasterized=True, label="uncensored")
        ax.scatter(y_true[cens], pred[cens], s=6, alpha=0.5, color=color,
                   rasterized=True, label="artificially censored")
        lims = [y_true.min() - 5, y_true.max() + 5]
        ax.plot(lims, lims, color="0.4", linewidth=1, linestyle=":")
        s = summary.loc[mode]
        ax.set_title(f"{mode}: censored-subset bias {s['bias_censored']:+.1f} dB, "
                     f"RMSE {s['rmse_censored']:.1f} dB", color=INK, fontsize=11)
        ax.set_xlabel("true required SNR [dB]", color=INK)
        ax.grid(True, color="0.9", linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.legend(frameon=False, fontsize=9, loc="upper left")
    axes[0].set_ylabel("held-out prediction [dB]", color=INK)
    fig.suptitle("Semi-synthetic censoring: held-out predictions vs known truth (linear)",
                 color=INK, fontsize=13)
    fig.tight_layout()
    fig.savefig(out_dir / "censoring_experiment.png", dpi=150, bbox_inches="tight")
    print(f"\nwrote {out_dir}/censoring_experiment.json and censoring_experiment.png")


if __name__ == "__main__":
    main()
