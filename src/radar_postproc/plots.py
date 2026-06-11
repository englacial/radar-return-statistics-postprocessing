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

# Labels for the categorical BedMachine mask codes (Antarctica/Greenland share 0-3).
MASK_LABELS = {0: "ocean", 1: "ice-free land", 2: "grounded ice",
               3: "floating ice", 4: "lake-vostok / non-greenland"}


def _category_label(col: str, code: int) -> str:
    if col == "bedmachine_mask" and code in MASK_LABELS:
        return f"{code}: {MASK_LABELS[code]}"
    return str(code)


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
        categorical = sampling.get(col, {}).get("method") == "nearest"
        if categorical and finite.any():
            # Discrete colour per category code (e.g. BedMachine mask).
            from matplotlib.colors import BoundaryNorm, ListedColormap
            uniq = np.unique(v[finite].astype(int))
            base = plt.get_cmap("tab10")
            cmap = ListedColormap([base(i % base.N) for i in range(len(uniq))])
            edges = np.concatenate(
                [[uniq[0] - 0.5], (uniq[:-1] + uniq[1:]) / 2.0, [uniq[-1] + 0.5]])
            sc = ax.scatter(x[finite], y[finite], c=v[finite].astype(int), s=4,
                            cmap=cmap, norm=BoundaryNorm(edges, cmap.N))
            cbar = fig.colorbar(sc, ax=ax, shrink=0.7, label=col, ticks=uniq)
            cbar.ax.set_yticklabels([_category_label(col, int(u)) for u in uniq])
        else:
            # Continuous field: colour limits span the full min..max of the data.
            vmin = float(v[finite].min()) if finite.any() else None
            vmax = float(v[finite].max()) if finite.any() else None
            sc = ax.scatter(x[finite], y[finite], c=v[finite], s=4, cmap="viridis",
                            vmin=vmin, vmax=vmax)
            fig.colorbar(sc, ax=ax, shrink=0.7, label=col)
        # Show NaN points (out of coverage) in light grey for context.
        if (~finite).any():
            ax.scatter(x[~finite], y[~finite], s=3, color="lightgrey",
                       label=f"NaN ({(~finite).sum()})")
            ax.legend(loc="upper right", fontsize=8)
        ax.set_aspect("equal")
        ax.set_title(f"{col}\n{run_id} · sampled in {sampled_crs} · n={finite.sum()}/{len(v)}")
        ax.set_xlabel(f"x (m, {display_crs})")
        ax.set_ylabel("y (m)")
        fig.tight_layout()

        path = out_dir / f"{col}.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(str(path))
        logger.info("Wrote %s", path)

    return written
