# GitHub Actions automation — proposal (2026-06-10)

Goal: run the augmentation pipeline automatically and reproducibly, for all three
stores, with minimal secrets and sensible caching.

## What the pipeline actually needs (learned from local runs)

| Concern | Reality | Implication for CI |
|---|---|---|
| Input icechunk read | **anonymous** public S3 (no AWS creds) | no AWS auth for reads |
| BedMachine | Earthdata login via `earthaccess` | needs `EARTHDATA_*` secrets |
| ITS_LIVE | public HTTPS, **remote windowed COG** (no download) | no cache needed, no auth |
| Downloads to cache | BedMachine netCDF: Antarctic ~1.1 GB, Greenland ~2.8 GB | cache to avoid re-downloads |
| Runtime | ase/utig ~1–3 min, greenland ~10–15 min (cold) | matrix runs them in parallel |
| Memory | peak ~2.7 GB locally | fine on 16 GB hosted runners |
| Disk | 1.1 + 2.8 GB cache + outputs | under runner's ~14 GB and the 10 GB Actions-cache cap |
| Reproducibility | same inputs → same `run_id` | safe to dedup / skip rewrites |

Net: the **only required secret is Earthdata**. AWS is needed *only if* we choose to
publish outputs to S3 (write).

## Two workflows

### 1. `ci.yml` — fast, no network, no secrets (push / PR)
- `uv sync --extra test`
- `uv run ruff check`
- `uv run pytest -m "not integration"` + the synthetic-store integration tests
  (they build a *local* icechunk store, so still no network)
- Purpose: guard every change. ~1–2 min.

### 2. `augment.yml` — the real runs (scheduled + manual + on-config-change)
- **Triggers**: `workflow_dispatch`, weekly `cron`, and `push` to `main` touching
  `config/**` or `src/radar_postproc/datasets/**`.
- **Matrix** over `store: [ase, greenland, utig]` (parallel jobs).
- **Steps per store**:
  1. `uv sync`
  2. restore BedMachine download cache (key on dataset short_name+version)
  3. `uv run snakemake --cores 4 --config store=${{ matrix.store }}`
     (produces parquet + manifest + plots + csv)
  4. upload `outputs/${store}/{run_id}.*` and plots as a workflow **artifact**
  5. (optional) publish — see below
- **Secrets**: `EARTHDATA_USERNAME`, `EARTHDATA_PASSWORD` (consumed by
  `earthaccess.login(strategy="environment")`, which my plugin already triggers).

## Caching the big downloads

```yaml
- uses: actions/cache@v4
  with:
    path: outputs/cache
    key: bedmachine-${{ matrix.store }}-${{ hashFiles('config/' + matrix.store + '.yaml') }}
    restore-keys: bedmachine-${{ matrix.store }}-
```
ase/utig share the same Antarctic netCDF; a cross-store key (`bedmachine-antarctic-v4`)
would let them share one cached copy. Greenland's 2.8 GB + Antarctic's 1.1 GB sit
under the 10 GB per-repo Actions-cache budget. ITS_LIVE needs no cache (remote reads).

## Where do the outputs go? (pick one)

- **A. Workflow artifacts only** (default). Zero extra auth, 90-day retention.
  Good for review / manual download. *Recommended to start.*
- **B. Commit to an `outputs` orphan branch / GitHub Release.** Durable, versioned,
  no AWS. Parquets are small (ase ~2 MB, greenland ~15 MB). Release assets are clean.
- **C. Push to `s3://opr-radar-augment/{store}/{run_id}/`.** Matches the original
  plan; needs an **AWS OIDC role** (`permissions: id-token: write`) — the only thing
  that reintroduces AWS auth. Best if downstream consumers read from S3.

Because `run_id` is content-derived, the publish step can **skip** when
`{run_id}` already exists at the destination → no churn on unchanged inputs.

## Optional: auto-refresh the pinned snapshot

Configs pin `icechunk.snapshot_id` for reproducibility, so scheduled runs are
*stable* but won't pick up new upstream data on their own. A small extra job can:
1. `radar-postproc resolve-snapshot config/<store>.yaml`
2. if it differs from the pinned value, open a **PR** bumping `snapshot_id`.
This keeps reproducibility (human-reviewed bump) while surfacing new data. Off by
default; enable per store if wanted.

## Suggested robustness tweak (not required)

BedMachine currently full-loads each variable for `xr.interp` (~2.7 GB peak). It's
fine on 16 GB runners, but switching it to the same **windowed COG/netCDF sampling**
ITS_LIVE uses would drop peak memory to ~MBs and make CI bullet-proof on any runner
size. Small, isolated change to `bedmachine.py`.

## Open decisions for the user

1. Output destination: **A (artifacts)** / B (release) / C (S3 + OIDC)?
2. Auto-re-pin snapshot PRs: yes / no (and which stores)?
3. Cron cadence: weekly (default) / monthly / dispatch-only?
4. Do the BedMachine windowed-sampling tweak now, or leave full-load?
