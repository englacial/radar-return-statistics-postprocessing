"""Bayesian fit of the geometric-spreading exponent x in 1/R^x.

Model (per channel, fit separately):
    P_ij = alpha_i - 10 * x * (log10 R_ij - mean_i log10 R) + eps_ij
    eps ~ StudentT(nu=4, 0, sigma); x ~ N(3, 1.5); sigma ~ HalfN(5);
    alpha_i ~ N(site mean power, 20).
Surface channel uses R = r_surf and only gate-valid traces (surface_gate_ok:
the DC-8 low config stops recording the surface at ~8 us TWTT, so beyond it
"surface power" is a noise floor — see
claude_notes/20260831-dc8-gain-investigation.md). Bed channel uses R =
r_bed_refr, the refraction-corrected range h + d/1.78 — the convention of
docs/1_rssnr_background.md and mission_design_tool/physics.js — and traces
with bed margin > 10 dB.

PRIMARY FIT: all altitudes (the DC-8 high/low configs are radiometrically
consistent as posted), with a free per-regime offset delta_high ~ N(0, 3 dB)
as insurance on the attenuator-step accuracy. Low-altitude-only is kept as a
sensitivity. Sites are re-qualified after filtering (>= 2 passes >= 200 m
apart in height).

Saturation and censoring (see claude_notes/20260831-exponent-bayes-results.md):
- Surface: PRIMARY treatment is a hard headroom screen — keep only traces
  >= HEADROOM_DB below the param-derived per-segment clip level S
  (saturation_levels.csv: 20 log10(Vpp/2) - adc_gains_dB(wf1)). The margin
  is empirical (saturation_margin_analysis.py / results note Update 5): the
  receive chain (LNA/IF) compresses starting ~6-10 dB below ADC full scale —
  pairwise crossover exponents go NEGATIVE at < 6 dB headroom and only
  recover by 10-15 dB — so proximity-to-S alone (or a 3 dB margin) is not
  safe. With the screen in place every retained trace is in the linear
  regime, so the softmin saturation model (fit(saturate=True), kept for
  sensitivity work) is inert for the primary. Segments without a derivable S
  (2012 DC-8) are dropped from the surface channel. Blanking
  (attenuation at very short range) is NOT saturation and stays handled by
  the img_comb window minimum + SAT_FLOOR in build_model_table.
- Bed: the margin > 10 dB cut is dropped. Detected picks get a
  signal-plus-noise pedestal mean, softmax_{k0}(mu, N_i) with k0 = ln(10)/10
  fixed by physics, and are left-censored at the per-trace noise floor N_i
  (picks at/below N_i contribute the censored mass), removing the selection
  bias that inflated delta_high.

Runs a synthetic recovery check (known x = 2.5 on the real geometry) before
fitting real data, then sensitivity variants (Normal likelihood, r_bed_refr,
>= 3-level sites).

Usage: uv run python scripts/multi_altitude_crossovers/exponent_bayes.py
Outputs: outputs/multi_altitude_crossovers/{x_posteriors.png,
  centered_scatter.png, posterior_summary.csv}
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # for plot_style

import arviz as az
import matplotlib
import numpy as np
import pandas as pd
import pymc as pm
from scipy.stats import gaussian_kde

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import SHEET_COLOR, C_OTHER, C_OTHER2, LS_PRED, INK, style_axis  # noqa: E402

MIN_VSEP_M = 200.0
LOW_ALT_M = 3000.0
MIN_MARGIN_DB = 10.0
HEADROOM_DB = 10.0          # min margin below param clip level S (Update 5)
K_SAT = 1.0                 # softmin sharpness for receiver clipping [1/dB]
K_DB = np.log(10.0) / 10.0  # exact dB softmax sharpness for power addition
OUT_DIR = Path("outputs/multi_altitude_crossovers")
RNG = np.random.default_rng(20260831)


def greedy_nlevels(heights: np.ndarray) -> int:
    n, last = 0, -np.inf
    for v in np.sort(heights):
        if v - last >= MIN_VSEP_M:
            n, last = n + 1, v
    return n


def prepare(t: pd.DataFrame, rcol: str, pcol: str, margin: bool = False,
            gate: bool = False, low_only: bool = False,
            noise_floor: bool = False, sat_levels: bool = False,
            min_headroom: float | None = None) -> pd.DataFrame:
    """Filter one channel to valid traces; re-qualify sites."""
    d = t[t[pcol].notna() & (t[rcol] > 0)]
    if low_only:
        d = d[d["regime"] == "low"]
    if gate:
        d = d[d["surface_gate_ok"]]
    if margin:
        d = d[d["bed_margin_dB"] > MIN_MARGIN_DB]
    if noise_floor:  # censored-likelihood mode: keep all picks, need the floor
        d = d[d["bed_margin_dB"].notna()]
    d = d.copy()
    if noise_floor:
        d["noise_dB"] = d[pcol] - d["bed_margin_dB"]
    d["is_high"] = (d["regime"] == "high").astype(float)
    d["segment"] = d["frame_id"].str.rsplit("_", n=1).str[0]
    if sat_levels:  # attach param-derived clip levels; drop unknown-S segments
        sl = pd.read_csv(OUT_DIR / "saturation_levels.csv")
        d = d.merge(sl[["season", "segment", "S_dB"]],
                    on=["season", "segment"], how="left")
        n_unk = int(d["S_dB"].isna().sum())
        if n_unk:
            print(f"  [prepare] dropping {n_unk} traces from segments with "
                  f"unknown S_dB "
                  f"({sorted(d.loc[d['S_dB'].isna(), 'season'].unique())})")
        d = d[d["S_dB"].notna()]
        if min_headroom is not None:
            near = (d["S_dB"] - d[pcol]) < min_headroom
            if near.any():
                top = d.loc[near, "season"].value_counts().head(3).to_dict()
                print(f"  [prepare] dropping {int(near.sum())} traces with "
                      f"headroom < {min_headroom:.0f} dB (top: {top})")
            d = d[~near]
    # site still needs >= 2 passes with >= 200 m height separation
    lev = d.groupby("site_id").apply(
        lambda g: greedy_nlevels(
            g.groupby("frame_id")["r_surf"].median().to_numpy()),
        include_groups=False)
    d = d[d["site_id"].isin(lev[lev >= 2].index)].copy()
    d["logR"] = np.log10(d[rcol])
    grp = d.groupby("site_id")
    d["clogR"] = d["logR"] - grp["logR"].transform("mean")
    d["cP"] = d[pcol] - grp[pcol].transform("mean")
    d["site_idx"] = pd.factorize(d["site_id"])[0]
    return d


def fit(d: pd.DataFrame, pcol: str, likelihood: str = "studentt",
        saturate: bool = False, censor: bool = False,
        season_offset: bool = False,
        draws: int = 1000, tune: int = 1000):
    """One channel fit.

    saturate: observed mean = softmin_K_SAT(mu, S_flight), S_flight
      hierarchical per segment within season (surface clipping).
    censor: observed mean = softmax_K_DB(mu, noise_dB) (signal-plus-noise
      pedestal) and left-censored at noise_dB (bed detection floor).
    season_offset: add gamma_season ~ N(0, 5) calibration offsets — REQUIRED
      for cross-season site tables, where alpha_i no longer absorbs the
      inter-season gain difference. Identified by multi-season sites; for
      single-season sites gamma trades against alpha harmlessly (both proper).
    """
    import pytensor.tensor as pt

    site_idx = d["site_idx"].to_numpy()
    n_sites = int(site_idx.max()) + 1
    site_mean_p = d.groupby("site_idx")[pcol].mean().sort_index().to_numpy()
    with pm.Model() as model:
        x = pm.Normal("x", 3.0, 1.5)
        sigma = pm.HalfNormal("sigma", 5.0)
        alpha = pm.Normal("alpha", mu=site_mean_p, sigma=20.0, shape=n_sites)
        mu = alpha[site_idx] - 10.0 * x * d["clogR"].to_numpy()
        if d["is_high"].any():
            delta = pm.Normal("delta_high", 0.0, 3.0)
            mu = mu + delta * d["is_high"].to_numpy()

        if season_offset:
            sc, seasons = pd.factorize(d["season"])
            model.add_coord("season_c", list(seasons))
            gamma = pm.Normal("gamma_season", 0.0, 5.0, dims="season_c")
            mu = mu + gamma[sc]

        if saturate:
            # Param-derived deterministic clip level per segment
            # (saturation_levels.csv: S = 20 log10(Vpp/2) - adc_gains_dB(wf1),
            # exact product-units bookkeeping; see
            # claude_notes/20260831-saturation-level-derivation.md). A single
            # global offset absorbs the residual [-4, +5] dB onset/overshoot
            # regime; no per-flight estimation, no identifiability problem.
            S = d["S_dB"].to_numpy()
            b = pm.Normal("S_offset", 0.0, 2.0)
            mu = -pt.logaddexp(-K_SAT * mu, -K_SAT * (S + b)) / K_SAT

        obs = d[pcol].to_numpy()
        if censor:
            noise = d["noise_dB"].to_numpy()
            mu = pt.logaddexp(K_DB * mu, K_DB * noise) / K_DB
            base = (pm.StudentT.dist(nu=4, mu=mu, sigma=sigma)
                    if likelihood == "studentt"
                    else pm.Normal.dist(mu=mu, sigma=sigma))
            pm.Censored("P", base, lower=noise, upper=np.inf,
                        observed=np.maximum(obs, noise))
        elif likelihood == "studentt":
            pm.StudentT("P", nu=4, mu=mu, sigma=sigma, observed=obs)
        else:
            pm.Normal("P", mu=mu, sigma=sigma, observed=obs)
        idata = pm.sample(draws=draws,
                          tune=max(tune, 1500) if saturate else tune,
                          chains=4, cores=4,
                          target_accept=0.95 if saturate else 0.9,
                          random_seed=1, progressbar=False)
    return idata


def report(name: str, d: pd.DataFrame, idata) -> dict:
    s = az.summary(idata, var_names=["x", "sigma"]).apply(
        pd.to_numeric, errors="coerce")
    div = int(idata.sample_stats["diverging"].sum())
    xs = idata.posterior["x"].to_numpy().ravel()
    row = {"fit": name, "n_traces": len(d), "n_sites": d["site_idx"].nunique(),
           "x_mean": float(xs.mean()), "x_sd": float(xs.std()),
           "x_eti89_lb": s.loc["x", "eti89_lb"],
           "x_eti89_ub": s.loc["x", "eti89_ub"],
           "sigma_mean": s.loc["sigma", "mean"],
           "x_rhat": s.loc["x", "r_hat"], "x_ess_bulk": s.loc["x", "ess_bulk"],
           "divergences": div}
    if "delta_high" in idata.posterior:
        ds = idata.posterior["delta_high"].to_numpy().ravel()
        row["delta_high_mean"], row["delta_high_sd"] = float(ds.mean()), float(ds.std())
    print(f"{name}: n={row['n_traces']} traces / {row['n_sites']} sites | "
          f"x = {row['x_mean']:.3f} +/- {row['x_sd']:.3f} "
          f"[{row['x_eti89_lb']:.2f}, {row['x_eti89_ub']:.2f}] | "
          f"sigma = {row['sigma_mean']:.2f} dB | "
          f"rhat = {row['x_rhat']:.3f}, ess = {row['x_ess_bulk']:.0f}, "
          f"divergences = {div}"
          + (f" | delta_high = {row['delta_high_mean']:+.2f} "
             f"+/- {row['delta_high_sd']:.2f} dB"
             if "delta_high_mean" in row else ""))
    return row


def synthetic_check(d: pd.DataFrame) -> dict:
    """Simulate powers from the real surface geometry with known x = 2.5."""
    x_true, sigma_true = 2.5, 3.0
    sim = d.copy()
    alpha_true = RNG.normal(-40, 15, size=sim["site_idx"].max() + 1)
    sim["P_sim"] = (alpha_true[sim["site_idx"]]
                    - 10 * x_true * sim["clogR"]
                    + sigma_true * RNG.standard_t(4, size=len(sim)))
    idata = fit(sim, "P_sim")
    row = report("synthetic (x_true = 2.5)", sim, idata)
    ok = abs(row["x_mean"] - x_true) < 3 * row["x_sd"]
    print(f"  recovery {'OK' if ok else 'FAILED'}: true 2.5 vs "
          f"{row['x_mean']:.3f} +/- {row['x_sd']:.3f}")
    return row


def synthetic_check_sat(d: pd.DataFrame) -> dict:
    """As synthetic_check but clipped at the param-derived S (b_true = 0)."""
    x_true, sigma_true = 2.5, 3.0
    sim = d.copy()
    alpha_true = RNG.normal(-40, 15, size=sim["site_idx"].max() + 1)
    mu = (alpha_true[sim["site_idx"]] - 10 * x_true * sim["clogR"]).to_numpy()
    s = sim["S_dB"].to_numpy()
    clipped = -np.logaddexp(-K_SAT * mu, -K_SAT * s) / K_SAT
    sim["P_sim"] = clipped + sigma_true * RNG.standard_t(4, size=len(sim))
    frac = float((mu > s - 3).mean())
    idata = fit(sim, "P_sim", saturate=True)
    row = report("synthetic-sat (x=2.5, b=0)", sim, idata)
    b_hat = idata.posterior["S_offset"].mean().item()
    ok = abs(row["x_mean"] - x_true) < 3 * row["x_sd"]
    print(f"  recovery {'OK' if ok else 'FAILED'}: x {row['x_mean']:.3f} "
          f"+/- {row['x_sd']:.3f} (true 2.5), S_offset {b_hat:+.2f} (true 0), "
          f"{frac:.0%} of traces near/above clip")
    return row


def fig_posteriors(idata_surf, idata_bed):
    fig, ax = plt.subplots(figsize=(6.5, 4))
    for idata, label, color in [(idata_surf, "surface ($x_{surf}$)", C_OTHER),
                                (idata_bed, "bed ($x_{bed}$)", C_OTHER2)]:
        xs = idata.posterior["x"].to_numpy().ravel()
        grid = np.linspace(xs.min() - 0.05, xs.max() + 0.05, 400)
        pdf = gaussian_kde(xs)(grid)
        ax.plot(grid, pdf, LS_PRED, color=color, lw=1.8,
                label=f"{label}: {xs.mean():.2f} $\\pm$ {xs.std():.2f}")
        ax.fill_between(grid, pdf, color=color, alpha=0.15)
    for k in (2, 3, 4):
        ax.axvline(k, color="0.75", lw=0.9, zorder=0)
        ax.text(k, ax.get_ylim()[1] * 0.98, f"$1/R^{k}$", ha="center",
                va="top", fontsize=8, color="0.5")
    ax.set_xlabel("spreading exponent $x$", color=INK)
    ax.set_ylabel("posterior density", color=INK)
    ax.set_title("Posterior of the range exponent "
                 "(all altitudes, gate-QC'd surface)", fontsize=11, color=INK)
    ax.legend(fontsize=9)
    style_axis(ax)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "x_posteriors.png", dpi=140)
    plt.close(fig)


def fig_scatter(d_surf, d_bed, x_surf, x_bed):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.3), sharey=True)
    for ax, d, x, ttl in [(axes[0], d_surf, x_surf, "Surface"),
                          (axes[1], d_bed, x_bed, "Bed (refraction-corr. R)")]:
        for sheet, g in d.groupby("sheet"):
            ax.plot(g["clogR"], g["cP"], "o", ms=3, alpha=0.35,
                    color=SHEET_COLOR[sheet], mec="none", label=sheet,
                    rasterized=True)
        rr = np.array([d["clogR"].min(), d["clogR"].max()])
        ax.plot(rr, -10 * x * rr, LS_PRED, color=INK, lw=1.8,
                label=f"posterior mean $x$ = {x:.2f}")
        ax.set_title(f"{ttl} (n={len(d)}, {d['site_idx'].nunique()} sites)",
                     fontsize=10, color=INK)
        ax.set_xlabel("site-centered $\\log_{10} R$", color=INK)
        ax.legend(fontsize=8)
        style_axis(ax)
    axes[0].set_ylabel("site-centered power [dB]", color=INK)
    fig.suptitle("Per-site-centered power vs range "
                 "(all altitudes, gate-QC'd surface)", fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.99))
    fig.savefig(OUT_DIR / "centered_scatter.png", dpi=140)
    plt.close(fig)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t = pd.read_parquet(OUT_DIR / "model_table.parquet")
    rows = []

    d_surf = prepare(t, "r_surf", "surface_power_dB", gate=True,
                     sat_levels=True, min_headroom=HEADROOM_DB)
    d_bed = prepare(t, "r_bed_refr", "bed_power_dB", noise_floor=True)
    d_bed_m = prepare(t, "r_bed_refr", "bed_power_dB", margin=True)

    print("== synthetic recovery check (surface geometry) ==")
    rows.append(synthetic_check(d_surf))

    print(f"\n== primary fits (StudentT, all altitudes; surface headroom "
          f">= {HEADROOM_DB:.0f} dB, bed noise-censored) ==")
    id_surf = fit(d_surf, "surface_power_dB")
    rows.append(report("surface / headroom>=10 / all-alt", d_surf, id_surf))
    id_bed = fit(d_bed, "bed_power_dB", censor=True)
    rows.append(report("bed / censored+pedestal / all-alt", d_bed, id_bed))

    fig_posteriors(id_surf, id_bed)
    x_s = float(id_surf.posterior["x"].mean())
    x_b = float(id_bed.posterior["x"].mean())
    fig_scatter(d_surf, d_bed, x_s, x_b)

    print("\n== comparison: no headroom screen / margin-cut bed ==")
    d_surf_ns = prepare(t, "r_surf", "surface_power_dB", gate=True,
                        sat_levels=True)
    rows.append(report("surface / no headroom screen (comparison)", d_surf_ns,
                       fit(d_surf_ns, "surface_power_dB")))
    rows.append(report("bed / plain margin-cut (comparison)", d_bed_m,
                       fit(d_bed_m, "bed_power_dB")))

    print("\n== sensitivity variants ==")
    d_surf_low = prepare(t, "r_surf", "surface_power_dB", gate=True,
                         low_only=True, sat_levels=True,
                         min_headroom=HEADROOM_DB)
    rows.append(report("surface / headroom>=10 / low only", d_surf_low,
                       fit(d_surf_low, "surface_power_dB")))
    d_bed_low = prepare(t, "r_bed_refr", "bed_power_dB", noise_floor=True,
                        low_only=True)
    rows.append(report("bed / censored / low only", d_bed_low,
                       fit(d_bed_low, "bed_power_dB", censor=True)))
    d_bed_geom = prepare(t, "r_bed_geom", "bed_power_dB", noise_floor=True)
    rows.append(report("bed / censored / r_bed_geom", d_bed_geom,
                       fit(d_bed_geom, "bed_power_dB", censor=True)))
    for name, d, pcol, kw in [
            ("surface / headroom>=10 / >=3-level",
             d_surf[d_surf["n_levels"] >= 3], "surface_power_dB", {}),
            ("bed / censored / >=3-level",
             d_bed[d_bed["n_levels"] >= 3], "bed_power_dB", {"censor": True})]:
        d = d.copy()
        d["site_idx"] = pd.factorize(d["site_id"])[0]
        rows.append(report(name, d, fit(d, pcol, **kw)))

    pd.DataFrame(rows).to_csv(OUT_DIR / "posterior_summary.csv", index=False)
    print(f"\nsummary -> {OUT_DIR / 'posterior_summary.csv'}")


if __name__ == "__main__":
    main()
