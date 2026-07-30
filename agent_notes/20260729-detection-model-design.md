# Detection-aware SNR model (design note, 2026-07-29)

Motivation: the upcoming ASE reprocessing will include traces with *no bed pick*
in segments that have picks elsewhere — informative non-detections. Combined
with the existing saturation problem, this turns the model into a standard
selection model (Tobit type 2 / Heckman-style, occupancy-style detection) in
which the detection threshold is *learned*, not chosen.

## Latent structure

True required surface SNR at trace i:

    y*_i = mu(x_i) + eps_i,   eps_i ~ Normal(0, sigma)      (the existing regression)

Per-trace measurement ceiling (RSSNR when the bed peak equals the noise floor):

    C_i = y_obs_i + margin_i                (detected traces; identity we already use)
    C_i = f(surface_power, noise floor, geometry)   (undetected traces; see data notes)

True bed-echo margin over the noise floor in dB: m*_i = C_i − y*_i.

## Detection model (the learned threshold)

Human-in-the-loop picking has no hard threshold → model detection as a soft
probit in dB:

    P(detect_i | y*_i) = Phi( (C_i − y*_i − theta) / tau )

- theta: detection threshold — dB above the noise floor a bed echo needs before
  a picker will pick it. **Learned parameter** (prior ~ Normal(8, 5) from the
  margin diagnostics).
- tau: softness of the threshold — picker variability, spatial-continuity
  effects, peak-statistics of noise. **Learned** (HalfNormal(5)).
- Probit link is the natural choice given approximately Gaussian matched-filter
  noise; theta/tau absorb the details (peak-over-median statistics etc.) that
  we previously tried to fix as a hard margin threshold.
- Optional hierarchy: theta_c ~ Normal(theta0, sigma_theta) per campaign/season
  (different pickers, systems) — "threshold as a random variable" literally.

## Likelihood — three data classes, all closed-form

1. **Detected, clean margin** (bed pick well above floor):

       L_i = Normal(y_i | mu_i, sigma) * Phi((C_i − y_i − theta)/tau)

   The second factor is the selection correction: detected traces are
   preferentially low-y*, and ignoring it biases mu downward near the
   detection limit (same pathology the Tobit fix addressed, now handled
   consistently).

2. **Detected, near the floor** (the current saturation case): the pick exists
   but the measured value is noise-contaminated. Keep the right-censored /
   saturating treatment for the *value* while the detection factor stays:

       L_i = P(y* >= y_i | mu_i, sigma) * Phi((C_i − y_i − theta)/tau)

   (or the smooth saturating operator y_obs = −10log10(10^(−y*/10) + 10^(−C_i/10)) + noise).
   With non-detections in the likelihood, theta/tau take over most of the work
   the hard margin_threshold_dB used to do.

3. **Undetected** (no bed pick, segment otherwise picked): marginalize the
   latent y* analytically using the Gaussian–probit identity
   ∫ Normal(y; mu, sigma) · Phi(a + b·y) dy = Phi((a + b·mu)/sqrt(1 + b²sigma²)):

       P(no detect_i) = 1 − Phi( (C_i − theta − mu_i) / sqrt(tau² + sigma²) )
                      = Phi( (mu_i − (C_i − theta)) / sqrt(tau² + sigma²) )

   **No per-trace latent variables needed** — each missing trace contributes one
   probit-regression-like term. Sampling stays cheap and NUTS-friendly.

## PyMC sketch

```python
with pm.Model() as m:
    mu = build_mean_function(X_all)            # linear / atten_refl, as today
    sigma = pm.HalfNormal("sigma", 1)
    theta = pm.Normal("theta", 8, 5)           # dB
    tau = pm.HalfNormal("tau", 5)              # dB

    # detected, clean: observed values + selection factor
    pm.Normal("obs", mu=mu[det], sigma=sigma, observed=y_det)
    pm.Potential("sel", pm_logcdf_normal((C_det - y_det - theta) / tau).sum())

    # undetected: closed-form marginal probability
    z = (mu[miss] - (C_miss - theta)) / pt.sqrt(tau**2 + sigma**2)
    pm.Potential("miss", pm_logcdf_normal(z).sum())
```

(Work in dB space for C/theta/tau; apply the target normalizer's affine
transform consistently to y, mu, C so priors stay interpretable.)

## Data requirements from the reprocessing (per no-pick trace)

- Surface pick + surface power (needed for C_i) — available (surface always picked).
- A noise-floor estimate: post-bed window is undefined without a pick → use the
  **pre-surface window for both classes** when computing the C_i that feeds the
  detection term (or model a fixed offset between windows); mixing windows
  between classes would leak a systematic offset into theta.
- Geometry: C_i's spreading correction needs ice thickness, unknown without a
  bed pick → use BedMachine thickness (already a covariate; 10% thickness error
  is <1 dB in the 20log10 spreading term).
- A per-trace flag distinguishing "picker looked, no bed" from "not picked for
  other reasons". Restricting to segments with picks elsewhere approximates
  this; consider dropping gaps at segment edges (picker attention artifacts)
  and keeping interior gaps (strong evidence of low SNR).

## Planned: empirical detection curves (pre-modeling check)

Before fitting anything, validate the floor-relative detection assumption
directly: bin traces by measured margin (bed_power − noise_floor, using the
same noise window for picked and unpicked traces), compute the **detected
fraction per bin**, and overlay the resulting empirical probit curves
**per season/campaign** (convention: these are survey-level curves, not
sheet-level — use non-sheet colors).

What to look for:
- Curves from different surveys should roughly coincide when plotted against
  margin despite very different absolute noise floors — that validates
  defining theta relative to the local floor.
- Residual *horizontal* offsets identify campaigns needing their own
  hierarchical theta_c (and estimate the offsets); differing *slopes* argue
  for per-campaign tau (noise texture / incoherent-averaging differences).
- The transition location gives the empirical prior for theta; its width, for tau.

Do this twice:
1. **Reprocessed ASE set** (as soon as it's ready) — first real non-detection
   data; single system, so curves should nearly overlay. Establishes the method.
2. **Full datasets** (when reprocessing extends beyond ASE) — the important
   test: many systems/seasons (P3/DC8/Basler...) means much larger spread; this
   is what determines whether hierarchical theta_c is needed and how wide
   sigma_theta should be.

Note the dependency: detected *fraction* requires no-pick traces in the store,
which only the reprocessing provides. Until then, the only weak proxy on the
full datasets is the per-season lower edge of the margin distribution of
picked traces (where picks stop appearing ~ theta + a few tau).

## Empirical findings — reprocessed ASE (2026-07-30, snapshot CD6RRCZPA6N0VSKTCFK0)

`scripts/noise_metric_comparison.py`, population qc_surface_pass &
bed_pick_attempted: 23,215 traces, 340 non-detections (1.5%).

- **Pre-surface noise is unusable as an at-bed floor**: median +56 dB above
  post-bed noise, wildly season-dependent (+32 to +83 dB medians), and it
  implies *negative* margins for beds that were confidently detected. Early-
  record gain/blanking artifacts, not thermal floor.
- **Record-tail noise is the right pick-independent metric**: sits 0-15 dB
  *below* post-bed (median -12.5 dB; post-bed likely contains bed coda), flat
  vs bed depth (no sign of deep-return contamination in ASE), and detected-
  trace margins bottom out at ~+3-5 dB over the tail floor — exactly where the
  detection curves start rolling off. Use record-tail for the detection model
  (theta, tau, C_i); post-bed stays a picked-trace-only diagnostic.
- **Unit shift**: margins vs the tail floor are ~12 dB larger than vs post-bed;
  any threshold calibrated against post-bed (e.g. margin_threshold_dB=10)
  becomes ~22 in tail units. Recalibrate from the detection curves, don't reuse.
- Caveats: only 340 non-detections (sparse sigmoid statistics), and the
  detection-curve x uses along-track *interpolated* bed power, which is
  optimistic for missing traces (true bed power presumably lower) — flattens
  the empirical transition; the model itself does not have this bias.

## Empirical findings — ASE v2 (snapshot MDXN4Y1A3B6CCYC9HBY0, 2026-07-30)

`scripts/detection_curves.py`. New pick-free window stats verified:
`post_bed_noise_interp_dB` == `post_bed_noise_dB` where picks exist; per-trace
peak-over-median delta median +9.5 dB (picked traces).

- **The censoring population is now directly visible**: missing-trace margins
  (interp bed power − at-depth noise) are bimodal — a sharp mode at 0–8 dB
  (genuine low-SNR non-detections, roughly a third of the 340) and a broad
  40–120 dB population (the margin-independent pi component). Missing traces'
  window delta is elevated (median ~20 dB vs 9.5): many "missing" windows
  contain real energy (bed below the interpolated depth in valleys, scatter),
  further evidence most ASE gaps are not SNR-limited.
- **Detection curves finally roll off** (to ~0.75–0.85 below ~10 dB margin),
  but theta/tau remain unidentified by MLE on ASE alone: in-sample detected
  fraction never drops below ~0.75, so theta and tau trade off (degenerate fits,
  e.g. theta -> -100 for 2014). Expected — ASE is a bright-bed survey. The
  Bayesian model's priors will regularize this; real identification comes from
  low-SNR surveys (S. Greenland). Working numbers: transition onset ~5–10 dB
  over the at-depth floor, tau ~ several dB, pi ~ 1%.
- Prefer `post_bed_noise_interp_dB` (at-depth, pick-free) as the detection
  noise reference; record-tail stays as the sanity/fallback floor.
- Still missing from the store before large reprocessing: **record-end twtt**
  (needed to validate the tail metric and window placement for deep beds —
  matters for Greenland, non-issue in ASE).

## Empirical findings — ASE v3 (snapshot 690DKWTHN1BQDZYDJEW0): record-end headroom

`scripts/record_end_check.py`. Headroom = record_end_twtt − bed twtt.

- ASE has ample headroom (medians 38–47 us; <1.3% of traces under 10 us).
- **Tail contamination extends well beyond the 5 us window**: tail noise is
  elevated ~40 dB at headroom 15–17 us (2018, the only season with enough
  low-headroom traces to bin) and flat/clean above ~19 us. Bed *coda* trailing
  the bed by >10 us reaches the tail window long before the bed itself does.
- **Guard**: trust record_tail_noise_dB only where headroom > ~20 us; below
  that, fall back to post_bed_noise_interp_dB (or flag the trace). Apply this
  everywhere record_end_twtt exists — essential for deep-bed Greenland, where
  low headroom will be common rather than rare.

## Identifiability / validation

- theta vs mu intercept: separable because C_i varies trace-to-trace and the
  detected/undetected mix as a function of (C_i − mu_i) traces out the probit.
- tau vs sigma: sigma is pinned by scatter of detected values; tau only by the
  fuzziness of the detect/no-detect transition.
- Validate with the existing semi-synthetic machinery: simulate detection with
  known (theta*, tau*) on high-margin obs, check recovery; compare against the
  hard-threshold Tobit on the ASE test set once reprocessed.
- The threshold-sensitivity analysis (20260728) showed the hard threshold trades
  saturated-region correction against clean-region overshoot with no natural
  choice point — this model replaces that knob with two learned, physically
  interpretable parameters constrained by the non-detection pattern itself.
