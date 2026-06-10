"""Sanity-check map plots of each interpolated variable.

For every dataset-produced column (the keys of the manifest's `sampling` block),
scatter the trace points — projected into that column's native CRS — coloured by
the sampled value. Quick visual confirmation that e.g. velocity is high on the
ice streams and bed elevation has sensible structure.
"""

import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


def _load_manifest(parquet_path: Path) -> dict | None:
    """Manifest embedded in parquet metadata, or the sidecar json, or None."""
    meta = pq.read_schema(parquet_path).metadata or {}
    raw = meta.get(b"radar_postproc_manifest")
    if raw:
        return json.loads(raw)
    sidecar = parquet_path.with_suffix("").with_suffix(".manifest.json")
    if sidecar.exists():
        return json.loads(sidecar.read_text())
    return None


def plot_variables(
    parquet_path: str | Path,
    out_dir: str | Path | None = None,
    columns: list[str] | None = None,
) -> list[str]:
    """Write one PNG per interpolated column. Returns the written paths."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parquet_path = Path(parquet_path)
    gdf = gpd.read_parquet(parquet_path)
    manifest = _load_manifest(parquet_path)
    sampling = (manifest or {}).get("sampling", {})

    # Default to the dataset-produced columns; fall back to any non-radar columns.
    cols = columns or list(sampling.keys())
    if not cols:
        raise ValueError("No interpolated columns found (empty manifest sampling block)")

    run_id = (manifest or {}).get("run_id", parquet_path.stem)
    out_dir = Path(out_dir) if out_dir else parquet_path.parent / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Use ONE display projection for every variable so the maps are comparable,
    # regardless of the CRS each value happened to be sampled in (e.g. ERA5 is
    # sampled in EPSG:4326 but should still plot in the same polar stereographic
    # frame as BedMachine/ITS_LIVE). Pick the hemisphere's polar stereographic
    # from the trace latitudes.
    display_crs = "EPSG:3031" if float(gdf.geometry.y.mean()) < 0 else "EPSG:3413"
    pts = gdf.to_crs(display_crs)
    x_all, y_all = pts.geometry.x.to_numpy(), pts.geometry.y.to_numpy()

    written = []
    for col in cols:
        if col not in gdf.columns:
            logger.warning("Column %s not in parquet, skipping plot", col)
            continue
        sampled_crs = sampling.get(col, {}).get("crs", display_crs)
        x, y = x_all, y_all
        v = gdf[col].to_numpy()
        finite = np.isfinite(v)

        fig, ax = plt.subplots(figsize=(8, 8))
        # Robust colour limits (2–98th pct) so a few outliers don't wash it out.
        if finite.any():
            vmin, vmax = np.nanpercentile(v[finite], [2, 98])
        else:
            vmin = vmax = None
        sc = ax.scatter(x[finite], y[finite], c=v[finite], s=4, cmap="viridis",
                        vmin=vmin, vmax=vmax)
        # Show NaN points (out of coverage) in light grey for context.
        if (~finite).any():
            ax.scatter(x[~finite], y[~finite], s=3, color="lightgrey",
                       label=f"NaN ({(~finite).sum()})")
            ax.legend(loc="upper right", fontsize=8)
        fig.colorbar(sc, ax=ax, shrink=0.7, label=col)
        ax.set_aspect("equal")
        ax.set_title(f"{col}\n{run_id} · sampled in {sampled_crs} · n={finite.sum()}/{len(v)}")
        ax.set_xlabel(f"x (m, {display_crs})")
        ax.set_ylabel("y (m)")
        fig.tight_layout()

        path = out_dir / f"{run_id}_{col}.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(str(path))
        logger.info("Wrote %s", path)

    return written
