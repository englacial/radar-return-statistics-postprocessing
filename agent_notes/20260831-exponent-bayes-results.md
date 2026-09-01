# Range-exponent Bayes fit — first results (2026-08-31)

Scripts: `scripts/multi_altitude_crossovers/{build_model_table.py, exponent_bayes.py}`
Outputs: `outputs/multi_altitude_crossovers/{model_table.parquet, posterior_summary.csv, x_posteriors.png, centered_scatter.png}`

## Stage 1 — modeling table

Relaxed search (>= 2 passes, >= 200 m vertical sep, 500 m ball, single
collection) with greedy disjoint partitioning (seeds accepted in descending
level count, claiming their neighborhoods):

- 2250 sites / 5729 traces. Antarctica 1854 / 4404, Greenland 396 / 1325.
- Dominant seasons: 2016_Antarctica_DC8 (949 sites), 2013_Antarctica_Basler
  (453), 2014_Greenland_P3 (189).
- 238 sites have >= 3 levels. Regime split: 3640 low (< 3 km AGL), 2089 high.

## Stage 2 — fits (all StudentT nu=4 unless noted; low-altitude only; 4 chains,
target_accept 0.9; every fit: rhat = 1.00, 0 divergences, ess_bulk > 3900)

| fit | n traces / sites | x (mean +/- sd) | sigma [dB] |
|---|---|---|---|
| synthetic, true x = 2.5 | 3005 / 1068 | 2.521 +/- 0.025 | 2.8 |
| surface, r_surf | 3005 / 1068 | **1.432 +/- 0.039** | 4.1 |
| bed, r_bed_geom, margin > 10 dB | 2280 / 759 | **2.762 +/- 0.090** | 3.9 |
| surface, Normal lik. | 3005 / 1068 | 1.479 +/- 0.057 | 8.4 |
| bed, Normal lik. | 2280 / 759 | 2.829 +/- 0.097 | 5.6 |
| bed, r_bed_refr (h + d/1.78) | 2280 / 759 | 2.190 +/- 0.072 | 3.9 |
| surface, >= 3-level sites only | 209 / 51 | **2.178 +/- 0.096** | 2.2 |
| bed, >= 3-level sites only | 194 / 50 | 3.062 +/- 0.262 | 2.7 |

## Read-out

- Synthetic recovery on the real geometry works (2.52 vs true 2.5, within 1 sd).
- **Bed**: x ~ 2.8 (geometric R), robust to Normal vs StudentT (2.76 -> 2.83)
  and to the >= 3-level restriction (3.06 +/- 0.26). The data support ~1/R^3
  and clearly reject 1/R^2 and 1/R^4 for the geometric-range parameterization.
  With refraction-corrected range the same signal reads x = 2.19 — the quoted
  exponent depends on which R you use, so any comparison to radar-equation
  forms must fix the R convention first.
- **Surface — the surprise**: the full fit (dominated by ~1000 2-level sites)
  gives x = 1.43, well below even 1/R^2, but the >= 3-level subset gives
  2.18 +/- 0.10 with less than half the residual scatter (2.2 vs 4.1 dB).
  The 2-pass pairs are noisier and pull the slope down; candidate mechanisms:
  surface saturation at low altitude (flattens the surface falloff, cf.
  saturation_diagnostics), and pair-level systematics that a 2-point site
  cannot self-diagnose. The bed shows the same direction of shift
  (2.76 -> 3.06) but much weaker. Until this is resolved, the >= 3-level
  estimates (x_surf ~ 2.2, x_bed ~ 3.1) are the more trustworthy numbers, and
  headline x_surf = 1.43 should NOT be quoted without that caveat.
- Sensitivity to the likelihood is small (< 0.07 in x); StudentT mainly
  shrinks sigma by absorbing outliers (there is a ~-65 dB surface outlier
  cluster from Greenland visible in the scatter).
- Precision comfortably beats the plan's success criterion (sd <= 0.2 for the
  primary fits), so the binding issue is now bias (2- vs 3-level discrepancy),
  not variance.

## Suggested next steps

- Stage 3 remainder: posterior predictive checks, leave-one-site-out / 70W-line
  collapse, per-site slope forest plot (would localize the 2-level bias).
- Cross-check surface saturation flags against the 2-level sites; consider a
  saturation-screened refit.
- Hierarchical x_i ~ N(x, tau) variant to quantify site-level slope spread.

## Update (2026-08-31, later): refraction-corrected range is now the primary convention

Decision: bed range is ALWAYS the refraction-corrected R = h + d/1.78,
matching docs/1_rssnr_background.md and mission_design_tool/physics.js
(spread = 20 log10(h / (h + d/n))). Geometric R demoted to a sensitivity.
exponent_bayes.py and site_plots.py updated; all figures regenerated.

Refit results in this convention (StudentT, low altitude):

- x_surf = 1.431 +/- 0.039 (unchanged; same 2-level caveat as above,
  >= 3-level sites give 2.18 +/- 0.10)
- x_bed  = 2.191 +/- 0.070 (was 2.76 in geometric R);
  >= 3-level sites: 2.41 +/- 0.21
- Sensitivities: Normal likelihood 2.25 +/- 0.08; geometric R 2.76 +/- 0.09.

Interpretation vs the RSSNR/mission-tool assumption of x = 2 on this R:
the bed exponent is mildly but significantly steeper than 2 in the pooled
fit (2.19 +/- 0.07), and consistent-with-to-slightly-above 2 on the cleaner
>= 3-level subset (2.41 +/- 0.21). The fit varies h at ~fixed d per site, so
it tests the altitude dependence of the spreading term, not the d-dependence.

## Update 2 (2026-08-31): surface gate QC + high-altitude legs included

Following claude_notes/20260831-dc8-gain-investigation.md: the 2-level x_surf
bias was the DC-8 low config's surface record gate (~8 us TWTT, ~1.2 km AGL) —
beyond it "surface power" is a noise floor. model_table now carries
`surface_gate_ok` (TWTT <= 7.7 us any platform; <= 72 us DC-8 high config;
non-DC8 high legs excluded, gates unverified: 4769/5729 traces pass). Primary
fits now use ALL altitudes with delta_high ~ N(0, 3 dB).

Refit (StudentT, refraction-corrected bed R):

- x_surf = 1.82 +/- 0.04 (delta_high = +0.4 +/- 0.5 dB, sigma 2.7 dB);
  >= 3-level sites: 1.97 +/- 0.07; low-only: 1.69 +/- 0.09
- x_bed  = 2.00 +/- 0.05 (delta_high = +3.0 +/- 0.5 dB, sigma 3.6 dB);
  >= 3-level sites: 2.27 +/- 0.11; low-only: 2.19 +/- 0.07
- The 2- vs 3-level discrepancy largely closed (was 1.43 vs 2.18 on surface).
- Headline: both channels now sit near the coherent x = 2 assumption of
  docs/1_rssnr_background.md and the mission design tool.

Caveats:
- Marginal mixing on the all-alt fits (rhat up to 1.03, ess ~150-450; x and
  delta_high trade off). Worth longer chains before quoting final numbers.
- delta_high = +3 dB on the bed channel (vs ~0 on surface) may be margin-
  censoring bias: weak high-altitude bed echoes fall below the 10 dB margin
  cut, so survivors read high. The >= 3-level bed delta (+6.4) likewise.
  A censored likelihood or lower margin cut would test this.
- Site example plots (site_plots.py) now blank gated surface points.

## Update 3 (2026-08-31): modeled saturation (surface) + censored noise floor (bed)

Likelihood changes (exponent_bayes.py):
- Surface: observed mean = softmin_k(mu, S_flight), k = 1/dB. S_flight must be
  ANCHORED above each flight's observed max (S = maxP + softplus(eta),
  hierarchical eta): a free hierarchical S is degenerate with alpha — the
  sampler declares whole flights clipped and x runs away (synthetic: 2.5 ->
  3.29; real data: x = 3.75, delta_high = +21.5 dB). Anchoring fixes this.
- Bed: margin cut replaced by signal-plus-noise pedestal mean
  softmax_{ln10/10}(mu, N_i) + left-censoring at the per-trace noise floor
  (pm.Censored). Picks with any margin now enter (4873 vs 4543 traces).

Results (all-alt, StudentT, refraction-corrected bed R):
- x_surf = 2.09 +/- 0.05, delta_high = +0.5 +/- 0.6 (plain same data: 1.72,
  delta -2.2). Low-only: 1.87 +/- 0.11 (plain: 1.31). >=3-level: 2.16 +/- 0.10.
  The three surface cuts now AGREE (1.9-2.2) where plain spanned 1.3-1.9.
- x_bed = 1.98 +/- 0.05, delta_high = +2.4 +/- 0.5 (margin-cut plain: 2.00,
  delta +3.0). Censoring trims but does not eliminate the bed regime offset.

Synthetic validation of the anchored saturating model (real geometry,
x_true = 2.5, S_true = -45):
- no clipping (4% near clip): x = 2.54 +/- 0.05 — unbiased, no harm.
- moderate (35% near clip): plain = 1.86, saturating = 2.00 — the model
  recovers only part of the clipping bias, because anchoring at the flight
  max overestimates S by the noise max-order-statistic (~3 dB at n~20).
- extreme (67%): fails low (0.97). Known limit.

Interpretation: the saturating estimates are a LOWER BOUND on x_surf with
bias shrinking as the true clipped fraction falls; plain fits are strictly
worse. Headroom diagnostic: S hugs maxP for all 174 flights (soft-clipping
the top tail always buys likelihood; sigma 2.47 -> 2.08), so it cannot count
"truly saturated" flights.

Next refinement (proposed): derive S per segment DETERMINISTICALLY from the
param files (receiver gain / ADC full scale, cf. adc_gains in the embedded
param structs) instead of estimating it — removes the identifiability
problem entirely. Alternatively subtract the expected noise max-order
statistic from the anchor.

## Update 4 (2026-08-31): param-derived S integrated; 2012 DC-8 verdict; b-offset tension

Fixed per-segment S (saturation_levels.csv) + one global offset b ~ N(0, 2):
- x_surf = 1.83 +/- 0.05 all-alt (b posterior = -5.44 +/- 0.27 (!), see below);
  low-only 1.77 +/- 0.08; >=3-level 1.89 +/- 0.09. Plain same data: 1.68.
- x_bed unchanged: 1.98 +/- 0.05 (censored+pedestal).
- Synthetic with b_true = 0 (19% clip): x recovered 2.68 vs 2.5 (3sigma high),
  b -0.58 — a FREE b introduces mild bias via b-x correlation.

Tension: the fit pulls b to -5.4 dB (2.7 sigma against its prior), but the
derivation note's independent validation found observed pile-ups sitting AT
the param S (-0.5..+2.7 dB) for genuinely saturated segments. So b = -5.4 is
likely the likelihood soft-clipping sub-ceiling data for variance gain (the
anchored-model pathology in milder form), not a real compression floor —
though an analog floor a few dB below ADC full scale (seen empirically only
in 2014_Greenland_P3) may contribute.

Important implication: at the TRUE param-derived S, only 0.7% of our
gate-valid surface traces are within 3 dB of clip (nearly all
2013_Greenland_P3). Genuine ADC saturation is RARE in the crossover sample —
so it is NOT the main cause of the persistently flat low-altitude 2-pass
surface slopes (x ~ 1.3-1.8 across every screen). Remaining candidates:
sub-window blanking/suppression, analog compression, or real short-range
physics (beam-/pulse-limited transition).

2012_Antarctica_DC8 (checked via xOPR + embedded params, this session):
NOT recoverable from posted data. param_csarp.radar.wfs carries NO gain
field (only Tpd/f0/f1/rx_paths/tukey/tx_weights; Vpp_scale = 2); products
date to Feb-2013 (svn rev 812 — the modern adc_gains-dividing loader chain
does not apply); the season posts no records/params dirs. Recovery would
need raw-data headers AND replication of the rev-812 normalization. Keep
2012 excluded from the surface channel (costs 26 sites / 53 traces).

Where the estimate stands (refraction-corrected R, doc/tool convention):
- Surface: x ~ 1.8-2.1, systematics-dominated (treatment of the sub-ceiling
  flattening decides where in that band). All treatments agree x < ~2.2.
- Bed: x = 1.98-2.29 (censored 1.98 primary; low-only 2.18; >=3-level 2.24;
  geometric-R 2.29) — robust across likelihoods, right at the assumed 2.
Recommended next: fix b (0 or a measured per-season floor), and chase the
residual low-altitude surface flattening as its own question.

## Update 5 (2026-08-31): empirical saturation onset vs param S — 3 dB is NOT enough

scripts/multi_altitude_crossovers/saturation_margin_analysis.py
(209,917 gate-valid surface traces; 1,949 crossover pass-pairs);
figure: saturation_margin.png.

Headroom edges (S - p99.9 of power) per season:
- 2013_GL_P3 -0.2 dB (91% of traces within 10 dB of S — deeply ceiling-bound),
  2017_GL_P3 -1.2 (thin clipped tail), 2014_GL_P3 +2.8 (analog floor ~3 dB
  below ADC, matching the derivation note's observation).
- All DC-8 seasons: edges +8..+11 dB; every other season >= 13 dB. Data
  never reach ADC full scale outside the Greenland P-3 seasons.

Pairwise crossover exponent vs headroom of the brighter trace (the direct
nonlinearity probe): x_pair is NEGATIVE at headroom 0-6 dB (-0.9, -0.4:
brighter trace suppressed below the dimmer one — hard compression), still
suppressed at 6-10 dB (0.3), fully recovered by 10-15 dB (2.9) and stable
beyond. All sub-10 dB pairs are Greenland P-3.

Verdict:
(a) Param-derived S IS useful: it locates the true ceiling within ~3 dB
    where data actually reach it, and correctly ranks seasons by risk. But
    it is an ADC-only number.
(b) 3 dB of margin is NOT enough: the receive chain (LNA/IF) goes nonlinear
    starting ~6-10 dB below ADC full scale. Required margin ~10 dB.
(c) Reinterpretation: the fit's S_offset = -5.4 dB was partly REAL (analog
    compression onset), not purely likelihood exploitation.
(d) Practical: a hard "headroom > 10 dB" screen removes 91% of 2013_GL_P3,
    10% of 2014_GL_P3, ~3% of 2017_GL_P3, and ~0.1-0.3% of everything else —
    i.e., it effectively just retires 2013_GL_P3. Antarctica's remaining
    low-altitude surface flatness is NOT near-ceiling data (its pairs at
    20-30 dB headroom give healthy x ~ 2.0), so that open question stands.

## Update 6 (2026-08-31): FINAL primary = 10 dB headroom screen

Primary surface treatment is now the hard screen: drop traces with
S_dB - P < 10 dB (HEADROOM_DB in exponent_bayes.py; justified by Update 5).
Removes only 151 traces (111 of them 2013_Greenland_P3, 40 2014_GL_P3), all
retained traces are in the verified linear regime, softmin retired from the
primary. Long-chain confirmation of the headline (3000 draws, target 0.95):
x_surf = 1.838 +/- 0.046, rhat 1.00, ess 516.

Final numbers (StudentT, all altitudes, refraction-corrected bed R):

  surface / headroom>=10:        1.84 +/- 0.05   (delta_high -1.0 +/- 0.6)
    low-only:                    1.79 +/- 0.08
    >=3-level sites:             1.90 +/- 0.10
    (no screen, comparison:      1.68 +/- 0.05)
  bed / censored+pedestal:       1.98 +/- 0.05   (delta_high +2.4 +/- 0.5)
    low-only:                    2.18 +/- 0.07
    >=3-level sites:             2.24 +/- 0.11
    geometric-R convention:      2.29 +/- 0.06

Reading: bed sits at the coherent x = 2 (doc/mission-tool assumption);
surface sits slightly BELOW 2 (1.8-1.9) consistently across cuts. Remaining
known systematics: the bed's +2.4 dB high-regime offset (partially but not
fully explained by margin censoring) and the surface's mild sub-2 flatness
(not saturation, not gate artifacts — possibly short-range physics or
residual sub-window suppression). Both channels comfortably exclude
1/R^3 and 1/R^4 under this convention.

### Addendum: 20 dB headroom sensitivity

Doubling the screen to 20 dB (drops 909 traces vs 151; now cutting into
2014_GL_P3 bulk and DC-8/Basler bright tails):
- all-alt: 1.85 +/- 0.05 (10 dB: 1.84) — unchanged; delta_high +0.2 (~0).
- low-only (requalified): 1.88 +/- 0.08 (10 dB: 1.79) — up ~1 sigma.
- >=3-level: 1.84 +/- 0.11 (10 dB: 1.90) — unchanged.
The surface exponent is INSENSITIVE to margin beyond 10 dB: the mild sub-2
value (~1.85) is not residual compression. (Caution for future refits: a
low-only cut must go through prepare(low_only=True) so sites requalify —
subsetting an all-alt table keeps <200 m-separated pairs and biases x low.)

## Update 7 (2026-08-31): 2x2 robustness grid (scope x min levels)

scripts/multi_altitude_crossovers/robustness_2x2.py; cross-season sites from
build_model_table.py --cross-season (partition across all seasons of a
store; fits add gamma_season ~ N(0,5) calibration offsets). All fits: 10 dB
headroom + gate screen (surface), censored+pedestal (bed), StudentT,
all altitudes, refraction-corrected bed R. Figure:
robustness_2x2_posteriors.png; table: robustness_2x2.csv.

  channel  scope   >=lev   n_traces/sites     x
  surface  within    2      3144 / 1294    1.83 +/- 0.05
  surface  within    3       673 /  229    1.89 +/- 0.10
  surface  cross     2      5727 / 2210    2.05 +/- 0.05
  surface  cross     3      1389 /  409    1.96 +/- 0.10
  bed      within    2      4873 / 1888    1.98 +/- 0.05
  bed      within    3       780 /  235    2.25 +/- 0.11
  bed      cross     2     15762 / 5556    1.79 +/- 0.04
  bed      cross     3      1874 /  483    1.83 +/- 0.10

Reading: all 8 posteriors lie in [1.79, 2.25]; every combination excludes
x = 3 and x = 4 decisively. The spread across analysis choices (~0.2)
exceeds the per-fit statistical error (~0.05), so the honest statement is
x_surf = 1.9 +/- 0.1 and x_bed = 2.0 +/- 0.2 with systematics dominating.
Patterns: cross-season pulls the surface UP (1.83 -> 2.05) and the bed DOWN
(1.98 -> 1.79) — season-calibration and site-population effects of similar
size but opposite sign per channel; the within/>=3 bed (2.25) remains the
high outlier (also carries the delta_high = +6 dB oddity).

(Rename, later on 2026-08-31: the "2x2" robustness grid is now called the
site-selection robustness grid — robustness_site_selection.py and
robustness_site_selection{.csv,_posteriors.png}. Same content.)
