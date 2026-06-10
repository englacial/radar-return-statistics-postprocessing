# Build status — radar-return-statistics-postprocessing

Architecture: `../../claude_notes/initial-plan.md` (parent opr/claude_notes, read-only).

## Done & validated (local, no CI yet)

- **Skeleton**: pyproject (uv), `src/radar_postproc/`, configs, README, CLAUDE.md, git init (no commits yet).
- **config.py**: setdefault defaults + canonical-JSON `config_hash`.
- **io_icechunk.py**: `make_storage` — S3 reads are **anonymous by default** (public
  stores, read-only consumer; no AWS creds needed; `store.anonymous: false` opts into
  from_env). `extract_points` reads a **pinned snapshot** → EPSG:4326 GeoDataFrame. Also derives a per-trace `collection`
  (OPR season name) from `frame_index` → `frame_collections` root attr; manifest records
  the season list under `icechunk.collections`. `resolve_snapshot` helper for re-pinning.
- **sampling.py**: `sample_raster` (in-memory bilinear/nearest via xr.interp) +
  `sample_cog` (windowed rasterio reads of local/remote COGs — no full load).
- **datasets/**: `@register` registry; `bedmachine` (Antarctic netCDF v4 / Greenland
  per-variable GeoTIFF v6, earthaccess), `itslive` (v2 static speed COG, remote
  windowed sampling, ETag as provenance hash). `mar` = **registered stub, deferred**.
- **provenance.py / output.py**: content-derived `run_id`, manifest (embedded in
  parquet + sidecar json).
- **runner.py + click CLI**: `run` / `plot` / `to-csv` / `list-datasets` /
  `validate-config` / `resolve-snapshot`. **Snakefile** DAG: `run` → `plots` + `csv`
  (each a stable `_SUCCESS` marker; csv writes `{run_id}.csv` next to the parquet).
- **plots.py**: per-interpolated-variable sanity map (projected scatter coloured by
  value, NaNs in grey). Snakemake `plots` rule + `radar-postproc plot`. Validated on
  ASE: velocity peaks on Pine Island/Thwaites; bed shows interior marine troughs.
- **Tests**: 9 pass (sampling unit, COG-sampler unit, synthetic-store integration,
  run_id determinism). ruff clean.

## Real smoke test (headline gate) — PASS

`radar-postproc run config/ase.yaml` on the real ASE store (snapshot 9HSDDT9ZSZ4JWR5K59HG,
23,579 QC traces), BedMachine Antarctica v4 + ITS_LIVE Antarctic, ~1m16s:
- `bedmachine_bed_m` vs radar `bed_elevation`: **pearson r = 0.90**, median offset
  −7.9 m (geoid vs WGS84, as expected). `scripts/check_bed_sanity.py`.
- `itslive_v_m_yr`: 23,286/23,579 finite, 0.3–4764 m/yr (plausible ASE ice streams).
- Output: `outputs/ase/842e1d10ddc3.parquet` + manifest.

## Pinned snapshots (config/*.yaml)

ase=9HSDDT9ZSZ4JWR5K59HG · greenland=47YWMVPHYRNBK939GHPG · utig=DS449QPKYFC4E98J1G80 (2026-05-28)

## Plan deltas discovered

- BedMachine Antarctica is **v4** (not v3); Greenland is **v6** as per-variable GeoTIFFs.
- ITS_LIVE: used v2 static speed COG with **remote windowed reads** (plan said
  download/zarr) — the Antarctic COG is ~5 GB so /vsicurl sampling avoids the download.
- `netcdf4` engine for BedMachine nc (h5netcdf needs h5py, not installed).

## Outstanding (paused for user review before CI)

1. **MAR** deferred — need a Greenland source with SMB + t2m. Candidates surveyed:
   Maure 2023 (Zenodo 10007946, SMB only, 6 km, 8 GB); MARv3.12 Greenland (climato.be /
   Zenodo 7591112 etc.). Antarctic MAR: Zenodo 4459259 (Kittel 2021). Awaiting source choice.
2. **Greenland/utig** validated only via `validate-config`; not yet run end-to-end.
   (greenland/utig configs have bedmachine; itslive commented for Greenland.)
3. **CI**: `.github/workflows/augment.yml` done — manual trigger, matrix over the 3
   stores, `uv sync` + `uv run snakemake`, actions/cache for BedMachine downloads,
   outputs as artifacts. Needs repo secrets EARTHDATA_USERNAME/PASSWORD. No test-only
   `ci.yml` yet (offered).
4. No git commit yet → manifest `git.sha` is null until first commit.
