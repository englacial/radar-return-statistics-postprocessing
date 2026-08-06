# Reading `predictions.zarr` directly from the mission design tool

What the upstream pipeline would need to change so `mission_design_tool/` can
consume `outputs/model/<model>/predictions.zarr` over HTTP with no
`build_data.py` export step. Written 2026-08-05; nothing here is implemented.

Accepted trade: the single-file `dist/mission_design_tool.html` build goes away.
A multi-file zarr store cannot be inlined into one HTML, and `file://` blocks the
fetches. The GitHub Pages build is unaffected.

## Correction to an earlier size estimate

I previously said direct consumption would cost roughly 4× the bytes. That was
wrong — it assumed float32 rasters. Measured, with the layers gzip-compressed:

| layer | dtype | float32 + gzip | int16 + gzip |
|---|---|---|---|
| `pred_mean` | f4 | 1.85 MB | 0.76 MB |
| `pred_std` | f4 | 1.22 MB | **0.02 MB** |
| `thickness` | f4 | 1.95 MB | 0.91 MB |
| `mask` | u1 | 0.02 MB | 0.02 MB |
| Greenland, all four | | 0.69 MB | 0.24 MB |
| **total** | | **5.71 MB** | **1.94 MB** |

Current packed export: **2.59 MB**. So a dense int16 zarr is *smaller* than the
hand-packed sparse export, because the sparse form spends 4 bytes per cell on a
`uint32` index (2.4 MB, monotonic, nearly incompressible) to skip cells that
gzip already flattens to almost nothing as NaN runs. `pred_std` collapses to
20 KB because it barely varies.

The size argument for the export step does not hold. The remaining arguments are
the codec, the missing layers, and the single-file build.

## Change 1 — compress with gzip/zlib instead of blosc (required)

`predictions.zarr/*/pred_mean/.zarray` currently declares:

```json
"compressor": {"id": "blosc", "cname": "lz4", "clevel": 5, "shuffle": 1}
```

Browsers have no blosc. `DecompressionStream` supports only `gzip`, `deflate`,
and `deflate-raw`, so the alternative is a numcodecs blosc WASM build — a real
dependency plus a WASM fetch, against the tool's no-dependency constraint.

`numcodecs.GZip` writes plain gzip frames, which `DecompressionStream('gzip')`
reads directly. In `write_predictions_zarr` (`train.py:212`):

```python
from numcodecs import GZip
enc = {name: {"compressor": GZip(level=6)} for name in ds.data_vars}
ds.to_zarr(zarr_path, group=sheet, mode="w" if i == 0 else "a",
           zarr_format=2, consolidated=True, encoding=enc)
```

Compatibility impact: none that matters. The docstring's "maximum viewer/QGIS
compatibility" rationale is about zarr v2 + consolidated metadata, not the
codec; gzip is the most universally supported zarr compressor. Expect the store
to grow somewhat versus blosc-lz4 — worth measuring, but it is 5.7 MB today.

## Change 2 — add thickness and mask layers (required)

The two per-cell terms the link budget needs (`Δspreading`, adaptive
`G_pulse-comp`) both depend on ice thickness, and the floating/grounded split
needs the BedMachine mask. Neither is in the zarr today, which is the only
reason `build_data.py` also opens the 60 MB `grid.parquet`.

`split.parquet` already carries both columns, and `run_train` already holds that
frame as `df`, so this is additive. In `run_train` where `layers` is assembled
(`train.py:~343`):

```python
layers = {**full, "obs_snr_dB": df[target].to_numpy(), "fold": fold_layer,
          "bedmachine_thickness_m": df["bedmachine_thickness_m"].to_numpy(),
          "bedmachine_mask": df["bedmachine_mask"].to_numpy()}
```

`write_predictions_zarr` fills non-`fold` layers with NaN, which is correct for
thickness. `mask` should get an integer fill instead — either add it to the
`fold` branch or give the rasterizer a per-layer fill/dtype map. Mask values stay
BedMachine's (2 grounded, 3 floating, 4 Lake Vostok / non-Greenland land); the
tool's floating test is `mask == 3`.

Add matching `attrs` alongside the existing ones at `train.py:202-206`.

This also makes the zarr self-sufficient for QGIS and any other consumer, which
seems independently worthwhile.

## Change 3 — int16 encoding (optional, 3× smaller)

Per the table above. Via xarray encoding:

```python
enc[name] |= {"dtype": "int16", "scale_factor": 0.1, "_FillValue": -32768}
```

Caveat: this is CF convention. `xr.open_zarr` decodes it automatically; JS zarr
readers do **not**, so the tool must apply `scale_factor`/`_FillValue` itself
from `.zattrs`. That is a few lines, but it is a silent-wrong-answer hazard if
someone later writes another JS consumer and forgets. Thickness at
`scale_factor: 1.0` fits `int16` (max 4715 m); RSSNR at `0.1` spans ±3276 dB.

Recommendation: land changes 1 and 2 first, treat 3 as a follow-up. 5.7 MB is
already acceptable, and float32 keeps the store trivially correct for every
reader.

## Change 4 — chunking (optional)

Antarctic layers are chunked 334×334 → 16 chunks each. The tool always wants the
whole array, so 4 layers × 16 chunks × 2 sheets ≈ 100 HTTP requests per load.
Not fatal over HTTP/2, but a single chunk per array would make it 8 requests.
Only worth doing if load latency turns out to be noticeable — it hurts partial
readers like QGIS panning, so probably leave it alone.

## What the tool side becomes

Delete `build_data.py` and `data/`. Replace `loadSheet()` with a minimal zarr v2
reader — roughly 60 lines, no dependencies:

1. `fetch('…/predictions.zarr/.zmetadata')` — one request, 10.7 KB, already
   consolidated, giving every array's shape/chunks/dtype/compressor/attrs.
2. For each needed array, fetch its chunk keys (`0.0`, `0.1`, …), pipe each
   through `DecompressionStream('gzip')`, and blit into a typed array. Row-major
   `order: "C"` and `dimension_separator: "."` are already what the store uses.
3. Read `x`/`y` coordinate arrays for the geometry that `data/meta.json`
   supplies today.

Still fetched as plain JSON, unchanged: `metrics.json` (θ, τ, CV RMSE) and
`manifest.json` (`run_id` for the About dialog). Both are small and
browser-readable as-is.

`physics.js` is untouched — it already takes typed arrays of thickness and RSSNR
and knows nothing about where they came from. `selftest.mjs` would need a node
zarr reader or a small fixture.

## Consequences

- **Lost:** the single-file build; offline use without a server; the
  `run_id`-in-`meta.json` staleness check (replaced by reading `manifest.json`
  live, which is strictly better — it cannot go stale).
- **Gained:** no export step to forget; the tool always reflects whatever model
  output is deployed alongside it; `predictions.zarr` becomes self-describing
  for every consumer, not just this one.
- **Deployment:** GitHub Pages must serve the zarr store's ~1400 chunk files.
  That is fine for Pages, but the repo would carry them, and a redeploy after
  retraining is a large-ish diff. Worth considering whether the store is copied
  into the published directory or fetched from S3 with CORS.

## Order of work

1. `train.py`: GZip codec + thickness/mask layers (changes 1 and 2). Re-run
   `snakemake model_all`; confirm `xr.open_zarr` and QGIS still open the store.
2. Tool: minimal zarr reader replacing `loadSheet`; delete `build_data.py`,
   `build_standalone.py`, `data/`, `dist/`.
3. Decide on int16 encoding once real load times are visible.
