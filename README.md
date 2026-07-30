# radar-return-statistics-postprocessing

Joins external gridded products (BedMachine, ITS_LIVE, ERA5, geothermal heat flow)
onto the per-trace radar metrics produced by
[`radar-return-statistics`](../radar_return_statistics) and writes one
geoparquet per icechunk store — then models required surface SNR across both
ice sheets: a full-ice-sheet covariate grid, a spatially-blocked test/fold
split, and Bayesian models (PyMC) whose predictions are exported as
viewer-ready Zarr.

The upstream project writes per-trace lat/lon, layer picks, powers, noise
estimates and a QC mask to versioned icechunk zarr stores on S3
(`opr-radar-metrics/icechunk/{ase,greenland,utig}`). This project reads a
**pinned snapshot** of one of those stores, samples each configured external
raster at every trace location, and emits a self-describing geoparquet plus a
provenance manifest.

## Quick start

```bash
uv sync --extra test

# List available dataset plugins
uv run radar-postproc list-datasets

# Pin the latest snapshot of a store into its config (one-time)
uv run radar-postproc resolve-snapshot config/ase.yaml

# Validate a config (loads defaults, instantiates plugins)
uv run radar-postproc validate-config config/ase.yaml

# Run the full pipeline
uv run radar-postproc run config/ase.yaml
# -> outputs/ase/ase.parquet + outputs/ase/ase.manifest.json

# Sanity-check map plots of each interpolated variable
uv run radar-postproc plot outputs/ase/ase.parquet
# -> outputs/ase/plots/{column}.png

# Convert a geoparquet output to a flat CSV (drops geometry; lat/lon kept)
uv run radar-postproc to-csv outputs/ase/ase.parquet
# -> outputs/ase/ase.csv

# Or run the whole DAG (extract+sample+merge, then plots + csv) via Snakemake
uv run snakemake --cores 4 --config store=ase
```

`run` extracts trace points from the pinned snapshot, fetches + samples each
dataset, merges the columns onto the points, and writes the parquet with the
manifest embedded in file-level metadata (so a single file is self-describing).

Output filenames are fixed and human-readable (`{store}.parquet`,
`{store}.manifest.json`, `{store}.csv`, `plots/{column}.png`); re-runs overwrite
them in place. The content-derived `run_id` is not in the filenames — it's
embedded in the parquet metadata, the manifest, a leading `# run_id:` comment in
the CSV, and the plot titles (see [Reproducibility](#reproducibility)).

## Configuration

One YAML per store (`config/{ase,greenland,utig}.yaml`). Plain dict + defaults,
matching the upstream style. Key sections:

- `store`: icechunk backend (S3 bucket/prefix/region), read-only.
- `icechunk.snapshot_id`: **pinned** immutable snapshot — reads never go through
  `branch="main"`, so a re-run months later is byte-reproducible.
- `extract`: `qc_only`, `max_traces` (cap for smoke runs), `carry_columns`. A
  per-trace `collection` column (the OPR season/campaign name, e.g.
  `2018_Antarctica_DC8`) is derived automatically from the store; the set of
  seasons is also recorded in `manifest.icechunk.collections`.
- `datasets`: list of `{name, ...kwargs}` referencing registered plugins.

## Dataset plugins

| name | regions | columns | source | auth |
|------|---------|---------|--------|------|
| `bedmachine` | antarctic (NSIDC-0756 v4), greenland (IDBMG4 v6) | per `variables:` — e.g. `bedmachine_bed_m`, `bedmachine_surface_m`, `bedmachine_thickness_m`, `bedmachine_mask`, `bedmachine_errbed_m` | NSIDC | Earthdata |
| `itslive` | antarctic, greenland | `itslive_v_m_yr`, `itslive_v_error_m_yr` | AWS Open Data | none |
| `era5` | global (all stores) | `era5_t2m_mean_K` | WeatherBench2 (GCS) | none |
| `ghf` | antarctic, greenland | `ghf_mW_m2`, `ghf_lower_mW_m2`, `ghf_upper_mW_m2` | Zenodo 17745730 | none |

`era5` samples a long-term mean 2 m air temperature from the WeatherBench2 ERA5
hourly **climatology** (1990–2019, 0.25°, period fixed by the product); the global
mean field is computed once (~6 GB read) and cached at ~4 MB, then reused by every
store.

`ghf` is geothermal heat flow with a lower/upper uncertainty envelope, from the
community-recommended, **re-gridded (non-topographically-corrected)** fields of
Fahrner et al. (2025) / Lösing et al. (2026): Lösing & Ebbing (2021) for Antarctica
and Colgan et al. (2022) for Greenland (without NGRIP by default; `ngrip: true` for
the with-NGRIP variant). Source values are W/m²; output is mW/m². The regridded
version is used because only it carries uncertainties (the topographically corrected
version does not).

`bedmachine` takes a `variables:` list. Continuous fields
(`bed`/`surface`/`thickness`/`errbed`, metres) are sampled bilinearly; the
categorical `mask`
(0=ocean, 1=ice-free-land, 2=grounded-ice, 3=floating-ice, 4=lake-vostok/non-greenland)
is sampled nearest. `errbed` is BedMachine's bed-elevation error. Antarctica ships
all variables in one netCDF; for Greenland, only `bed` has a standalone GeoTIFF, so
requesting other variables pulls the full netCDF (~2.8 GB).

Each plugin is one file in `src/radar_postproc/datasets/` implementing the
`ExternalDataset` protocol (`fetch` / `open` / `sample`), registered via
`@register`. Adding a dataset = one new file + one config entry.

See [`docs/data_sources.md`](docs/data_sources.md) for citations, file provenance,
and how to interpret each error/uncertainty field.

## Modeling pipeline (grid → split → train → benchmark)

Cross-store stages driven by one config, `config/model.yaml`:

```bash
# Everything (uses existing augment outputs; builds grid/split/train/benchmark).
# --rerun-triggers=mtime keeps snakemake from re-deriving augment outputs that
# were built elsewhere (CLI, CI artifacts).
uv run snakemake --cores 4 --rerun-triggers=mtime -- model_all

# Or stage by stage
uv run radar-postproc grid  config/model.yaml   # outputs/model/grid.parquet
uv run radar-postproc split config/model.yaml   # outputs/model/split.parquet + cells.csv + cell_maps/
uv run radar-postproc train config/model.yaml --model linear
uv run radar-postproc benchmark config/model.yaml
```

- **grid** builds a ~5 km grid over all ice (strided BedMachine mask, points on
  native pixel centres, per sheet: antarctic EPSG:3031, greenland EPSG:3413) and
  samples every dataset plugin onto it. Expensive (multi-GB downloads) but rare.
- **split** attaches `required_surface_snr_dB` to each grid point (nearest radar
  observation within `nn_cutoff_m`, ase+utig pooled for the antarctic), assigns
  500 km blocking cells anchored at the projected origin (stable IDs like
  `ant:-3:1`), holds out the hand-picked `split.test_cells`, and distributes the
  rest into `n_folds` folds (seeded, capacity-limited, cell granularity — the
  Roberts et al. 2017 spatial-blocking rationale). Pick test cells from
  `outputs/model/cell_maps/*.png` + `cells.csv`, list them in the config, re-run.
- **train** (per model) runs the spatially-blocked k-fold CV, fits the final
  model on all training folds, predicts the full grid, and writes
  `outputs/model/{model}/`: `metrics.json` (per-fold + pooled RMSE/MAE/coverage,
  sampler diagnostics), `posterior.nc` (ArviZ InferenceData; normalizer +
  feature list in attrs), `predictions.zarr`, and a provenance manifest.
- **benchmark** collects every model's metrics into `benchmark.csv`/`.md`.

`predictions.zarr` has one group per sheet (`antarctic`, `greenland`), each a
regular x/y grid (zarr v2, consolidated metadata, rioxarray `spatial_ref` CRS)
with `pred_mean`, `pred_std_mu` (parameter uncertainty), `pred_std` (full
predictive), `obs_snr_dB`, and `fold` (−1 none / 0..k−1 / 100 test) — openable
directly with `xr.open_zarr(path, group="antarctic")`, QGIS, or a web viewer.

Models are plugins in `src/radar_postproc/models/` (`@register_model`,
mirroring the datasets registry): `linear` (Bayesian linear regression) and
`atten_refl` (the 2020 `mu = atten_rate·thickness − refl` structure). Adding a
model = one file + one entry under `train.models`.

## Reproducibility

Each run is identified by a content-derived
`run_id = sha256(snapshot_id + config_hash + sorted(dataset_hashes))[:12]`; same
inputs → same `run_id`. The `run_id` is **not** in the filenames — output names
are fixed (`{store}.parquet` etc.) and re-runs overwrite in place — so it is
carried inside each artifact instead:

- **parquet**: the `run_id` key in the file-level metadata (and the full manifest
  under `radar_postproc_manifest`).
- **manifest** (`{store}.manifest.json`): `run_id` plus the icechunk snapshot, git
  sha, config (inlined) and hash, per-dataset version/url/sha256, per-column
  sampling method/CRS, and the OPR seasons.
- **csv**: a leading `# run_id: ...` comment (read with
  `pandas.read_csv(path, comment="#")`).
- **plots**: the `run_id` is printed in each plot title.

To recover it programmatically: `radar_postproc.output.read_run_id(parquet_path)`.

Downstream stages chain provenance: each stage's `run_id` hashes its inputs'
run_ids plus its own config section (grid ← dataset hashes; split ← grid run_id
+ the three augment run_ids; train ← split run_id + model entry), so any
prediction traces back to exact snapshots, configs, and dataset versions.

## Credentials

- **Earthdata** (BedMachine via `earthaccess`): `EARTHDATA_USERNAME` /
  `EARTHDATA_PASSWORD` env vars or `~/.netrc`.

## Tests

```bash
uv run pytest tests/unit        # synthetic-fixture samplers + split/model logic, no network
uv run pytest -m integration    # synthetic icechunk store + reproducibility
uv run pytest -m slow           # MCMC tests incl. synthetic split->train end-to-end (offline)
```

## GitHub Actions

`.github/workflows/augment.yml` runs the pipeline for each store on a manual
trigger (`workflow_dispatch`), matrixed over `[ase, greenland, utig]`. Each job is
just the local workflow — `uv sync` then `uv run snakemake --cores 4 --config
store=<store>` — with the BedMachine downloads persisted via `actions/cache` and
the per-store `outputs/` uploaded as an artifact. The only required configuration
is two repo secrets, `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD` (BedMachine);
icechunk and ITS_LIVE need no credentials.

`.github/workflows/model.yml` runs the modeling pipeline (`workflow_dispatch`),
taking the run id of a successful augment run as input and downloading its
artifacts as the split stage's inputs. The finished grid is cached keyed on
`config/model.yaml`, so split/train-only iterations skip the multi-GB grid
downloads; the model outputs are uploaded as one `model` artifact.
