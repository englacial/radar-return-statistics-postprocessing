"""Empirical saturation onset vs the param-derived clip thresholds.

Assesses (a) whether the param-derived S (saturation_levels.csv, ADC full
scale in product units) is a useful ceiling, and (b) how much margin below S
is needed before the chain is linear — earlier components (LNA/IF) may
compress before the ADC clips, so proximity-to-S alone may understate
saturation.

Two probes, both on gate-valid (img_comb window) surface data:
1. Headroom histograms per season: headroom = S - P. A hard ceiling at S
   shows a sharp right edge at 0 with pile-up; an analog compression floor
   shows the edge at positive headroom; a season that never approaches S is
   uninformative about the ceiling but safe.
2. Pairwise crossover exponents vs headroom: for each site, each pair of
   passes >= MIN_VSEP m apart, x_pair = -dP / (10 dlog10 R). Compression of
   the brighter (usually lower) trace attenuates dP, flattening x_pair. The
   headroom of the brighter member at which median x_pair starts dropping is
   the required margin. Confound: headroom correlates with range and season,
   so the trend is also reported per sheet and for the DC-8-only subset.

Usage: uv run python scripts/multi_altitude_crossovers/saturation_margin_analysis.py
Outputs: outputs/multi_altitude_crossovers/saturation_margin.png + printed stats
"""

import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import C_ANT, C_GRL, C_OTHER, INK, style_axis  # noqa: E402

OUT = Path("outputs/multi_altitude_crossovers")
MIN_VSEP_M = 200.0
SAT_FLOOR_S = 2.5e-6
HEAD_BINS = [-3, 0, 3, 6, 10, 15, 20, 30, 60]


def load_gate_valid():
    """All gate-valid surface traces from the full stores, with S_dB."""
    w = pd.read_csv(OUT / "img_comb_windows.csv")
    sl = pd.read_csv(OUT / "saturation_levels.csv")
    frames = []
    for store in ["antarctica", "greenland"]:
        df = pd.read_parquet(
            f"outputs/{store}/{store}.parquet",
            columns=["frame_id", "collection", "surface_twtt",
                     "surface_power_dB", "elevation", "surface_elevation"])
        df = df[df["surface_power_dB"].notna()]
        df["segment"] = df["frame_id"].str.rsplit("_", n=1).str[0]
        df = df.rename(columns={"collection": "season"})
        df = df.merge(w[["season", "segment", "surf_valid_min_safe_s",
                         "surf_valid_max_safe_s"]],
                      on=["season", "segment"], how="inner")
        lo = df["surf_valid_min_safe_s"].clip(lower=SAT_FLOOR_S)
        df = df[(df["surface_twtt"] >= lo)
                & (df["surface_twtt"] <= df["surf_valid_max_safe_s"])]
        df = df.merge(sl[["season", "segment", "S_dB"]],
                      on=["season", "segment"], how="inner")
        df = df[df["S_dB"].notna()]
        df["sheet"] = store
        frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["headroom"] = d["S_dB"] - d["surface_power_dB"]
    d["h_agl"] = d["elevation"] - d["surface_elevation"]
    return d


def headroom_report(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for season, g in d.groupby("season"):
        q = g["headroom"].quantile([0.001, 0.01, 0.5])
        rows.append({
            "season": season, "n": len(g),
            "edge_p0.1_dB": round(q[0.001], 1),
            "p1_dB": round(q[0.01], 1),
            "median_dB": round(q[0.5], 1),
            "pct_within_3dB": round(100 * (g["headroom"] < 3).mean(), 2),
            "pct_within_6dB": round(100 * (g["headroom"] < 6).mean(), 2),
            "pct_within_10dB": round(100 * (g["headroom"] < 10).mean(), 2),
        })
    return pd.DataFrame(rows).sort_values("edge_p0.1_dB")


def pairwise(d: pd.DataFrame) -> pd.DataFrame:
    """Pairwise exponents from model-table crossover sites."""
    t = pd.read_parquet(OUT / "model_table.parquet")
    t = t[t["surface_gate_ok"] & t["surface_power_dB"].notna()]
    t["segment"] = t["frame_id"].str.rsplit("_", n=1).str[0]
    sl = pd.read_csv(OUT / "saturation_levels.csv")
    t = t.merge(sl[["season", "segment", "S_dB"]], on=["season", "segment"],
                how="inner")
    t = t[t["S_dB"].notna()]
    per_pass = (t.groupby(["site_id", "sheet", "season", "frame_id"])
                .agg(P=("surface_power_dB", "median"),
                     R=("r_surf", "median"), S=("S_dB", "first"))
                .reset_index())
    rows = []
    for (site, sheet, season), g in per_pass.groupby(
            ["site_id", "sheet", "season"]):
        g = g.sort_values("R").reset_index(drop=True)
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = g.iloc[i], g.iloc[j]
                if abs(b["R"] - a["R"]) < MIN_VSEP_M:
                    continue
                dlog = np.log10(b["R"] / a["R"])
                x_pair = -(b["P"] - a["P"]) / (10 * dlog)
                bright = a if a["P"] > b["P"] else b
                rows.append({"sheet": sheet, "season": season,
                             "x_pair": x_pair, "dlogR": dlog,
                             "headroom_bright": bright["S"] - bright["P"]})
    return pd.DataFrame(rows)


def knee_table(p: pd.DataFrame, label: str):
    b = pd.cut(p["headroom_bright"], HEAD_BINS)
    tab = p.groupby(b, observed=True)["x_pair"].agg(
        n="size", median="median",
        q25=lambda v: v.quantile(0.25), q75=lambda v: v.quantile(0.75))
    print(f"\n{label}: median pairwise exponent vs headroom of brighter trace")
    print(tab.round(2).to_string())
    return tab


def main():
    d = load_gate_valid()
    print(f"{len(d)} gate-valid surface traces with known S")
    hr = headroom_report(d)
    print("\nPer-season headroom vs param S (sorted by observed edge):")
    print(hr.to_string(index=False))

    p = pairwise(d)
    print(f"\n{len(p)} crossover pass-pairs")
    tab_all = knee_table(p, "ALL")
    for sheet in ["antarctica", "greenland"]:
        knee_table(p[p["sheet"] == sheet], sheet)
    knee_table(p[p["season"].str.contains("DC8")], "DC-8 seasons only")

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
    ax = axes[0]
    interesting = hr[hr["edge_p0.1_dB"] < 12]["season"]
    for i, season in enumerate(interesting):
        g = d[d["season"] == season]
        ax.hist(g["headroom"], bins=np.arange(-5, 40, 1), density=True,
                histtype="step", lw=1.4, label=f"{season} (n={len(g)})")
    ax.axvline(0, color=INK, lw=1.2)
    ax.text(0.3, ax.get_ylim()[1] * 0.97 if ax.get_ylim()[1] > 0 else 1,
            "param S", fontsize=8, color=INK, va="top")
    ax.set_xlabel("headroom below param S [dB]", color=INK)
    ax.set_ylabel("density", color=INK)
    ax.set_title("Headroom distributions (seasons approaching S)",
                 fontsize=10, color=INK)
    ax.legend(fontsize=7)
    style_axis(ax)

    ax = axes[1]
    centers = [(HEAD_BINS[i] + HEAD_BINS[i + 1]) / 2
               for i in range(len(HEAD_BINS) - 1)]
    for sub, color, label in [
            (p[p["sheet"] == "antarctica"], C_ANT, "antarctica"),
            (p[p["sheet"] == "greenland"], C_GRL, "greenland"),
            (p, C_OTHER, "all")]:
        b = pd.cut(sub["headroom_bright"], HEAD_BINS)
        g = sub.groupby(b, observed=False)["x_pair"]
        med = g.median().to_numpy()
        lo_q = g.quantile(0.25).to_numpy()
        hi_q = g.quantile(0.75).to_numpy()
        n = g.size().to_numpy()
        ok = n >= 15
        ax.errorbar(np.array(centers)[ok], med[ok],
                    yerr=[med[ok] - lo_q[ok], hi_q[ok] - med[ok]],
                    fmt="o-", ms=4, lw=1.2, capsize=2, color=color,
                    label=label)
    for k in (2, 3):
        ax.axhline(k, color="0.85", lw=0.8, zorder=0)
    ax.set_xlabel("headroom of brighter trace below param S [dB]", color=INK)
    ax.set_ylabel("pairwise exponent $x$", color=INK)
    ax.set_title("Crossover-pair exponent vs headroom\n"
                 "(compression flattens x at low headroom)",
                 fontsize=10, color=INK)
    ax.legend(fontsize=8)
    style_axis(ax)
    fig.tight_layout()
    fig.savefig(OUT / "saturation_margin.png", dpi=140)
    print(f"\nfigure -> {OUT / 'saturation_margin.png'}")


if __name__ == "__main__":
    main()
