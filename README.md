# radar-return-statistics-postprocessing

Joins external gridded products (BedMachine, ITS_LIVE, ERA5, geothermal heat flow)
onto the per-trace radar metrics produced by
[`radar-return-statistics`](../radar_return_statistics) and writes one
geoparquet per icechunk store.

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
# -> outputs/ase/{run_id}.parquet + outputs/ase/{run_id}.manifest.json

# Sanity-check map plots of each interpolated variable
uv run radar-postproc plot outputs/ase/{run_id}.parquet
# -> outputs/ase/plots/{run_id}_{column}.png

# Convert a geoparquet output to a flat CSV (drops geometry; lat/lon kept)
uv run radar-postproc to-csv outputs/ase/{run_id}.parquet
# -> outputs/ase/{run_id}.csv

# Or run the whole DAG (extract+sample+merge, then plots + csv) via Snakemake
uv run snakemake --cores 4 --config store=ase
```

`run` extracts trace points from the pinned snapshot, fetches + samples each
dataset, merges the columns onto the points, and writes the parquet with the
manifest embedded in file-level metadata (so a single file is self-describing).

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

## Reproducibility

Output is a pair: `{run_id}.parquet` + `{run_id}.manifest.json`, where
`run_id = sha256(snapshot_id + config_hash + sorted(dataset_hashes))[:12]`. Same
inputs → same `run_id` → safe dedup. The manifest records the icechunk snapshot,
git sha, config (inlined) and hash, per-dataset version/url/sha256, and the
sampling method/CRS per column.

## Credentials

- **Earthdata** (BedMachine via `earthaccess`): `EARTHDATA_USERNAME` /
  `EARTHDATA_PASSWORD` env vars or `~/.netrc`.

## Tests

```bash
uv run pytest tests/unit        # synthetic-fixture samplers, no network
uv run pytest -m integration    # synthetic icechunk store + reproducibility
```

## GitHub Actions

`.github/workflows/augment.yml` runs the pipeline for each store on a manual
trigger (`workflow_dispatch`), matrixed over `[ase, greenland, utig]`. Each job is
just the local workflow — `uv sync` then `uv run snakemake --cores 4 --config
store=<store>` — with the BedMachine downloads persisted via `actions/cache` and
the per-store `outputs/` uploaded as an artifact. The only required configuration
is two repo secrets, `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD` (BedMachine);
icechunk and ITS_LIVE need no credentials.
