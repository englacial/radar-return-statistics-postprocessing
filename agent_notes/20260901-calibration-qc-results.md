# Calibration QC filter — results (2026-09-01)

Branch `qc-calibration-filter`. Plan: `agent_plans/20260901-calibration-qc-filter.md`.
All numbers reproducible with `uv run python scripts/qc_filter_comparison.py`
(needs `outputs/baseline_20260807/`, a copy of the pre-QC `outputs/model` +
augment parquets) and `scripts/season_crossover_matrix.py [--calibration-qc]
--out-dir outputs/qc_filter/crossover`. Full tables: `outputs/qc_filter/comparison.md`.

## Data

Re-pinned 2026-09-02 00:36 UTC snapshots (antarctica SYAKG11X8AFFY0H9H65G,
greenland JWDABR34HPM816P70FD0, ase WQCXS05H226PX1KGRZ2G). Upstream verified
no science values changed, so baseline vs new differs only by the filter.

Share of traces entering the split stage (after the two season exclusions)
rejected, exclusive attribution:

| rule | Antarctica | Greenland |
|---|---|---|
| seam \|Δ\| ≥ 3 dB | 9.1% | 8.9% |
| surface from img2+ | 3.1% | 3.0% |
| margin < 2 dB | 2.2% | 0.7% |
| any | 14.4% | 12.7% |

Worst seasons: 2016_Greenland_P3 51% (49% seam), 2017_Antarctica_Basler 46%
(already excluded), 2013_Antarctica_Basler 33%, 2017_Antarctica_P3 31%,
2019_Antarctica_GV 24% (22% at its −30 dB ceiling), 2016_Antarctica_DC8 22%.
Unmeasured seams (check could not run): 92% of 2014_Greenland_P3, 61% of
2012_Antarctica_DC8 → `drop_unmeasured_seam: true` would reject 35% / 50% of
all traces, i.e. whole seasons; kept false. Threshold sensitivity: seam 2 dB →
27% / 18% rejected, 5 dB → 12% / 7%; margin 1–3 dB moves totals by ±2 pts;
dropping the img2 rule saves 2.6 / 2.5 pts. img2-sourced traces have median
RSSNR 8–17 dB below img1 traces of the same DC8 season (2012: 46.7 vs 38.4,
2014: 59.5 vs 42.1, 2016: 46.7 vs 35.9, 2018: 65.3 vs 50.2 dB) — the bias the
changelog warns of is visible in the target, so the rule stays.

Training grid points 19,272 → 17,447 (−9.5%); 714 kept points now match a
different, QC-passing trace. Median training target rises 61.9 → 64.6 dB
(Antarctica) and 73.6 → 74.0 dB (Greenland): the filter preferentially
removes low-RSSNR traces (saturated surface ⇒ RSSNR biased low).

## Model fit

| | atten_refl baseline | atten_refl QC | linear baseline | linear QC |
|---|---|---|---|---|
| CV RMSE [dB] (fold range) | 13.02 (11.9–14.4) | 12.87 (11.8–14.3) | 13.92 | 13.80 |
| CV coverage 1σ | 0.680 | 0.679 | 0.704 | 0.702 |
| CV log score [dB] | −4.019 | −4.009 | −4.103 | −4.096 |
| test RMSE (own test set) | 12.67 | 12.72 | 13.95 | 13.98 |
| σ residual [dB] | 12.91 | 12.71 | 14.33 | 14.15 |
| θ / τ [dB] | −1.22 / 3.01 | −1.08 / 2.79 | | |
| divergences / R̂max | 0 / 1.0025 | 0 / 1.0049 | | |

Same points, both posteriors (the fair comparison — the CV populations differ):
on the QC test set (1,330 uncensored) atten_refl baseline 12.75 dB vs QC 12.72
dB; on the baseline test set (1,433) 12.67 vs 12.64 dB. Bias +1.9 → +2.0 dB.
Differences of 0.03–0.05 dB are noise.

Posteriors (`posterior_comparison.png`): every parameter within ~2 sd of its
baseline. Largest moves: β_a[greenland] −2.1 → −3.6 dB/km, −β_r[greenland]
−16.0 → −17.2 dB, −α_r +6.05 → +5.66 dB, −β_r[T_air] +0.25 → +0.28 dB/K,
σ 12.9 → 12.7 dB, τ 3.0 → 2.8 dB. The sheet-level attenuation and
reflectivity offsets move against each other, so predictions barely change.

Predictions (`prediction_difference.png`): new − baseline posterior mean over
the full grid, median +0.01 dB (Antarctica) / +0.07 dB (Greenland), 5th–95th
percentile −0.2…+0.6 / −0.3…+0.6 dB; predictive std ratio 0.985. Largest
positive shifts on the Siple Coast / Ross ice streams and NW Greenland
(≈ +0.6–1 dB); slightly negative in interior SW Greenland.

## Model-free check: season crossovers

`outputs/qc_filter/crossover/crossover_summary.md` (off-diagonal cells present
both before and after):

| | mean \|median Δ\| pre → QC | mean sd pre → QC | diagonal sd pre → QC |
|---|---|---|---|
| Antarctica (18 cells) | 11.65 → 11.90 dB | 9.84 → 9.69 dB | 8.61 → 7.71 dB |
| Greenland (15 cells) | 7.13 → 6.53 dB | 9.03 → 7.55 dB | 7.06 → 6.92 dB |

- **2016_Greenland_P3** (49% seam rejects) is the clear success: vs
  2014/2017/2018/2019 it goes from −4.3/+2.1/+2.5/−1.2 dB with sd 11–13 dB to
  −0.2/−1.2/−0.6/−3.3 dB with sd 7.7–8.8 dB — i.e. it now looks like every
  other P3 season, and its pairs with 2013 drop from −18.8 to −14.9 dB.
- Within-season repeatability tightens where seams were flagged:
  2013_Antarctica_Basler 10.4 → 8.4 dB, 2017_Antarctica_P3 8.2 → 5.6 dB,
  2014_Antarctica_DC8 9.4 → 8.4 dB, 2017_Antarctica_Basler 17.9 → 13.7 dB.
- **Season-level offsets do not move**: 2012_Antarctica_DC8 stays ~12 dB below
  the 2014–2018 DC8/P3 seasons; 2017_Antarctica_Basler stays ~30 dB below its
  neighbours (and 9–13 dB below the 2022/23 BaslerMKB); 2013_Antarctica_P3 vs
  2019_GV stays +15 dB; 2013_Greenland_P3 stays 16–19 dB low. The QC catches
  2% of 2013_Greenland_P3 and 46% of 2017_Antarctica_Basler, so
  `exclude_collections` must stay as it is.

Out-of-fold residuals by season (`scripts/residual_audit.py`, now in
`docs/figures/residuals_by_season.png`): per-season medians move by <= 1.4 dB
(2012 DC8 +7.7 -> +7.9, 2013 P3 -5.3 -> -6.7, 2019 GV +11.2 -> +12.2,
2017 P3 -9.2 -> -9.6); the same seasons stay offset.

## Assessment

The suggested filter is sound hygiene and cheap (≈13% of traces, no
retuning), and it demonstrably repairs the one season whose problem *is*
trace-level (2016 Greenland seams). But the required-SNR model was already
insensitive to it: at the 5 km grid / 1 km nearest-trace matching scale the
rejected traces were a minority whose biases partly average out, and the
dominant residual scatter (σ ≈ 12.7 dB) and the season-level calibration
offsets are untouched. Adopt it as the default, keep the two exclusions, and
treat season-level (crossover-derived) calibration as the next lever — not
QC threshold tuning, which the sensitivity table shows only trades data
volume for no measurable fit change.

Open follow-ups: 2012_Antarctica_DC8's img1 ceiling is now fit_ok at −29.7 dB
(4% of traces at the ceiling) but its ~12 dB offset to later DC8 seasons is
a whole-season effect; 2019_Antarctica_GV loses 22% to its −30 dB ceiling
(pileup 0.11, span 0.35 — a credible fit, worth a look upstream);
2023_Antarctica_BaslerMKB's ceiling (pileup 0.01) is a weak fit that removes
0.7% — harmless but not evidence of saturation.
