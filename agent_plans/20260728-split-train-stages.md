# 2026-07-28: grid / split / train / benchmark stages

Extends the repo past `augment` into a modeling pipeline, modernizing the 2020
`radioglaciology/snr_paper_2020` work (private repo): spatially-blocked
geographic segmentation with a held-out test set and k folds, then Bayesian
models of `required_surface_snr_dB` with predictions across both full ice
sheets, exported as viewer-ready Zarr.

## Decisions

- **Target**: `required_surface_snr_dB` (+ `pre_surface_noise_dB`,
  `post_bed_noise_dB`) added to the default `extract.carry_columns` — augment
  run_ids changed and all three stores were re-run.
- **Pooled model** across both ice sheets with a raw `is_greenland` indicator
  appended to the z-scored features. Antarctic = ase+utig merged (EPSG:3031),
  Greenland separate (EPSG:3413).
- **Regrid like 2020**: training data lives on the coarsened BedMachine grid
  (strided `isel`, points on native pixel centres; 500 m × stride 10 = 5 km for
  Antarctica, 150 m × stride 33 ≈ 4.95 km for Greenland), target attached by
  KD-tree nearest neighbour within `split.nn_cutoff_m` (1 km default).
- **Prediction extent**: the full ice sheets — the grid stage samples every
  dataset plugin at every ice grid point (~541k antarctic + ~93k greenland),
  so prediction is just the fitted model applied to the whole grid.
- **Cells anchored at projected (0,0)** (not the data extent, which the 2020
  code used): IDs like `ant:-3:1` are stable across data updates and unique
  across sheets. Fold assignment is the 2020 greedy capacity-limited algorithm
  with one fix (the last fold never closes, so assignment can't run dry).
- **Test cells are hand-picked** into `split.test_cells` using
  `outputs/model/cell_maps/*.png` + `cells.csv`; empty list = warning, no test
  metrics, everything else still runs.
- **PyMC v5-style API on PyMC 6 / ArviZ 1.x** (DataTree-based). Models are
  registry plugins (`@register_model`) subclassing `BaseBayesianModel`:
  `build()` makes the PyMC graph, `mu_draws()` applies the mean function
  analytically to posterior draws (fast batched full-grid prediction, no
  pytensor recompute). Both `pred_std_mu` (parameter uncertainty) and
  `pred_std` (incl. observation noise) are exported.
- **Provenance chains**: grid run_id ← dataset sha256s + grid config section;
  split ← grid run_id + 3 augment run_ids + split section; train ← split
  run_id + model entry. Fixed-name outputs, run_id embedded (same as augment).
- `config/model.yaml` is separate from the per-store configs so modeling
  params never perturb augment run_ids.
- ITS_LIVE at grid scale needs `download: true` (per-point remote windowed
  reads measured ~140 reads/s → would be ~8 h; local COGs are minutes).

## Layout

- `src/radar_postproc/grid.py` — `build_grid_points` (pure), `sample_covariates`,
  `run_grid`. Reuses dataset plugins unmodified.
- `src/radar_postproc/split.py` — `cell_ids`, `assign_target_nn`,
  `assign_folds` (all pure), cell maps + `cells.csv`, `run_split`.
- `src/radar_postproc/train.py` — CV loop, final fit, batched grid prediction,
  `predictions.zarr` (zarr v2, per-sheet groups, rioxarray CRS), `posterior.nc`
  (normalizer + features in attrs), `metrics.json`, `write_benchmark`.
- `src/radar_postproc/models/` — registry (`__init__`), `base`, `normalize`,
  `linear`, `atten_refl`.
- Snakefile: `model_all` → per-model `train` (wildcard) + `benchmark`;
  `split` ← `grid` + the three store parquets. `rule run` now derives its
  config from `wildcards.store` (was a latent single-store bug).
- `.github/workflows/model.yml`: dispatch with `augment_run_id` input,
  downloads augment artifacts, caches the finished grid keyed on
  `config/model.yaml`, prunes ITS_LIVE COGs + GHF zip before cache save.

## Status

- [x] Phase 1: carry columns + Snakefile wildcard fix + re-augment (all stores)
- [x] Phase 2: grid stage (code + tests; full run: see below)
- [x] Phase 3: split stage (code + tests)
- [x] Phase 4: train stage + linear model (code + tests)
- [x] Phase 5: atten_refl + benchmark (code + tests)
- [x] Phase 6: model.yml workflow + docs
- [x] Real-data run: grid (616,536 points, ~14 min incl. downloads), split
      (10,957 train points, 5 folds), both models trained + benchmark.
      First results: atten_refl CV RMSE 15.4 dB, linear 16.3 dB, both 0
      divergences, r-hat ≤ 1.005, 1σ coverage 0.66.
- [ ] User picks `split.test_cells` from cell_maps, re-runs split + train

## Follow-ups / open items

- Detection-aware model (learned detection threshold, non-detection traces from
  the ASE reprocessing) + planned empirical detection-curve checks per season:
  see `agent_notes/20260729-detection-model-design.md`.

- `split.test_cells` is empty until the user picks cells from the maps.
- ISMIP6 basal temperature and an SMB product (RACMO/MAR) were 2020 covariates
  with no current plugin; GHF stands in for basal conditions. Add as dataset
  plugins if wanted.
- Optional LOO/`az.compare` column in the benchmark (needs stored
  log-likelihood; currently off to keep posterior.nc small).
- Publishing predictions somewhere durable (S3?) is still undecided, same as
  the augment artifacts question in `20260610-github-actions-automation.md`.
