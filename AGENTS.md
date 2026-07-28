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
- Output = `{run_id}.parquet` + `{run_id}.manifest.json`, manifest also embedded
  in the parquet metadata. `run_id` is content-derived (see `provenance.py`).

Outputs and caches go under `outputs/` (gitignored).
