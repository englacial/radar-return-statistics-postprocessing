# Antarctic surface-speed product swap: ITS_LIVE v2 → MEaSUREs phase-based (NSIDC-0754)

Date: 2026-08-05. Old-vs-new comparison: `uv run python scripts/velocity_swap_comparison.py`.

The coverage numbers below were produced by a one-off `scripts/velocity_coverage_check.py`,
since deleted along with the ~11 GB of NSIDC-0720/0725 files it needed. Its outputs are
preserved at `outputs/model/analysis/velocity_coverage_{by_lat.csv,maps.png}` but are no
longer regenerable without re-downloading those products.

## Why not the originally-proposed products

The ask was to replace ITS_LIVE with NSIDC-0720 (MEaSUREs Annual Antarctic Ice
Velocity Maps) + NSIDC-0725 (MEaSUREs Greenland Annual Ice Sheet Velocity Mosaics)
to close the polar-hole gap. **Neither does.** Valid-data fraction on the 5 km
Antarctic model grid:

| product | all | 89–90°S | 88–89 | 87–88 | 86–87 | 85–86 | 84–85 | 82.5–84 |
|---|---|---|---|---|---|---|---|---|
| ITS_LIVE v2 | 87.9% | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 11.3 | 54.8 |
| 0720 2000/2001 | 53.6% | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| 0720 2017/2018 | 80.9% | 0.0 | 0.0 | 0.0 | 0.2 | 4.7 | 16.0 | 44.7 |
| 0720 2019/2020 | 77.2% | 0.0 | 0.0 | 0.0 | 0.0 | 2.3 | 4.3 | 15.5 |
| 0720 2024/2025 | 32.6% | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| 0720 union of the 4 | 86.0% | 0.0 | 0.0 | 0.0 | 0.3 | 5.5 | 17.2 | 46.9 |
| **0754 phase-based** | **99.5%** | 95.8 | 97.0 | 99.2 | 98.2 | 97.8 | 99.1 | 99.5 |

0720's hole is larger than ITS_LIVE's and breathes year to year. NSIDC-0725 fills
**0 of the 2,044** ITS_LIVE-gap cells in Greenland — every one is genuine nodata
(`-1`) there too (checked 2017/18 and 2021/22); 0725 is also slightly narrower in
the far north (northern bound y = −639,100 m in EPSG:3413, 27 grid cells beyond it,
none of which ITS_LIVE covers either). So Greenland stays on ITS_LIVE.

Other reasons 0720/0725 are a poor fit even setting coverage aside: they are annual
snapshots rather than a climatology (so a bespoke multi-year composite would be
required); they come from different teams at different resolutions for the two
sheets (1 km UCI vs 200 m UW), which confounds any inter-product bias with the
model's `is_greenland` indicator; 0720 uses `_FillValue = 0.0`, masking genuine
zero-component velocities; and 0720's ERRX/ERRY are documented as "relative quality
rather than absolute error".

## What was changed

- New plugin `src/radar_postproc/datasets/measures_vel.py` (`measures_vel`,
  Antarctica-only, NSIDC-0754 v1, ~7 GB netCDF via earthaccess). Speed =
  `hypot(VX, VY)`; error propagates ERRX/ERRY through the magnitude.
- Both velocity plugins now emit the generic columns `surface_v_m_yr` /
  `surface_v_error_m_yr` (was `itslive_v_*`), so the model has one speed column
  across sheets; the manifest's per-dataset `source_info` records the product.
- `config/model.yaml`: Antarctic grid dataset → `measures_vel`; features updated.
- `config/{antarctica,ase,utig}.yaml`: Antarctic augment stores → `measures_vel`.
  All four stores re-augmented (antarctica 209,399 traces, greenland 168,386, ase
  23,066, utig 26,089); the whole grid → split → train → benchmark chain then re-run.
  Split counts and every model metric reproduced **exactly** (CV 14.958, test 14.290),
  only run_ids changed — as expected, since `split.py` takes the radar target and
  noise columns from the augment parquets and never velocity.
- `scripts/error_histograms.py`: `itslive_v_error()` → `surface_v_error()`, now showing
  NSIDC-0754-derived error for Antarctica alongside ITS_LIVE for Greenland
  (`outputs/error_histograms/surface_v_error.png`).
- `scripts/velocity_swap_comparison.py` added.
- `docs/data_sources.md` velocity section rewritten; `docs/2_input_data.md` covariate
  list updated; `docs/3_model.md` headline table and attenuation-rate caption updated.
  **Every figure under `docs/figures/` regenerated** from the migrated pipeline.

Baseline (ITS_LIVE) run snapshotted at `outputs/model_itslive_baseline/`.

## Effect

Antarctic grid cells with a usable speed: 475,195 → 538,311 (missing 12.21% → 0.55%).
Antarctic grid cells carrying a radar observation that survive the feature filter:
9,502 → 13,220 observed (+39%) and 343 → 407 non-detections. Pooled CV set
14,390 → 17,867 rows.

The 3,732 newly-usable Antarctic observations are **not** a single-season artifact —
they span 8 collections and both institutions (UTIG 2,298 / CReSIS 1,434, largest
single collection 2023_Antarctica_BaslerMKB at 1,312). They are much thicker
(mean 2,331 m vs 1,587 m over all Antarctic obs) and higher-RSSNR (71.3 vs 58.5 dB).
The model was previously extrapolating into the thick interior; now it interpolates.

Headline metrics (atten_refl):

| | ITS_LIVE | MEaSUREs |
|---|---|---|
| CV RMSE | 14.92 dB (14.13–16.77) | 14.96 dB (14.41–15.75) |
| CV 1σ coverage | 0.678 | 0.677 |
| test RMSE | 14.386 dB | 14.290 dB |
| test 1σ coverage | 0.701 | 0.710 |
| n_cv | 14,390 | 17,867 |

CV RMSE is not directly comparable (different, larger CV set) but the fold range
tightened markedly. Like-for-like on cells both runs can predict:

| sheet | rows | n | RMSE old | RMSE new | bias old | bias new | 1σ cov old | 1σ cov new |
|---|---|---|---|---|---|---|---|---|
| antarctic | held-out test cells | 829 | 15.408 | **14.605** | +0.918 | +2.674 | 0.661 | 0.702 |
| antarctic | other cells | 8,659 | 15.358 | 15.367 | +0.778 | +2.594 | 0.654 | 0.661 |
| greenland | held-out test cells | 694 | 12.945 | 13.892 | −1.024 | −1.409 | 0.758 | 0.720 |
| greenland | other cells | 6,734 | 12.362 | 13.125 | +1.149 | +1.122 | 0.785 | 0.749 |

So: Antarctic held-out accuracy and calibration improve; Greenland degrades slightly
(its covariates are byte-identical — this is the shared model reallocating toward
Antarctica now that Antarctica carries 39% more data, visible as the `is_greenland`
reflectivity contrast moving from −1.2 to −3.1 dB); and Antarctic predictions pick up
a ~+2.6 dB positive bias against observed cells.

Predictions move a lot, not just in the filled hole: over the 465,095 cells both runs
predict, median Δ = **+3.73 dB**, mean |Δ| = 4.77 dB, and the interior of East
Antarctica rises by ~10 dB (`map_diff.png`). Antarctic median predicted RSSNR
65.9 → 73.0 dB. Greenland is essentially unchanged (78.2 → 78.7 dB median), which is
the sanity check that the Antarctic shift is real signal and not a refit wobble.

## Caveats worth carrying forward

- Two products across two sheets: inter-product bias is partly confounded with
  `is_greenland`. Where both products are valid they agree well (r = 0.961 on log₁₀
  speed, median ratio −1.0%, MAD 1.15 m/yr), which bounds but does not eliminate this.
- The physical-unit intercepts are referenced to training-set mean conditions, which
  moved (thickness 1,431 → 1,607 m, T_air 251.8 → 248.1 K). Part of the α_a change
  (37.5 → 32.9 dB/km two-way) is re-referencing, not different inferred physics.
- The full-grid posterior-predictive histograms are now over a 13% larger Antarctic
  domain, so `hist_obs_vs_ppc_sheets.png` compares a fixed observation sample against
  a bigger prediction domain. The Antarctic PPC sitting right of the observations is
  partly that, partly the coefficient shift.
- The two `surface_v_error_m_yr` columns are not comparable across sheets: phase-based
  error over Antarctica has a 99th percentile of ~5 m/yr against ~58 m/yr for ITS_LIVE
  over Greenland. Nothing in the model uses this column.
- 845 Antarctic cells predictable under ITS_LIVE are not under 0754 (scattered
  no-data), against 62,593 gained.
