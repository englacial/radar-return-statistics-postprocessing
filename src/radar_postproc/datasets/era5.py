"""ERA5 multi-year mean 2 m air temperature (WeatherBench2 precomputed climatology).

ERA5 is global, so this one plugin serves all three stores (both Antarctic and
Greenland) with no per-store logic.

Source: the WeatherBench2 ERA5 **hourly climatology** on public GCS, read
anonymously (no account), matching the repo's "auth: none" pattern.
  gs://weatherbench2/datasets/era5-hourly-climatology/1990-2019_6h_1440x721.zarr
This is a *precomputed* (hour, dayofyear) climatology at full 0.25 deg over
1990-2019. We chose it over raw hourly ARCO-ERA5 because that store is chunked
whole-globe-per-hour, so a true multi-year mean would pull hundreds of GB; the
climatology yields the same long-term mean for one ~6 GB read, then we cache the
tiny global mean field. The averaging window is therefore fixed by the product
(1990-2019, ~the WMO 1991-2020 normal); pick the 1990-2017 product via
``climatology=`` if needed.

Long-term mean = unweighted mean over (hour, dayofyear) — every 6-hour slot is
equal length, so no day-length weighting is needed (cf. monthly-means, which
would need days-in-month weights).

Output column: era5_t2m_mean_K (Kelvin, ERA5 native — not converted).
"""

import hashlib
import logging
from pathlib import Path

import numpy as np
import xarray as xr

from ..sampling import sample_raster
from . import register

logger = logging.getLogger(__name__)

_CLIMATOLOGY = {
    "1990-2019": "gs://weatherbench2/datasets/era5-hourly-climatology/1990-2019_6h_1440x721.zarr",
    "1990-2017": "gs://weatherbench2/datasets/era5-hourly-climatology/1990-2017_6h_1440x721.zarr",
}


def wrap_antimeridian(da: xr.DataArray) -> xr.DataArray:
    """Append an x = +180 column copied from x = -180. Idempotent.

    The ERA5 grid ends at +179.75, so `xr.interp` returns NaN for any point east
    of that — a one-cell-wide NaN stripe along the antimeridian, which in
    EPSG:3031 runs straight down from the pole through the Ross Ice Shelf. The
    field is periodic in longitude, so closing it is exact, not an extrapolation.
    """
    if float(da["x"].max()) >= 180.0:
        return da
    wrap = da.isel(x=0).assign_coords(x=180.0)
    return xr.concat([da, wrap], dim="x")


def to_xy_field(da: xr.DataArray) -> xr.DataArray:
    """Normalize an ERA5 (longitude 0..360, descending latitude) field for sampling.

    Converts longitude to -180..180, sorts both axes ascending, renames the dims
    to x/y so sampling.sample_raster (EPSG:4326) can interpolate at trace lon/lat
    directly, and closes the longitude seam. Pure / network-free so it can be
    unit-tested.
    """
    out = da.assign_coords(longitude=(((da.longitude + 180) % 360) - 180))
    out = out.sortby("longitude").sortby("latitude")
    return wrap_antimeridian(out.rename({"longitude": "x", "latitude": "y"}))


@register
class ERA5:
    name = "era5"
    crs = "EPSG:4326"
    valid_region = "global"

    def __init__(self, climatology: str = "1990-2019", variable: str = "2m_temperature",
                 out_column: str = "era5_t2m_mean_K", **kwargs):
        if climatology not in _CLIMATOLOGY:
            raise ValueError(f"era5 climatology must be one of {list(_CLIMATOLOGY)}, got {climatology!r}")
        self.climatology = climatology
        self.url = _CLIMATOLOGY[climatology]
        self.variable = variable
        self.out_column = out_column
        self.version = climatology  # the averaging window lives in provenance here
        self.variables = [self.out_column]
        self.name = f"era5_t2m_{climatology}"
        self._cache_path: Path | None = None
        if kwargs:
            logger.info("era5: ignoring unused kwargs %s (window is fixed by the climatology product)",
                        list(kwargs))

    def fetch(self, cache_dir: Path) -> Path:
        # Real work happens in sample(); nothing to download up front.
        return Path(cache_dir)

    def open(self, path: Path) -> Path:
        return Path(path)  # passthrough; sample() builds/loads the mean field

    def _build_or_load(self, cache_dir: Path) -> xr.DataArray:
        """Global long-term-mean t2m field, cached as a tiny NetCDF (~4 MB)."""
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path = cache_dir / f"era5_t2m_mean_{self.climatology}.nc"
        if not self._cache_path.exists():
            logger.info("era5: computing %s mean field from %s", self.climatology, self.url)
            ds = xr.open_zarr(self.url, storage_options={"token": "anon"}, chunks={},
                              decode_timedelta=True)
            # Unweighted mean over the climatology's two time dims (dask-streamed).
            mean = ds[self.variable].mean(["hour", "dayofyear"]).compute()
            mean = to_xy_field(mean)
            mean.name = self.out_column
            mean.to_netcdf(self._cache_path)
            logger.info("era5: cached mean field -> %s", self._cache_path)
        # wrap_antimeridian on the load path too: caches written before the seam
        # fix lack the +180 column, and rebuilding one means a ~6 GB GCS read.
        return wrap_antimeridian(xr.open_dataarray(self._cache_path))

    def sample(self, src, lon: np.ndarray, lat: np.ndarray) -> dict[str, np.ndarray]:
        da = self._build_or_load(src)
        values = sample_raster(da, lon, lat, self.crs, method="bilinear")
        return {self.out_column: values}

    def source_info(self, path=None) -> dict:
        info = {
            "name": self.name,
            "version": self.version,
            "climatology": self.climatology,
            "source_url": self.url,
            "crs": self.crs,
        }
        if self._cache_path is not None and self._cache_path.exists():
            info["sha256"] = _sha256(self._cache_path)
        return info

    def sampling_info(self) -> dict:
        return {self.out_column: {"method": "bilinear", "crs": self.crs}}


def _sha256(path: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
