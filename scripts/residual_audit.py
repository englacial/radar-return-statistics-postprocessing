"""Out-of-fold residual audit by season/institution (recommended model config).

Runs the spatially-blocked CV loop once (config/model.yaml: censoring,
detection, indicators, exclusions all respected via the train-stage helpers),
collects out-of-fold predictions for every observed grid point, and renders
residual (pred - obs) boxplots per source season, colored by institution.

Reconstructs a figure originally produced by a one-off script (lost to /tmp
cleanup); re-running reflects the current config, including any
split.exclude_collections.

Usage: uv run python scripts/residual_audit.py
"""

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from radar_postproc.config import load_model_config  # noqa: E402
from radar_postproc.models import get_model  # noqa: E402
from radar_postproc.models.normalize import invert_normalizer  # noqa: E402
from radar_postproc.train import (  # noqa: E402
    _censored_mask,
    _design_matrix,
    _fit_and_eval,
    add_indicator_columns,
    select_nondetects,
)

from plot_style import INK, style_axis  # noqa: E402

MODEL = "atten_refl"
C_INST = {"CReSIS": "tab:orange", "UTIG": "tab:purple"}


def main():
    config = load_model_config("config/model.yaml")
    tcfg = config["train"]
    target = config["split"]["target"]
    features = list(tcfg["features"])
    indicators = tuple(tcfg.get("indicators", ["is_greenland"]))

    df = pd.read_parquet("outputs/model/split.parquet")
    add_indicator_columns(df, list(indicators))  # whatever config asks for
    thick_ok = (~(df["bedmachine_thickness_m"] < tcfg["min_thickness_m"])
                if tcfg["min_thickness_m"] is not None else pd.Series(True, index=df.index))
    usable = df[target].notna() & df[features].notna().all(axis=1) & thick_ok
    train_df = df[usable & (df["fold"] >= 0)].reset_index(drop=True)
    nd_ok = df[features].notna().all(axis=1) & thick_ok
    nd_all = (df[df["is_nondetect"] & (df["fold"] >= 0) & nd_ok]
              if "is_nondetect" in df else df.iloc[0:0])

    sampler = dict(draws=tcfg["draws"], tune=tcfg["tune"],
                   chains=tcfg["cv_chains"], seed=tcfg["seed"])
    oof = np.full(len(train_df), np.nan)
    for k in sorted(train_df["fold"].unique()):
        tr_idx = (train_df["fold"] != k).to_numpy()
        tr, va = train_df[tr_idx], train_df[~tr_idx]
        nd_k = None
        if tcfg["detection"]["enabled"] and len(nd_all):
            nd_k, _ = select_nondetects(nd_all[nd_all["fold"] != k], tcfg["detection"])
        model = get_model(MODEL)
        idata, norm, _ = _fit_and_eval(model, tr, None, features, target, sampler,
                                       tcfg["censoring"], detection=tcfg["detection"],
                                       nd_train=nd_k, indicators=indicators)
        mu, _, _ = model.predict(idata, _design_matrix(va, features, norm, indicators))
        oof[~tr_idx] = invert_normalizer(mu, norm[target])
        print(f"fold {k}: {len(va)} OOF predictions")

    clean = ~_censored_mask(train_df, tcfg["censoring"])
    aud = train_df[clean].copy()
    aud["resid"] = oof[np.asarray(clean)] - aud[target].to_numpy()
    aud = aud[aud["collection"].notna()]

    seasons = sorted(aud["collection"].unique())
    data = [aud.loc[aud["collection"] == s, "resid"].to_numpy() for s in seasons]
    colors = [C_INST.get(aud.loc[aud["collection"] == s, "institution"].iloc[0], "tab:gray")
              for s in seasons]

    fig, ax = plt.subplots(figsize=(1.0 * len(seasons) + 4, 6.5))
    bp = ax.boxplot(data, patch_artist=True, showfliers=False, whis=(2, 98))
    for i, (patch, med, c, r) in enumerate(zip(bp["boxes"], bp["medians"], colors, data)):
        patch.set_facecolor(c)
        patch.set_alpha(0.45)
        med.set_color(c)
        ax.annotate(f"{np.median(r):+.1f}", (i + 1, np.median(r)),
                    textcoords="offset points", xytext=(0, 6),
                    ha="center", fontsize=8, color=INK)
    ax.axhline(0, color="0.4", linewidth=1)
    ax.set_xticks(range(1, len(seasons) + 1),
                  [f"{s}\n(n={len(d)})" for s, d in zip(seasons, data)],
                  rotation=45, ha="right", fontsize=8)
    style_axis(ax)
    ax.set_ylabel("OOF residual (pred − obs) [dB]", color=INK)
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=c, alpha=0.45)
               for c in C_INST.values()]
    ax.legend(handles, C_INST.keys(), frameon=False, fontsize=9)
    excl = config["split"]["exclude_collections"]
    ax.set_title(f"Out-of-fold residuals by season (uncensored points; {MODEL}, "
                 f"indicators={list(indicators)}"
                 + (f"; excluded: {', '.join(excl)}" if excl else "") + ")",
                 color=INK, fontsize=11)
    fig.tight_layout()
    out = "outputs/model/analysis/residuals_by_season.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"wrote {out}")
    print(aud.groupby("institution")["resid"].agg(["mean", "std", "count"]).round(2))


if __name__ == "__main__":
    main()
