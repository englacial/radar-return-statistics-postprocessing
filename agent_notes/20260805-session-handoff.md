# Session handoff — 2026-08-05

State snapshot at the end of the long modeling session (grid/split/train pipeline
built; detection-aware model adopted; season outliers excluded). Read alongside
`agent_plans/20260728-split-train-stages.md` and
`agent_notes/20260729-detection-model-design.md`.

## Current recommended model & headline numbers

`atten_refl` + Tobit censoring (margin < 10 dB vs `post_bed_noise_interp_dB`)
+ detection layer (θ, τ; δ < 8 dB pre-filter on) + `indicators: [is_greenland]`
+ `split.exclude_collections: [2017_Antarctica_Basler, 2013_Greenland_P3]`
(crossover-identified calibration outliers; exclusion improved held-out accuracy).

- CV RMSE 14.92 dB [14.13–16.77], coverage 0.68; test RMSE 14.39 dB, coverage 0.70
- σ ≈ 15.0 dB; θ = −1.8 ± 0.4 dB; τ = 3.2 ± 0.2 dB; 0 divergences, R̂ ≤ 1.002
- 20,810 observed / 934 non-detect grid points; snapshots pinned in configs
  (antarctica VE13DY3F546ZE1J9KC60, greenland GEAMAHQ7BRVPG9SQPK20,
  ase CS9HEDFGE8FXDHVC9KM0 — ase is analysis-only, not a model input)
- Physical units: 37.5 dB/km two-way attenuation at mean conditions;
  conversion machinery in `scripts/posterior_physical.py` (posterior.nc is
  self-describing — normalizer constants in attrs)

## Uncommitted working tree (as of handoff)

- Doc audit edits to `docs/{1_rssnr_background,2_input_data,3_model}.md`
  (TODOs filled, fact-check fixes — notably the excluded Antarctic season is
  `2017_Antarctica_Basler` (CReSIS), NOT BaslerJKB) + refreshed
  `docs/figures/map_{pred_mean,q80}.png`
- New: `scripts/posterior_physical.py` (+ `docs/figures/posterior_physical.png`),
  `scripts/prediction_summary_figures.py` — both referenced by 3_model.md's
  repro block, so commit them together with the doc edits
- `docs/required-snr-model.md` untracked: superseded by the three numbered
  docs — user to decide delete vs keep
- `LICENSE` and `docs/UAV IPR Link Budget.ipynb` are the user's own additions

## Open modeling threads (rough priority)

1. **Step-5 season calibration** (discussed, not decided): the crossover
   matrices (`scripts/season_crossover_matrix.py`) over-determine per-season
   offsets — least-squares them into a data correction or informative priors.
   In-model alternative: hierarchical per-season offsets grouped by institution
   (CReSIS wide variance, UTIG narrow — see memory note). Binary `is_utig` was
   tested and rejected (ablation ≈ 0 coefficient; colocated +5.6 dB offset is
   season-pair structure, not institutional).
2. **θ anchor refinement**: θ slightly negative because Tobit-censored
   (saturated) picks enter the detection selection factor with ~0 dB margins;
   exclude them from the selection factor and θ should become physical.
3. **δ-filter refinement**: window-length-normalized δ (expected noise peak from
   `record_end_twtt` + window size) instead of fixed 8 dB; Greenland's δ
   bimodality is shallower than ASE's, so classified fractions are
   cutoff-sensitive (9%/15%/20% at 6/8/10 dB).
4. Residual σ ≈ 15 dB is a feature ceiling — SMB and ISMIP6 basal temperature
   are the known missing 2020-era covariates (dataset plugin system makes these
   one file each).
5. PIT histogram for full calibration (only 1σ coverage checked today);
   optional LOO/az.compare (needs stored log-likelihood, currently off).

## Gotchas for the next agent

- ArviZ 1.x: `az.summary` returns *strings* for some columns (r_hat etc.) and
  DataTree groups need `.to_dataset()`; `plot_posterior` is gone. See
  `models/base.py` diagnostics for the safe patterns.
- Snakemake: always `--rerun-triggers=mtime` for `model_all` (augment outputs
  are built elsewhere); `--forcerun split` after config-only changes.
- Keep analysis scripts in `scripts/` (repo), never only in the scratchpad —
  /tmp cleanup destroyed one agent's scripts mid-session (figure survived,
  producer lost, had to be reconstructed as `scripts/residual_audit.py`).
- Extraction caches in `outputs/cache/*_reprocessed_traces_v*.parquet` are
  stale after re-pins — delete when the snapshot changes.
- `pm.Data` rejects NaN: non-detect rows must be feature-complete before
  entering the design matrix (see `residual_audit.py` fix).
- Upstream `radar-return-statistics` producing code for the reprocessed stores
  was not pushed to GitHub main as of 2026-08-03 — verify before relying on
  manifest git provenance.
