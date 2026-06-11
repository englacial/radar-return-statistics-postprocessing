"""Geoparquet writer with fixed, human-readable filenames.

Each store writes outputs/{store}/{store}.parquet (+ .manifest.json sidecar). The
content-derived run_id is intentionally NOT in the filename — it lives in the
parquet file-level metadata (key ``run_id``, plus the full manifest) and the
sidecar manifest, so re-runs overwrite the same files while staying identifiable.
"""

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

_MANIFEST_KEY = b"radar_postproc_manifest"
_RUN_ID_KEY = b"run_id"


def write_output(gdf: gpd.GeoDataFrame, manifest: dict, out_dir: str | Path, store_name: str) -> dict:
    """Write {store}.parquet (+ {store}.manifest.json). Returns the paths.

    The manifest is embedded in the parquet file-level metadata, and the run_id is
    additionally stored under its own ``run_id`` key, so a single file is
    self-describing.
    """
    out = Path(out_dir) / store_name
    out.mkdir(parents=True, exist_ok=True)
    parquet_path = out / f"{store_name}.parquet"
    manifest_path = out / f"{store_name}.manifest.json"

    # GeoPandas writes geoparquet 1.1 (spatial index for downstream consumers).
    gdf.to_parquet(parquet_path, index=False)

    # Re-open to graft run_id + manifest into key-value file metadata, preserving
    # the geo metadata GeoPandas already wrote.
    table = pq.read_table(parquet_path)
    meta = dict(table.schema.metadata or {})
    meta[_MANIFEST_KEY] = json.dumps(manifest, default=str).encode()
    meta[_RUN_ID_KEY] = str(manifest["run_id"]).encode()
    pq.write_table(table.replace_schema_metadata(meta), parquet_path)

    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    return {"parquet": str(parquet_path), "manifest": str(manifest_path)}


def read_run_id(parquet_path: str | Path) -> str | None:
    """Read the embedded run_id from a parquet output (or None)."""
    meta = pq.read_schema(parquet_path).metadata or {}
    if _RUN_ID_KEY in meta:
        return meta[_RUN_ID_KEY].decode()
    raw = meta.get(_MANIFEST_KEY)
    return json.loads(raw)["run_id"] if raw else None


def parquet_to_csv(parquet_path: str | Path, csv_path: str | Path | None = None) -> str:
    """Write a flat CSV next to a geoparquet output ({store}.csv by default).

    Geometry is dropped (latitude/longitude remain columns). The run_id is written
    as a leading ``# run_id: ...`` comment so the flat file stays identifiable;
    read it back with ``pandas.read_csv(path, comment="#")``.
    """
    parquet_path = Path(parquet_path)
    gdf = gpd.read_parquet(parquet_path)
    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    csv_path = Path(csv_path) if csv_path else parquet_path.with_suffix(".csv")
    run_id = read_run_id(parquet_path)
    with open(csv_path, "w", newline="") as fh:
        if run_id:
            fh.write(f"# run_id: {run_id}\n")
        df.to_csv(fh, index=False)
    return str(csv_path)
