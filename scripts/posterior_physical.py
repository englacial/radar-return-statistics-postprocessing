"""Posterior distributions of every learned parameter, in physical units.

Reads the self-describing posterior (normalizer constants in attrs) and
converts draws out of z-space:
  attenuation side: x sigma_target / sigma_thickness x 1000 -> dB/km (two-way)
  reflectivity side: x (-sigma_target) -> dB contribution to RSSNR (the model
    subtracts refl, so the sign flip shows the effect on required SNR)
  sigma: x sigma_target -> dB;  theta, tau: already dB.
Covariate panels are labelled per original covariate unit. The spare
panel carries the headline CV / test RMSE from metrics.json.

Usage: uv run python scripts/posterior_physical.py
"""

import json

import arviz as az
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import INK, style_axis  # noqa: E402

MODEL = "atten_refl"
COV_UNITS = {"era5_t2m_mean_K": "K", "surface_v_m_yr": "m/yr", "ghf_mW_m2": "mW/m²"}


def main():
    idata = az.from_netcdf(f"outputs/model/{MODEL}/posterior.nc")
    metrics = json.load(open(f"outputs/model/{MODEL}/metrics.json"))
    norm = json.loads(idata.attrs["normalizer"])
    sy = norm["required_surface_snr_dB"]["std"]
    st = norm["bedmachine_thickness_m"]["std"]
    post = idata.posterior.to_dataset() if hasattr(idata.posterior, "to_dataset") \
        else idata.posterior
    post = post.stack(sample=("chain", "draw"))

    SHORT = {"era5_t2m_mean_K": "T_air", "surface_v_m_yr": "speed",
             "ghf_mW_m2": "GHF", "is_greenland": "greenland",
             "is_floating": "floating"}

    INDICATOR_STEP = {"is_greenland": "\n(Greenland − Antarctica)",
                      "is_floating": "\n(floating − grounded)"}

    def per_unit(c):
        """(divisor, unit-suffix) converting a per-1sd effect to per-original-unit."""
        if c in INDICATOR_STEP:
            return 1.0, ""  # 0/1 contrast: the effect IS the step between the two states
        return norm[c]["std"], f"/{COV_UNITS[c]}"

    rate = sy / st * 1000.0  # z -> dB/km (two-way)
    panels = [("α_a — attenuation rate\n@ mean conditions",
               post["alpha_atten"].values * rate, "dB/km (2-way)")]
    for c in post["covariate"].values:
        sx, suffix = per_unit(c)
        panels.append((f"β_a[{SHORT[c]}] — Δ atten rate" + INDICATOR_STEP.get(c, ""),
                       post["beta_atten"].sel(covariate=c).values * rate / sx,
                       f"dB/km{suffix}"))
    panels.append(("−α_r — reflectivity term\n@ mean conditions",
                   -post["alpha_refl"].values * sy, "dB"))
    for c in post["covariate"].values:
        sx, suffix = per_unit(c)
        panels.append((f"−β_r[{SHORT[c]}] — Δ RSSNR via refl" + INDICATOR_STEP.get(c, ""),
                       -post["beta_refl"].sel(covariate=c).values * sy / sx,
                       f"dB{suffix}"))
    panels.append(("σ — residual scatter", post["sigma"].values * sy, "dB"))
    panels.append(("θ — detection threshold", post["theta"].values, "dB"))
    panels.append(("τ — picker softness", post["tau"].values, "dB"))

    fig, axes = plt.subplots(4, 4, figsize=(16.5, 11.5))
    for ax, (label, draws, unit) in zip(axes.ravel(), panels):
        ax.hist(draws, bins=60, density=True, color="tab:orange", alpha=0.35)
        ax.hist(draws, bins=60, density=True, histtype="step",
                color="tab:orange", linewidth=1.5)
        ax.axvline(draws.mean(), color=INK, linewidth=1.1)
        ax.set_title(f"{label}\n{draws.mean():+.3g} ± {draws.std():.2g} {unit}",
                     color=INK, fontsize=9)
        style_axis(ax)
        ax.set_yticks([])
    # spare panel: headline accuracy
    ax = axes.ravel()[len(panels)]
    ax.axis("off")
    cv = metrics["pooled_cv"]["rmse_dB"]
    ax.text(0.5, 0.65, f"CV RMSE (5-fold, spatial)\n{cv['mean']:.2f} dB "
            f"[{cv['min']:.2f}–{cv['max']:.2f}]",
            ha="center", va="center", fontsize=13, color=INK, weight="bold")
    ax.text(0.5, 0.25, f"held-out test RMSE\n{metrics['test']['rmse_dB']:.2f} dB",
            ha="center", va="center", fontsize=13, color=INK, weight="bold")
    for ax in axes.ravel()[len(panels) + 1:]:
        ax.axis("off")
    fig.suptitle(f"Posterior distributions in physical units — {MODEL} "
                 "(+ censoring + detection)\nrates are two-way; covariate effects per original unit", color=INK, fontsize=12)
    fig.tight_layout()
    out = "outputs/model/analysis/posterior_physical.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
