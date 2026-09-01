"""Site-selection robustness grid for the exponent fits.

Crosses {within-season sites, cross-season sites} x {>= 2 levels,
>= 3 levels} for both channels, all with the adopted primary treatments
(10 dB headroom + gate screen for surface, censored+pedestal for bed,
StudentT, all altitudes, delta_high). Cross-season fits add gamma_season
calibration offsets (see exponent_bayes.fit).

Requires model_table.parquet AND model_table_cross.parquet
(build_model_table.py [--cross-season]).

Usage: uv run python scripts/multi_altitude_crossovers/robustness_site_selection.py
Outputs: outputs/multi_altitude_crossovers/{robustness_site_selection.csv,
         robustness_site_selection_posteriors.png}
"""

import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from scipy.stats import gaussian_kde  # noqa: E402

from plot_style import LS_PRED, INK, style_axis  # noqa: E402
import exponent_bayes as eb  # noqa: E402

OUT = eb.OUT_DIR
COMBO_COLORS = {("within", 2): "tab:orange", ("within", 3): "tab:purple",
                ("cross", 2): "tab:red", ("cross", 3): "tab:brown"}


def main():
    tables = {"within": pd.read_parquet(OUT / "model_table.parquet"),
              "cross": pd.read_parquet(OUT / "model_table_cross.parquet")}
    rows, draws = [], {}
    for scope, t in tables.items():
        for min_lev in (2, 3):
            for channel in ("surface", "bed"):
                if channel == "surface":
                    d = eb.prepare(t, "r_surf", "surface_power_dB", gate=True,
                                   sat_levels=True,
                                   min_headroom=eb.HEADROOM_DB)
                    pcol, kw = "surface_power_dB", {}
                else:
                    d = eb.prepare(t, "r_bed_refr", "bed_power_dB",
                                   noise_floor=True)
                    pcol, kw = "bed_power_dB", {"censor": True}
                if min_lev == 3:
                    d = d[d["n_levels"] >= 3].copy()
                    d["site_idx"] = pd.factorize(d["site_id"])[0]
                name = f"{channel} / {scope}-season / >={min_lev} levels"
                idata = eb.fit(d, pcol, season_offset=(scope == "cross"),
                               draws=2000, tune=2000, **kw)
                row = eb.report(name, d, idata)
                row.update({"channel": channel, "scope": scope,
                            "min_levels": min_lev,
                            "n_seasons_mixed_sites": int(
                                (d.groupby("site_id")["season"].nunique() > 1)
                                .sum())})
                rows.append(row)
                draws[(channel, scope, min_lev)] = \
                    idata.posterior["x"].to_numpy().ravel()

    res = pd.DataFrame(rows)
    res.to_csv(OUT / "robustness_site_selection.csv", index=False)
    print("\nSummary:")
    print(res[["channel", "scope", "min_levels", "n_traces", "n_sites",
               "x_mean", "x_sd", "x_rhat", "x_ess_bulk"]]
          .round(3).to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=False)
    for ax, channel in zip(axes, ("surface", "bed")):
        for (scope, ml), color in COMBO_COLORS.items():
            xs = draws[(channel, scope, ml)]
            grid = np.linspace(xs.min() - 0.05, xs.max() + 0.05, 400)
            pdf = gaussian_kde(xs)(grid)
            ax.plot(grid, pdf, LS_PRED, color=color, lw=1.7,
                    label=f"{scope}-season, $\\geq${ml} levels: "
                          f"{xs.mean():.2f} $\\pm$ {xs.std():.2f}")
            ax.fill_between(grid, pdf, color=color, alpha=0.10)
        for k in (2, 3):
            ax.axvline(k, color="0.85", lw=0.9, zorder=0)
        ax.set_title(f"{channel} exponent", fontsize=11, color=INK)
        ax.set_xlabel("spreading exponent $x$", color=INK)
        ax.legend(fontsize=8)
        style_axis(ax)
    axes[0].set_ylabel("posterior density", color=INK)
    fig.suptitle("Robustness: site scope $\\times$ minimum altitude levels "
                 "(10 dB headroom, refraction-corrected bed R)",
                 fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(OUT / "robustness_site_selection_posteriors.png", dpi=140)
    print(f"\nfigure -> {OUT / 'robustness_site_selection_posteriors.png'}")


if __name__ == "__main__":
    main()
