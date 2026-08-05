# radar-return-statistics-postprocessing

Consumer side of [`radar-return-statistics`](https://github.com/englacial/radar-return-statistics):
reads pinned snapshots of the OPR radar-metrics icechunk stores, joins external
gridded products onto each trace ("augmentation" stage), and fits a Bayesian model
of required surface SNR (RSSNR) across both ice sheets, exporting
viewer-ready prediction maps ("model training" stage).

**Start with [`docs/`](docs/)** for a more complete description and key results:
* [`1_rssnr_background.md`](docs/1_rssnr_background.md) covers what RSSNR is and why it's useful
* [`2_input_data.md`](docs/2_input_data.md) covers input quality control and cross-season valdiation
* [`3_model.md`](docs/3_model.md) describes the statistical model and results

## Setup

```bash
git clone https://github.com/englacial/radar-return-statistics-postprocessing
cd radar-return-statistics-postprocessing
uv sync

# BedMachine downloads require NASA Earthdata credentials:
export EARTHDATA_USERNAME=... EARTHDATA_PASSWORD=...
# or create a .netrc file, see: https://nsidc.org/data/user-resources/help-center/creating-netrc-file-earthdata-login
```

## 1. Augmentation

One config per store (`config/{antarctica,greenland}.yaml`), each pinned to an
immutable icechunk snapshot so re-runs are reproducible. Per store:

```bash
uv run snakemake --cores 4 --config store=antarctica
uv run snakemake --cores 4 --config store=greenland
```

Output: `outputs/{store}/{store}.parquet` — per-trace radar metrics joined with
BedMachine, ITS_LIVE, ERA5, and geothermal heat flow, with a provenance
manifest embedded in the file metadata. To move a config to a newer snapshot:
`uv run radar-postproc resolve-snapshot config/<store>.yaml`.

This part is intended to stand on its own and be reusable for other purposes.

## 2. Model training

Driven by a single cross-store config, `config/model.yaml` (grid resolution,
spatial split, censoring/detection settings, season exclusions):

```bash
uv run snakemake --cores 4 --rerun-triggers=mtime -- model_all
```

This builds the full-ice-sheet covariate grid, the spatially-blocked
train/test split, trains the configured models with cross-validation, and
writes per-model outputs to `outputs/model/<model>/`:

- `predictions.zarr` — mean, uncertainty, and observation layers on regular
  per-sheet grids with CRS metadata (open with
  `xr.open_zarr(path, group="antarctic")`)
- `posterior.nc` — self-describing ArviZ posterior (normalizer in attrs)
- `metrics.json` / `benchmark.csv` — cross-validated and held-out metrics

Figure-generation commands for everything shown in the docs are listed in
[`docs/3_model.md`](docs/3_model.md); the scripts live in `scripts/`.

## Reproducibility

Reads are always pinned to `icechunk.snapshot_id`, output
filenames are fixed, and every stage embeds a content-derived `run_id` that
chains to its inputs' run_ids — any prediction traces back to exact snapshots,
configs, and dataset versions. See the manifests next to each output.

Tests: `uv run pytest -q` (offline; synthetic fixtures).
