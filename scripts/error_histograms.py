"""Histograms of each input dataset's error field, for both ice sheets.

Reads the error fields directly from the source grids (full grid for the cached
NetCDFs; a decimated overview read for the remote ITS_LIVE COG) and writes one
PNG per dataset to outputs/error_histograms/.

  uv run python scripts/error_histograms.py
"""

import logging
from math import ceil
from pathlib import Path

import matplotlib
import numpy as np
import rasterio
import xarray as xr
from rasterio.enums import Resampling

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("error_histograms")

CACHE = Path("outputs/cache")
OUT = Path("outputs/error_histograms")
OUT.mkdir(parents=True, exist_ok=True)
_ITS = "https://its-live-data.s3.amazonaws.com/velocity_mosaic/v2/static/cog"


def _stats_label(v):
    return (f"n={v.size:,}\nmin={v.min():.0f}\nmedian={np.median(v):.0f}\n"
            f"99th pct={np.percentile(v, 99):.0f}\nmax={v.max():.0f}")


def _finite(a):
    a = np.asarray(a, dtype="float64").ravel()
    return a[np.isfinite(a)]


def _source_legend(src_da):
    """{code: name} from the source variable's flag_values/flag_meanings attrs."""
    fv = np.atleast_1d(src_da.attrs.get("flag_values", []))
    fm = str(src_da.attrs.get("flag_meanings", "")).split()
    return {int(v): (fm[i] if i < len(fm) else f"code{int(v)}") for i, v in enumerate(fv)}


def bedmachine_errbed():
    """errbed split by `source`, one figure per ice sheet (+ an All-sources panel)."""
    regions = [
        ("Antarctica (NSIDC-0756 v4)", "antarctica",
         CACHE / "NSIDC-0756_BedMachineAntarctica_19700101-20191001_V04.1.nc", False),
        ("Greenland (IDBMG4 v6)", "greenland",
         CACHE / "BedMachineGreenland-v6.nc", True),
    ]
    for label, slug, f, group_bathy in regions:
        log.info("BedMachine errbed by source: reading %s", label)
        ds = xr.open_dataset(f, engine="netcdf4")
        e = ds["errbed"].values            # float32 full grid
        src = ds["source"].values
        legend = _source_legend(ds["source"])

        def gname(code):
            if group_bathy and code >= 10:
                return "bathymetry (10-53)"
            return legend.get(code, f"code{code}")

        groups: dict[str, list] = {}
        for code in np.unique(src):
            vals = e[src == code]
            vals = vals[np.isfinite(vals)]
            if vals.size:
                groups.setdefault(gname(int(code)), []).append(vals)
        groups = {k: np.concatenate(v) for k, v in groups.items()}

        # "All sources" first, then per-source by descending cell count.
        all_e = _finite(e)
        panels = [("All sources", all_e)] + sorted(groups.items(), key=lambda kv: -kv[1].size)

        log.info("=== %s : errbed (m) by source ===", label)
        for name, vals in panels:
            log.info("  %-22s n=%11d  min=%6.0f  median=%6.0f  max=%6.0f",
                     name, vals.size, vals.min(), np.median(vals), vals.max())

        ncol = 3
        nrow = ceil(len(panels) / ncol)
        fig, axes = plt.subplots(nrow, ncol, figsize=(4.4 * ncol, 3.2 * nrow), squeeze=False)
        for ax, (name, vals) in zip(axes.ravel(), panels):
            ax.hist(vals, bins=np.arange(0, 1001, 10), color="#3b6", edgecolor="none")
            ax.set_yscale("log")
            ax.set_title(name, fontsize=10)
            ax.set_xlabel("bed error (m)")
            ax.text(0.97, 0.95,
                    f"n={vals.size:,}\nmin={vals.min():.0f}\nmed={np.median(vals):.0f}\nmax={vals.max():.0f}",
                    transform=ax.transAxes, ha="right", va="top", fontsize=8, family="monospace")
        for ax in axes.ravel()[len(panels):]:
            ax.axis("off")
        fig.suptitle(f"BedMachine errbed by data source — {label}", fontsize=13)
        fig.tight_layout()
        _save(fig, f"bedmachine_errbed_{slug}.png")


def surface_v_error():
    """Speed-error field per sheet — the two products behind `surface_v_error_m_yr`.

    Antarctica: MEaSUREs phase-based (NSIDC-0754), ERRX/ERRY propagated through the
    speed magnitude exactly as datasets/measures_vel.py does. Greenland: the ITS_LIVE
    v2 `v_error` COG, read decimated over the network.
    """
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    log.info("MEaSUREs NSIDC-0754: reading (decimated) Antarctica")
    ds = xr.open_dataset(CACHE / "antarctic_ice_vel_phase_map_v01.nc")
    sl = slice(None, None, max(1, ds.sizes["x"] // 2000))
    vx, vy = ds["VX"][sl, sl].values, ds["VY"][sl, sl].values
    ex, ey = ds["ERRX"][sl, sl].values, ds["ERRY"][sl, sl].values
    ds.close()
    speed = np.hypot(vx, vy)
    with np.errstate(invalid="ignore", divide="ignore"):
        err = np.hypot(vx * ex, vy * ey) / speed
    err = np.where(speed > 0, err, np.hypot(ex, ey))
    panels = [(axes[0], "MEaSUREs NSIDC-0754 — Antarctica", _finite(err))]

    env = rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                       CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")
    url = f"{_ITS}/ITS_LIVE_velocity_120m_RGI05A_0000_v02_v_error.tif"
    log.info("ITS_LIVE v_error: reading (decimated) Greenland")
    with env, rasterio.open(url) as src:
        factor = max(1, max(src.width, src.height) // 2000)
        arr = src.read(1, out_shape=(src.height // factor, src.width // factor),
                       resampling=Resampling.nearest, masked=True)
    panels.append((axes[1], "ITS_LIVE v2 — Greenland",
                   _finite(arr.astype("float64").filled(np.nan))))

    for ax, label, v in panels:
        hi = np.percentile(v, 99.5)
        ax.hist(v[v <= hi], bins=120, color="#36b", edgecolor="none")
        ax.set_yscale("log")
        ax.set_title(f"{label}\n(x clipped at 99.5th pct; decimated grid)")
        ax.set_xlabel("speed error (m/yr)")
        ax.set_ylabel("grid cells (log)")
        ax.text(0.97, 0.95, _stats_label(v), transform=ax.transAxes, ha="right",
                va="top", fontsize=9, family="monospace")
    fig.tight_layout()
    _save(fig, "surface_v_error.png")


def ghf_uncertainty():
    files = {
        "Antarctica — Lösing & Ebbing (2021)": CACHE / "Loesing&Ebbing(2021)_GHF_resampled_500m.nc",
        "Greenland — Colgan et al. (2022)": CACHE / "Colgan&Wansing(2021)_GHF_resampled_500m.nc",
    }
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, (label, f) in zip(axes, files.items()):
        log.info("GHF envelope: reading %s", label)
        ds = xr.open_dataset(f)
        band = _finite((ds["HFmax"].values - ds["HFmin"].values) * 1000.0)  # W/m^2 -> mW/m^2
        ax.hist(band, bins=80, color="#b63", edgecolor="none")
        ax.set_yscale("log")
        ax.set_title(f"GHF uncertainty (upper−lower) — {label}")
        ax.set_xlabel("envelope width (mW/m²)")
        ax.set_ylabel("grid cells (log)")
        ax.text(0.97, 0.95, _stats_label(band), transform=ax.transAxes, ha="right",
                va="top", fontsize=9, family="monospace")
    fig.tight_layout()
    _save(fig, "ghf_uncertainty.png")


def _save(fig, name):
    path = OUT / name
    fig.savefig(path, dpi=120)
    plt.close(fig)
    log.info("wrote %s", path)


if __name__ == "__main__":
    bedmachine_errbed()
    ghf_uncertainty()
    surface_v_error()
