# Should the BedMachine floating/grounded mask be an `atten_refl` covariate?

**Recommendation: yes.** Add `is_floating` (BedMachine mask 3) to
`train.indicators`. It removes an 8–18 dB systematic bias on ice shelves that
thickness does *not* already explain, cuts CV RMSE by 0.85 dB (14.958 → 14.108),
cuts held-out test RMSE by 0.45 dB (14.290 → 13.835), and shrinks the residual
scatter σ from 15.0 to 14.3 dB. Every one of the 5 CV folds improves. The new
coefficients are ~25σ from zero and in the physically expected direction.

**Applied 2026-08-06**: `config/model.yaml` now sets
`indicators: [is_greenland, is_floating]`, and `outputs/model/` + `docs/` have
been regenerated from it. The numbers quoted below as "+`is_floating`" are the
current model; the "baseline" column is the superseded `is_greenland`-only fit.

Figure: `outputs/model/analysis/floating_mask_float.png`

Script: `scripts/floating_mask_experiment.py` (`diagnose` is the reusable part —
floating-vs-grounded residuals of the current fit, stratified by thickness).

---

## 1. What `is_floating` means

`is_floating` = BedMachine `mask == 3`, and nothing else. Mask 3 is the only
floating value and means the same thing in both the Antarctic and Greenland
products. Every other mask value in the grid counts as grounded.

Grid cells (616,536 total, grid built with `mask_values: [2, 3, 4]`):

| ice sheet | mask 2 | mask 3 floating | mask 4 |
|---|---|---|---|
| antarctic | 480,042 | 60,630 | 605 |
| greenland | 75,067 | 166 | 0 |

## 2. Cheap diagnostic first: is there residual signal after thickness?

Residuals of the **existing** fit (`outputs/model/atten_refl`), uncensored points
only (shelf obs are essentially never saturated: 0.0% censored on floating vs
6–9% on grounded, so this is not a censoring artifact):

| sheet | basal | n | mean resid | median | RMSE | median thickness | median obs |
|---|---|---|---|---|---|---|---|
| antarctic | floating | 2611 | **+7.94** | +9.91 | 17.66 | 481 m | 25.3 dB |
| antarctic | grounded | 9945 | −0.42 | −0.72 | 13.75 | 1573 m | 67.4 dB |
| antarctic | vostok | 7 | +14.82 | +14.43 | 15.27 | 4114 m | 69.3 dB |
| greenland | floating | 23 | +33.58 | +34.26 | 35.62 | 327 m | 7.6 dB |
| greenland | grounded | 6714 | +1.93 | +2.54 | 13.01 | 1396 m | 71.4 dB |

Held-out test cells only: Antarctic floating +14.78 dB (n=70) vs grounded
+1.51 dB (n=738). So the bias is not an in-sample artifact.

Stratifying by thickness kills the "shelves are just thin" confound — the shelf
offset *survives and grows* within thickness bands:

| thickness band | n grounded | resid grounded | n floating | resid floating | Δ |
|---|---|---|---|---|---|
| 100–400 m | 562 | −5.55 | 1032 | +4.22 | **+9.8** |
| 400–700 m | 1072 | −5.51 | 798 | +5.80 | **+11.3** |
| 700–1000 m | 1325 | −3.70 | 473 | +13.21 | **+16.9** |
| 1000–1500 m | 1801 | −2.14 | 290 | +18.37 | **+20.5** |
| 1500–2000 m | 1259 | +1.97 | 17 | +8.56 | +6.6 |

At *matched* thickness the model over-predicts required SNR on shelves by 10–20 dB
— it demands far more SNR than the radar actually needed. That is exactly the sign
a near-specular ocean interface (~0 dB) versus a −3 to −20 dB rock/till bed
predicts. This alone answers the question; the retrain just confirms and
quantifies it.

**Not a campaign/calibration confound.** The floating-minus-grounded residual
contrast is positive in *all ten* Antarctic collections that contain both, from
+1.8 to +13.6 dB (median +11.2), spanning CReSIS (DC8, P3, Basler, GV; 8
seasons) and UTIG (2022/2023 BaslerMKB; +11.7 and +11.2 dB). It is not one bad
season masquerading as a physical effect.

## 3. Evidence count and collinearity with `is_greenland`

Training observations (19,125 usable, min_thickness 100 m):

| | grounded | floating |
|---|---|---|---|
| antarctic | 9841 | **2542** | 8 |
| greenland | 6711 | 23 | 0 |

Held-out test: 70 Antarctic floating of 1523. So 13.4% of training obs are on
shelves — plenty of evidence, and essentially all of it Antarctic.

`corr(is_greenland, is_floating) = −0.283` over the training set. Mild, and both
indicators stay sharply identified (posterior sd 0.5 dB/km and 0.48 dB for the
Greenland pair, 1.0 and 1.1 for the floating pair). The honest interpretation is
that `is_floating` is an *Antarctic* shelf-vs-grounded contrast; Greenland
contributes 23 points and cannot inform it. The Greenland coefficients do shift
when the shelf term is added (β_a −18.9 → −15.4 dB/km, −β_r −3.12 → −4.78 dB)
because Antarctic shelves were previously biasing the Antarctic reference — that
shift is the correlation doing its job, not a pathology.

## 4. Retrain: same folds, same test cells, same seed

`scripts/floating_mask_experiment.py train float` re-runs `atten_refl` against the
identical `outputs/model/split.parquet` (symlinked into `outputs/exp_floating/`),
so folds, test cells, censoring, non-detections and seeds are unchanged.

| run | CV RMSE | CV fold range | CV MAE | CV 1σ cov | CV logscore | ND logscore | test RMSE | test 1σ cov | div | r̂ max |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline `is_greenland` | 14.958 | 14.41–15.75 | 11.964 | 0.677 | −4.171 | **−2.410** | 14.290 | 0.710 | 0 | 1.006 |
| `+is_floating` | **14.108** | 12.88–14.93 | 11.338 | 0.680 | −4.125 | −2.549 | **13.835** | 0.702 | 0 | 1.009 |

Per-fold CV RMSE, baseline → `+is_floating`: 15.22→14.39, 14.41→12.88,
15.75→14.93, 14.51→13.94, 14.86→14.26. **5 of 5 folds improve**, by 0.6–1.5 dB.
Sampling stays clean (0 divergences, r̂ ≤ 1.01).

### Posterior coefficients in physical units

Conversion per `scripts/posterior_physical.py`: attenuation side
× σ_target/σ_thickness × 1000 → dB/km two-way; reflectivity side × (−σ_target)
→ dB contribution to required SNR.

| parameter | unit | baseline | `+is_floating` |
|---|---|---|---|
| α_a attenuation rate | dB/km (2-way) | +32.9 ± 0.3 | +28.9 ± 0.3 |
| β_a[T_air] | dB/km/K | +0.669 ± 0.015 | +0.632 ± 0.015 |
| β_a[speed] | dB/km per m/yr | −0.0044 ± 0.0009 | −0.0083 ± 0.0009 |
| β_a[GHF] | dB/km per mW/m² | −0.169 ± 0.014 | −0.166 ± 0.014 |
| β_a[greenland] | dB/km | −18.9 ± 0.52 | −15.4 ± 0.50 |
| **β_a[floating]** | dB/km | — | **−9.84 ± 1.00** |
| −α_r reflectivity term | dB | +6.99 ± 0.25 | +8.97 ± 0.25 |
| −β_r[greenland] | dB | −3.12 ± 0.52 | −4.78 ± 0.48 |
| **−β_r[floating]** | dB | — | **−25.6 ± 1.1** |
| σ residual scatter | dB | 15.00 ± 0.08 | **14.30 ± 0.07** |
| θ detection threshold | dB | −1.56 ± 0.34 | −1.36 ± 0.33 |
| τ picker softness | dB | +3.01 ± 0.20 | +3.00 ± 0.19 |

Both floating coefficients have P(>0) = 0.000 over 4000 draws — ~10σ and ~23σ from
zero. Because thickness is z-scored (mean 1607 m, sd 991 m), the two terms
combine into a single thickness-dependent shelf discount:

    Δ RSSNR(floating, T) = −25.6 dB + (−9.84 dB/km) · (T − 1607 m)

| shelf thickness | indicator-only Δ RSSNR |
|---|---|
| 200 m | −11.8 ± 0.6 dB |
| 400 m | −13.7 ± 0.5 dB |
| 600 m | −15.7 ± 0.4 dB |
| 1000 m | −19.6 ± 0.5 dB |
| 1500 m | −24.5 ± 1.0 dB |

Direction and magnitude are physically right: floating ice needs 12–20 dB less
surface SNR than grounded ice of the same thickness, temperature, speed and GHF —
consistent with an ice/saline-water interface at ~0 dB against a −3 to −20 dB
grounded bed. The negative attenuation-side term says the shelf advantage grows
with shelf thickness, which is what the stratified residual table demanded.

### Residual bias, before and after (Antarctica, uncensored)

| basal | band | n | baseline | `+is_floating` |
|---|---|---|---|---|
| floating | 100–400 m | 1032 | +4.22 | **+0.25** |
| floating | 400–700 m | 798 | +5.80 | **−1.56** |
| floating | 700–1000 m | 473 | +13.21 | **+0.98** |
| floating | 1000–1500 m | 290 | +18.37 | **+2.06** |
| floating | 1500–2000 m | 17 | +8.56 | −15.29 |
| grounded | 100–400 m | 562 | −5.55 | **+0.25** |
| grounded | 400–700 m | 1072 | −5.51 | **−0.28** |
| grounded | 700–1000 m | 1325 | −3.70 | +0.83 |
| grounded | ≥1000 m | 6986 | −2.1 … +2.9 | +1.0 … +3.4 |

Two things worth noticing:

1. The shelf bias collapses from +4…+18 dB to ~±2 dB across the bands that hold
   99% of the shelf observations.
2. **Thin grounded ice is fixed as collateral.** The baseline under-predicted
   100–700 m grounded ice by 5.5 dB, because shelves were dragging the thin end
   of the thickness relation down. Removing them fixes it. Greenland grounded
   residuals are unchanged (≤0.6 dB per band) — no damage done to the sheet that
   contributes no shelf information.

### Effect on the deliverable maps

Antarctic floating cells are 58,169 of 527,688 predicted Antarctic cells (11.0%;
), plus 116 Greenland floating cells.

| region | n cells | median Δ (new − baseline) | p10 / p90 | median RSSNR base → new |
|---|---|---|---|---|
| antarctic floating | 58,169 | **−5.8 dB** | −13.7 / −0.7 | 29.0 → 24.0 dB |
| antarctic grounded | 468,914 | +1.1 dB | −1.1 / +4.6 | 75.1 → 76.2 dB |
| greenland floating | 116 | −7.2 dB | −11.0 / −1.5 | 41.9 → 34.9 dB |
| greenland grounded | 67,953 | −0.1 dB | −0.8 / +0.5 | 78.8 → 78.7 dB |

Per thickness band the floating-minus-grounded map contrast is −10.5, −10.3,
−13.7, −18.1 and −22.3 dB (100–300, 300–500, 500–800, 800–1200, 1200–2000 m).
The net map change on shelves (−5.8 dB median) is smaller than the raw indicator
effect (−13.7 dB at 400 m) because the rest of the model re-fits at the same time
— notably thin *grounded* ice rises +5 to +6 dB. Both movements are corrections
of documented biases, and both are large enough to matter for the mission-design
use of the maps. This is the strongest argument for adopting the change even
though CV RMSE only moves 0.85 dB: the accuracy gain is modest, the map change on
an eighth of Antarctica is not.

## 5. Alternatives considered and rejected

**Do nothing — thickness already carries it.** Rejected outright by §2: the shelf
offset is 10–20 dB *within* thickness bands and grows with thickness, i.e. it is
orthogonal to what thickness explains. The ~45 dB shelf-vs-grounded gap in
`schroeder2021_benchmark.csv` (29 dB shelf vs 66–76 dB grounded) is indeed mostly
thickness, but roughly 12–20 dB of it is not.

**Reflectivity-only indicator (append to the `refl` term, not both).** The
mechanism argues for reflectivity-only: the ocean interface is a boundary
property, not a bulk property of the ice column. But a reflectivity-only term is
a *constant* dB offset, and the stratified residuals demand a slope — the needed
correction grows monotonically from +9.8 dB at 100–400 m to +20.5 dB at
1000–1500 m. The fitted β_a[floating] = −9.84 ± 1.00 dB/km supplies exactly that
and is 10σ from zero, so a reflectivity-only version would leave the thickness
trend on the table. The physical reading of the attenuation-side term is not
"shelf ice attenuates 9.8 dB/km less" in isolation — it is the interaction the
model needs because thick shelf ice (near grounding lines, ice tongues) is a
different beast from a 300 m shelf front. Keeping the default "indicator enters
both terms" behaviour costs one parameter and is the better fit. Worth revisiting
if the shelf thickness range ever gets better sampled above 1500 m (n=17 today).


**Per-sheet floating indicators (`is_floating` × `is_greenland`).** Not tried:
Greenland has 23 floating training observations. There is no evidence to fit it
with, and Greenland's floating residual stays +30 dB after the change (see the
case against, below).

## 6. The case against

Three real caveats, none of which changes the recommendation:

1. **The non-detection log score gets worse**: −2.410 → −2.549 (worse in 4 of 5
   folds). Lowering predicted RSSNR on shelves makes "the bed was not detected
   here" less probable, so the detection side of the likelihood pays. The size is
   small and the trade is one-sided in total log-likelihood terms: the point
   score gains 0.046 nats over 19,125 observations (≈ +880) while the ND score
   loses 0.139 nats over 226 non-detections (≈ −31). Note also that only **4 of
   the 226** non-detections entering the likelihood are on floating ice (the
   delta filter removes most shelf non-detections as clutter/mislocated bed), so
   this is a second-order consequence of the tighter σ and shifted θ rather than
   the shelf term mispredicting non-detections directly. Still, it is the one
   metric that moves the wrong way and should be stated when the change lands.
2. **1σ coverage does not improve** (0.677 → 0.680 CV; 0.710 → 0.702 test). The
   model is still under-dispersed; a single global σ cannot fix that. Panel (c)
   of the figure shows it plainly: the shelf prediction *median* moves onto the
   observed median (29.1 → 24.0 dB against 25.4 dB observed) but the predicted
   spread stays far too narrow against the observed spread.
3. **Greenland's shelves stay broken.** 23 observations, residual +33.6 dB
   baseline → +27.7 dB after. The Antarctic-calibrated shelf discount barely
   dents it. Whatever is wrong with the Greenland floating-tongue observations
   (they sit at a median required SNR of 7.6 dB, extraordinarily low) is a
   separate problem, and `is_floating` is not the fix. It is 23 of 19,125 points,
   so it does not move any aggregate number either way.

An honest reading is that most of the CV RMSE gain comes from correctly
partitioning thin ice into "shelf" and "thin grounded" — two populations the
baseline was forced to average — rather than from any new physics. But that
partition is real, is bed-type driven, is available for free from a dataset
already joined onto every grid point, and the resulting coefficients land where
radar glaciology says they should.

## 7. What was changed in the repo

- `src/radar_postproc/train.py`: added `is_floating` to
  `INDICATOR_BUILDERS`. **Inert** — nothing selects them unless
  `train.indicators` is changed.
- `src/radar_postproc/config.py`: comment listing the supported indicators.
- `scripts/floating_mask_experiment.py`: new (diagnose / train / compare).
- `tests/unit/test_indicators.py`: new — covers the mask-4 sheet guard.
- `outputs/model/analysis/floating_mask_float.png`: new figure.
- Experiment artifacts live in `outputs/exp_floating/` and
  touched and still carries the canonical run (`atten_refl` CV 14.958 dB, test
  14.290 dB, `indicators: ["is_greenland"]`, run_id `baeae7225bb9`).

To adopt: set `train.indicators: [is_greenland, is_floating]` in
`config/model.yaml`, re-run `uv run snakemake --cores 4 model_all`, and refresh
`docs/3_model.md` plus `docs/figures/`. Note that `scripts/posterior_physical.py`
has a hard-coded `SHORT` label map that will need an `is_floating` entry, and
that the train-stage `run_id` hashes the indicator *name*, not the builder body —
renaming a builder's semantics without renaming the key would silently reuse a
run_id.
