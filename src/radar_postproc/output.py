"""Geoparquet writer with the manifest embedded in file-level metadata + sidecar."""

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq


def parquet_to_csv(parquet_path: str | Path, csv_path: str | Path | None = None) -> str:
    """Write a flat CSV next to a geoparquet output ({run_id}.csv by default).

    The point geometry is dropped — latitude/longitude are already columns — so the
    CSV is a plain table of trace + sampled values. Returns the CSV path.
    """
    parquet_path = Path(parquet_path)
    gdf = gpd.read_parquet(parquet_path)
    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    csv_path = Path(csv_path) if csv_path else parquet_path.with_suffix(".csv")
    df.to_csv(csv_path, index=False)
    return str(csv_path)


def write_output(gdf: gpd.GeoDataFrame, manifest: dict, out_dir: str | Path, store_name: str) -> dict:
    """Write {store}/{run_id}.parquet (+ .manifest.json sidecar).

    The manifest is also embedded in the parquet file-level metadata so a single
    file is self-describing. Returns {parquet, manifest} paths.
    """
    run_id = manifest["run_id"]
    out = Path(out_dir) / store_name
    out.mkdir(parents=True, exist_ok=True)
    parquet_path = out / f"{run_id}.parquet"
    manifest_path = out / f"{run_id}.manifest.json"

    # GeoPandas writes geoparquet 1.1 (spatial index for downstream consumers).
    gdf.to_parquet(parquet_path, index=False)

    # Re-open to graft the manifest into key-value file metadata, preserving the
    # geo metadata GeoPandas already wrote.
    table = pq.read_table(parquet_path)
    existing_meta = dict(table.schema.metadata or {})
    existing_meta[b"radar_postproc_manifest"] = json.dumps(manifest, default=str).encode()
    table = table.replace_schema_metadata(existing_meta)
    pq.write_table(table, parquet_path)

    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    return {"parquet": str(parquet_path), "manifest": str(manifest_path)}
