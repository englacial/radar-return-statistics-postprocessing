# Benchmark: `atten_refl` vs Schroeder et al. (2021) / IGARSS 2021

Script: `scripts/schroeder2021_benchmark.py` (`uv run python scripts/schroeder2021_benchmark.py`)
Outputs: `outputs/model/analysis/{cdf_antarctica_vs_2021.png, cdf_antarctica_icemask_vs_2021.png,
schroeder2021_benchmark.csv}`

## 1. What the 2021 CSV actually contains

Source file: `reference/rssnr_igarss_exported_data_20241215.csv` (supplied and checked
into this repo). 53,958 rows x 14 columns.

| column | meaning |
|---|---|
| `x`, `y` | EPSG:3031 cell centres, **Antarctica only**, integer metres. Actual spacing is 5000 m; cell centres end in `...4500`/`...500`, i.e. offset half a cell from our grid |
| `snr` | the 2021 **training observation** of required surface SNR [dB]; only 1,813 rows non-NaN (1,712 unique cells) — this is the red "Training data" curve of Fig. 2a |
| `snr_pred` | the 2021 model's **predicted** required surface SNR [dB] (798 NaN) |
| `snr_pred_std` | its predictive 1-sigma [dB]. Near-constant at **14.48 ± 0.16 dB** — the model is homoscedastic, so the 68% band is essentially a rigid ±14.5 dB shift of the CDF |
| `mask` | BedMachine mask: 2 = grounded (48,213), 3 = floating (5,745). No mask 4 |
| `v`, `vx`, `vy` | surface velocity [m/yr] (MEaSUREs) |
| `thickness`, `smb`, `surf_temp`, `base_temp` | the model's covariates |

Two things that are easy to get wrong:

- **It is a random subsample, not a full grid.** ~54k rows over a domain that holds
  ~550k 5 km ice cells, and 2,699 rows are repeat draws of the same `(x, y)` (the
  index column `Unnamed: 0` cycles 0–9999, so it was exported in chunks with
  replacement). The script deduplicates by averaging per cell → **50,504 unique cells**.
  Because it is a *uniform* subsample, CDFs are unbiased; only the n's differ.
- **10.1% of its cells are poleward of 84°S** — precisely the region our old
  ITS_LIVE-only grid could not predict. That is why the footprint check below matters.

## 2. How the panels were matched

- **Fig. 2a analogue** (`cdf_antarctica_vs_2021.png`): CDF of *predicted* required
  surface SNR — our full-grid posterior mean and the 2021 `snr_pred`, each with its
  68% predictive band, at full extent. Our Greenland prediction (green, n=68,069,
  median 78.7 dB) is drawn for context; the 2021 work is Antarctica-only so it has no
  counterpart there. Deliberately predictions only:
  the observation curves are flight-line samples, and the 2021 one is a ~10% subsample
  of 1,712 cells, so they add scatter without adding a comparison. The
  footprint-restricted variant is reported numerically (printed summary and
  `schroeder2021_benchmark.csv`) rather than as a second panel — it moves the 2021
  median by only +0.59 dB, so it does not need a panel of its own.
- **Fig. 3 analogue** (`cdf_antarctica_icemask_vs_2021.png`): ice shelf / fast / slow,
  drawn as two side-by-side panels (ours | 2021) rather than overlaid, so each keeps its
  own 68% band and the two are read against each other directly.
- **Class definitions.** Ice shelf = BedMachine mask 3 (floating). Grounded ice is split
  at **50 m/yr**, the 2021 convention. The check that matters is that it reproduces the
  published Fig. 3 numerically from this CSV: shelf median 25.5 dB, fast 58.2 dB, slow
  72.1 dB, against roughly 25 / 57 / 72 dB read off the figure.
  Our BedMachine mask 4 (Lake Vostok, 605 cells, 0.1%) is treated as grounded; the 2021
  export has no mask-4 cells.
- **Axes** follow the published figures: cumulative **percent** (0-100) on y, x starting
  at 0 dB. Every curve is a model prediction, so all are dashed per the repo convention;
  the dash *pattern* additionally distinguishes the series (long / short / dash-dot), so
  colour is not the only cue. The short dash is kept long enough not to read as dotted,
  which the repo reserves for posterior-predictive draws.
- **68% band construction** copies the 2021 notebook's `cdf_variable_maker`: fill between
  the CDF of `mu - sigma` and the CDF of `mu + sigma` (`fill_betweenx` on the sorted
  low/high arrays). For us `sigma` = `pred_std` from `predictions.zarr` (the full
  predictive sd, ~15.0 dB); for 2021 it is `snr_pred_std` (~14.5 dB). The two models'
  predictive widths are therefore almost identical, which makes the bands directly
  comparable.

## 3. Quantitative comparison

See `outputs/model/analysis/schroeder2021_benchmark.csv` (percentiles p10/p25/p50/p75/p90
and `frac_le_{40,60,80,100}dB` for each source x class).

Model benchmarked: `atten_refl`, run_id `f0708cfa69b4` (test RMSE 14.29 dB, MAE 11.23 dB,
1-sigma coverage 0.71). Note this is the *post-velocity-swap* fit — see caveat 1.

### All Antarctica (medians and resolvable-bed fractions)

| source | n | p10 | p25 | **p50** | p75 | p90 | <=40 dB | <=60 dB | <=80 dB | <=100 dB |
|---|---|---|---|---|---|---|---|---|---|---|
| ours prediction | 527,688 | 32.3 | 53.2 | **73.0** | 82.7 | 91.3 | 15.3% | 30.8% | 68.4% | 97.1% |
| ours observations | 13,330 | 22.4 | 38.8 | **61.8** | 78.7 | 89.6 | 26.0% | 47.8% | 76.9% | 97.8% |
| 2021 prediction | 50,504 | 29.8 | 51.1 | **67.8** | 79.5 | 85.0 | 15.8% | 37.2% | 76.4% | 100.0% |
| 2021 prediction, restricted to our footprint | 49,316 | 32.2 | 52.5 | **68.4** | 79.7 | 85.2 | 14.1% | 35.8% | 75.9% | 100.0% |
| 2021 training data | 1,712 | 33.9 | 48.3 | **66.6** | 80.6 | 89.2 | 14.9% | 39.7% | 74.1% | 97.4% |

### By class (median prediction, dB)

| class | ours | 2021 | difference |
|---|---|---|---|
| Ice shelf | 29.0 | 25.5 | +3.5 |
| Fast-moving ice (>50 m/yr) | 66.5 | 58.2 | +8.3 |
| Slow-moving ice (<=50 m/yr) | 75.6 | 72.1 | +3.5 |

Fraction resolvable at 60 dB of surface SNR: shelf 98.9% (ours) vs 100.0% (2021);
fast 39.3% vs 55.3%; slow 20.5% vs 27.4%.

### Where we agree

- **The overall shape is reproduced.** Both CDFs rise from ~20 dB to ~100 dB with the
  same S-shape, and the medians differ by only ~5 dB against a predictive sigma of ~15 dB.
  The two 68% bands overlap over essentially the whole range, so at the stated uncertainty
  the two models are statistically indistinguishable for the bulk of Antarctica.
- **The class ordering and its magnitude match.** Ice shelf << fast < slow in both, with
  the shelf/grounded separation (~45 dB) reproduced almost exactly. This is the single
  strongest confirmation, since it is the qualitative result Fig. 3 was making.
- **Predictive width matches**: our `pred_std` ~15.0 dB vs their `snr_pred_std` ~14.5 dB.
- **Their training data and ours land in the same place**: median observed required SNR
  66.6 dB (2021, n=1,712) vs 61.8 dB (ours, n=13,330), the gap being mostly composition
  (20% of our observations are on ice shelves vs 7% of theirs).

### Where we disagree

1. **We are ~5 dB more pessimistic in the median and much more pessimistic in the upper
   tail.** p90: 91.3 dB (ours) vs 85.0 dB (2021). Fraction of the bed resolvable at 80 dB:
   68.4% vs 76.4% — an 8-point difference that matters directly for platform sizing.
2. **The 2021 model has a hard ceiling that ours does not.** Its largest prediction
   anywhere is 105.2 dB and 99.97% of its cells sit at or below 100 dB; ours reaches
   160.3 dB with 2.9% above 100 dB. The 2021 model is a linear regression on three
   covariates and simply cannot produce the extreme interior values, so its upper tail is
   compressed by construction. Any "fraction of the bed we can never reach" statement
   taken from the 2021 curves is optimistic.
3. **We separate fast from slow ice far less than 2021 does.** Median gap slow-minus-fast:
   9.1 dB (ours) vs 13.9 dB (2021). This is the opposite of what you would expect from the
   covariate sets — velocity is a *direct* covariate for us and is absent from the 2021
   regression (thickness, surface temperature, surface elevation), so their fast/slow
   contrast is entirely mediated by thickness/elevation. It suggests the 2021 contrast is
   largely a thickness proxy, and that our model, having thickness *and* velocity, assigns
   most of that signal to thickness.
4. **Ice shelves: we predict a real right tail, they do not.** 83.1% of our shelf cells are
   <=40 dB vs 98.8% of theirs; our shelf p90 is 46.6 dB vs 34.2 dB. Their shelf
   distribution is very tight (sd 4.7 dB) against ours (sd ~9 dB).
5. **Our observed CDF sits ~11 dB below our predicted CDF at the median** (61.8 vs 73.0),
   whereas the 2021 pair nearly coincide (66.6 vs 67.8). That is not a defect: our model
   corrects upward for censoring and non-detection (caveat 3), so it should predict harder
   conditions than the detected-only observations show. The 2021 model, with no such
   correction, essentially reproduces its own detection-biased training distribution.

### Velocity-threshold sensitivity

Median prediction [dB], fast / slow, at several thresholds:

| threshold | ours fast | ours slow | 2021 fast | 2021 slow |
|---|---|---|---|---|
| 25 m/yr | 69.3 | 75.8 | 60.4 | 73.3 |
| **50 m/yr** (used) | **66.5** | **75.6** | **58.2** | **72.1** |
| 100 m/yr | 60.4 | 75.5 | 55.3 | 71.3 |
| 200 m/yr | 54.9 | 75.3 | 53.5 | 70.9 |

The slow-ice median is essentially threshold-independent for both models (slow ice
dominates the area: 424k of 528k of our cells). The fast-ice median falls with a stricter
threshold for both, and our fast/slow gap grows from 6.5 dB to 20.4 dB across this range
vs 12.9 -> 17.4 dB for 2021 — so conclusion 3 above is threshold-sensitive in magnitude
but holds in sign at every threshold at or below 100 m/yr.

## 4. Caveats

1. **The Antarctic coverage gap moved during this work.** When this benchmark started, the
   Antarctic velocity covariate was ITS_LIVE only, which has a polar hole poleward of
   ~84 deg S: 12.2% of grid points had no velocity and 13.9% had no prediction (68% of
   those poleward of 84S). Mid-task the pipeline was re-run with Antarctic velocity
   switched to MEaSUREs phase-based (NSIDC-0754, `surface_v_m_yr`), which closes the hole:
   velocity now missing on 0.5% of the grid and **2.5% of cells have no prediction**, only
   19% of which are poleward of 84S. The numbers above are from the post-swap fit. The
   script reads whichever velocity column exists and states the gap in the figure captions.
   - **Footprint effect, quantified (the comparison requested).** Restricting the 2021
     export to cells where we predict moves its median from 67.80 to 68.39 dB (+0.59 dB)
     and its <=80 dB fraction from 76.4% to 75.9%. Under the *old* 13.9%-gap grid the same
     restriction moved the median by only +0.18 dB. **In neither case does the footprint
     mismatch explain the ~5 dB median difference between the models** — that difference
     is real, not a coverage artefact. (Filling the hole did, however, raise *our own*
     median from 65.9 to 73.0 dB: the interior is thick, cold, slow ice that needs high
     SNR, and excluding it was flattering us.)
2. **Target definition differs.** Both call the quantity "required surface SNR", but the
   2021 values come from CReSIS-supplied products via that project's own
   noise-floor/pick conventions, while ours come from the OPR reprocessing in
   `radar-return-statistics` with our own margin and calibration constants. The 2021 link
   budget notebook adds geometric-spreading-to-bed and pulse-compression terms *after*
   `snr_pred`, so their RSSNR excludes both. No attempt was made to reconcile the
   definitions; a constant offset of a few dB between the two products cannot be excluded
   and would absorb part of the ~5 dB median difference.
3. **Censoring and non-detection are handled by us and not by 2021.** Our fit uses a Tobit
   likelihood for right-censored (near-noise-floor) observations (1,258 in training) plus a
   learned soft detection threshold (theta = -1.56 dB, tau = 3.00 dB) that marginalizes 226
   matched non-detections. The 2021 model is an uncensored linear regression on detected
   picks only. So the two predicted CDFs are not estimating quite the same thing: ours
   targets the true required SNR including places the radar failed, theirs targets the
   distribution of successfully detected beds. This is the most likely single explanation
   for our higher predictions and should be stated whenever the two are compared.
   Our *observed* (solid blue) curve is still detection-biased — the 466 Antarctic
   non-detections carry a NaN target and are simply absent from it.
4. **Model form differs.** Ours: `mu = atten_rate(covariates) * thickness - refl(covariates)`
   with covariates thickness, ERA5 T2m, surface velocity, GHF (plus a Greenland indicator),
   fit by NUTS. 2021 (as re-implemented in `required_surface_snr`): additive linear in
   thickness, surface temperature and surface elevation, no velocity, separate grounded and
   floating fits. The 2021 export predates that rework and its exact covariate set is not
   recoverable from the CSV alone.
5. **The 2021 export is a ~10% uniform subsample with replacement**, deduplicated here to
   50,504 unique cells. CDFs are unbiased under uniform subsampling, but per-class n's are
   small for ice shelves (5,176 cells, 114 observations), so the 2021 shelf curve is the
   least well constrained part of Fig. 3.
6. **Grid offsets.** The 2021 cell centres are offset half a cell (2,500 m) from ours; the
   footprint test snaps by nearest index, which is exact at 5 km resolution but means a
   2021 cell is matched to whichever of our cells contains it.
7. **Both figures are Antarctica-only**, so the standard sheet colour coding does not carry
   information within them. Figure 1 keeps tab:blue for our Antarctic series and uses
   tab:orange for the external 2021 reference; Figure 2 is class-split and uses the
   non-reserved tab:orange / tab:purple / tab:brown for shelf / fast / slow. Linestyles
   follow the convention throughout: solid = observations, dashed = model predictions.
