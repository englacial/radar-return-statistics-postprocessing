Use uv for dependency management and `uv run` to run anything.

Do not edit anything outside of this directory.

This repo (`radar-return-statistics-postprocessing`) is the consumer side of the upstream
`radar-return-statistics`.
It reads a *pinned snapshot* of the upstream `radar-return-statistics` icechunk
stores (`s3://opr-radar-metrics/icechunk/{ase,greenland,utig}`) and joins
external gridded products onto each trace. The upstream producer lives at
`../radar_return_statistics` (read-only reference).

`radar-return-statistics` lives at https://github.com/englacial/radar-return-statistics
There is a checked-out local copy at `../radar-return-statistics`. Do not edit it.

Architecture and decisions: `agent_notes/initial-plan.md` (parent dir).

Conventions:
- Plain-dict YAML config + `setdefault` defaults (`config.py`), mirroring upstream.
  No pydantic/typer. Config hashed via canonical `json.dumps(sort_keys=True)`.
- Reads are **always pinned** to `icechunk.snapshot_id` — never `branch="main"` —
  so re-runs are reproducible. Re-pin with `radar-postproc resolve-snapshot`.
- One dataset = one file in `src/radar_postproc/datasets/` implementing the
  `ExternalDataset` protocol + `@register`. CRS handling lives in `sampling.py`.
- Outputs use fixed names (`outputs/{store}/{store}.parquet`); the content-derived
  `run_id` is embedded in the parquet metadata + sidecar manifest (`provenance.py`).

Modeling stages (grid -> split -> train -> benchmark) are cross-store, driven by
`config/model.yaml` (kept separate so modeling params never perturb augment
run_ids), and live in `src/radar_postproc/{grid,split,train}.py`:
- One model = one file in `src/radar_postproc/models/` + `@register_model`
  (registry mirrors datasets). Models subclass `BaseBayesianModel` (PyMC),
  implementing `build()` and the analytic `mu_draws()`.
- Downstream run_ids chain upstream run_ids + own config section hash.
- Run with `uv run snakemake --cores 4 model_all` (no `store=`).
- Plan: `agent_plans/20260728-split-train-stages.md`.

Plotting conventions (shared constants: `scripts/plot_style.py`):
- Color encodes ice sheet: Antarctica = tab:blue, Greenland = tab:green.
  Anything that is both, neither, or not sheet-specific = tab:orange (or another
  non-reserved color if several such series appear together).
- Linestyle encodes data source: solid = observations/training data,
  dashed = model predictions (dotted = posterior-predictive draws).

Outputs and caches go under `outputs/` (gitignored).
