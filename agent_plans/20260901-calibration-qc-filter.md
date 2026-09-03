# Radiometric calibration QC filter (2026-09-01)

Branch: `qc-calibration-filter`.

## Context

Upstream `radar-return-statistics` shipped calibration method 0.4.0 (refreshed to 0.4.1 on 2026-09-03: 2018_Antarctica_DC8's rising img2 envelope no longer counts as a ceiling, 49 Antarctic frames backfilled, variable attrs added)
(`docs/dataset_changelog.md`, 2026-09): four per-trace diagnostics —
`img_comb_offset_dB`, `img_comb_pair`, `surface_source_image_index`,
`surface_ceiling_margin_dB` — plus a season-keyed `saturation` root attr. No
pre-existing values changed (verified byte-identical upstream), so a model
retrained on the re-pinned snapshots differs from the 2026-08-07 baseline only
through the QC filter.

Stores landed 2026-09-02 00:36 UTC (0.4.0: antarctica SYAKG11X8AFFY0H9H65G,
greenland JWDABR34HPM816P70FD0, ase WQCXS05H226PX1KGRZ2G) and were refreshed
2026-09-03 15:51 UTC (0.4.1, the pinned ones: antarctica 087JBD7NTAE8BTBTEYSG,
greenland ACA8WY61ZF9W6VSBA3HG, ase F6TXWSEQ9RQCD1H7MSMG). utig / crosssystem not updated (not model inputs).

## Plan

1. Carry the four calibration columns through augment (`extract.carry_columns`
   default; warn-skipped on stores without them).
2. Implement the suggested filter as `split.calibration_qc` (split stage, next to
   `exclude_collections`) so thresholds can be varied without re-running augment,
   and so it applies to observations AND non-detections before matching:
   - seam: `|img_comb_offset_dB| >= 3 dB` -> drop; NaN passes unless
     `drop_unmeasured_seam` (dropping NaN would remove 93% of 2014_Greenland_P3
     — `insufficient_overlap` is a geometry limitation, not evidence of a step).
   - img2: `surface_source_image_index >= 2` -> drop (-1 unknown passes). Kept
     as suggested: img2-sourced traces have median RSSNR 8–17 dB lower than
     img1 traces of the same DC8 season (surface-power low bias), and the rule
     costs only 3% of Antarctic / 4% of Greenland traces.
   - saturated: `surface_ceiling_margin_dB < 2 dB` -> drop; NaN (no credible
     season ceiling) passes.
3. Back up the pre-QC outputs (`outputs/baseline_20260807/`: model/ + augment
   parquets), re-pin snapshots, `snakemake model_all`.
4. Compare with `scripts/qc_filter_comparison.py` (per-season removal, threshold
   sensitivity, benchmark side by side, both posteriors on the SAME test points,
   posterior overlays, prediction-difference maps) and
   `scripts/season_crossover_matrix.py --calibration-qc` (model-free check).
5. Keep `exclude_collections` unchanged for the main comparison (one change at a
   time); report how much of each excluded season the QC alone would catch.

## Status

- [x] 1–2 implemented + unit tests (`tests/unit/test_calibration_qc.py`).
- [x] 3 pipeline run (2026-09-02 on 0.4.0; re-run 2026-09-03 16:13–16:27 UTC on 0.4.1 — identical posteriors; run_ids split 4ff998437f3b, atten_refl af5514ae3b72, linear c0b385764662)
- [x] 4 comparison + assessment (`agent_notes/20260901-calibration-qc-results.md`)
- [x] docs (`docs/2_input_data.md`); Snakefile: store + model configs are now rule inputs
