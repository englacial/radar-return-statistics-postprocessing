# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "earthaccess",
#     "xarray",
#     "netCDF4",
#     "numpy",
#     "matplotlib",
# ]
# ///
"""Histogram BedMachine bed-error (`errbed`) in mass-conservation cells.

Standalone — run with uv (no project install needed):

    uv run --script scripts/bedmachine_mc_errbed_hist.py

Fetches BedMachine Antarctica (NSIDC-0756 v4) and Greenland (IDBMG4 v6) through
earthaccess, keeps only grid cells whose `source` is "mass_conservation", and
writes both histograms to a single PNG.

Requires Earthdata credentials (earthaccess uses EARTHDATA_USERNAME /
EARTHDATA_PASSWORD env vars or ~/.netrc). Downloads are cached in CACHE_DIR and
reused on later runs; point CACHE_DIR at an existing cache to skip downloading,
or leave it and the files (Antarctica ~1.1 GB, Greenland ~2.8 GB) are fetched on
first run.
"""

from pathlib import Path

import earthaccess
import matplotlib
import numpy as np
import xarray as xr

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# --- configuration -----------------------------------------------------------
CACHE_DIR = Path("outputs/cache")  # reused if it already holds the netCDFs
OUTPUT_PNG = Path("outputs/bedmachine_mc_errbed_hist.png")
DATASETS = [
    ("Antarctica", "NSIDC-0756", "4"),
    ("Greenland", "IDBMG4", "6"),
]
# -----------------------------------------------------------------------------


def fetch_netcdf(short_name: str, version: str, cache_dir: Path) -> Path:
    """Download (or reuse from cache) the BedMachine netCDF granule."""
    results = earthaccess.search_data(short_name=short_name, version=version)
    if not results:
        # Earthdata/CMR only publishes the latest BedMachine version; older ones
        # are retired from the cloud catalog. Report what IS available.
        cols = earthaccess.search_datasets(short_name=short_name)
        avail = sorted({c["umm"].get("Version") for c in cols})
        raise RuntimeError(
            f"No {short_name} granules for version {version!r}. Earthdata/CMR only "
            f"serves version(s) {avail} for {short_name}; older versions are not "
            f"available through earthaccess (try the NSIDC archive directly)."
        )
    # Greenland also ships a per-variable GeoTIFF; we need the full netCDF.
    granule = next((g for g in results if g.data_links()[0].lower().endswith(".nc")), results[0])
    paths = earthaccess.download(granule, local_path=str(cache_dir))
    return Path(paths[0])


def mass_conservation_errbed(path: Path) -> np.ndarray:
    """errbed (m) at cells where source == mass_conservation, finite only."""
    ds = xr.open_dataset(path, engine="netcdf4")
    src = ds["source"]
    meanings = str(src.attrs.get("flag_meanings", "")).split()
    values = list(np.atleast_1d(src.attrs.get("flag_values", [])))
    code = values[meanings.index("mass_conservation")]  # robust to per-region code schemes
    e = ds["errbed"].values[src.values == code].astype("float64")
    return e[np.isfinite(e)]


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    earthaccess.login()

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, (label, short_name, version) in zip(axes, DATASETS):
        print(f"{label}: fetching {short_name} v{version} ...")
        v = mass_conservation_errbed(fetch_netcdf(short_name, version, CACHE_DIR))
        print(f"{label}: {v.size:,} mass-conservation cells "
              f"(min={v.min():.0f}, median={np.median(v):.0f}, max={v.max():.0f})")
        ax.hist(v, bins=np.arange(0, v.max() + 11, 10), color="#3b6", edgecolor="none")
        ax.set_yscale("log")
        ax.set_title(f"BedMachine {label}\nerrbed where source = mass conservation")
        ax.set_xlabel("bed error (m)")
        ax.set_ylabel("grid cells (log)")
        ax.text(0.97, 0.95,
                f"n={v.size:,}\nmin={v.min():.0f}\nmedian={np.median(v):.0f}\nmax={v.max():.0f}",
                transform=ax.transAxes, ha="right", va="top", fontsize=9, family="monospace")

    fig.tight_layout()
    fig.savefig(OUTPUT_PNG, dpi=120)
    print(f"wrote {OUTPUT_PNG}")


if __name__ == "__main__":
    main()
