"""MEaSUREs Phase-Based Antarctica Ice Velocity Map (NSIDC-0754, v1) — surface speed.

Mouginot, Rignot & Scheuchl (2019), 450 m, EPSG:3031, a 1996-2018 multi-sensor
composite. Chosen over ITS_LIVE for Antarctica because it is essentially gap-free
over the polar hole: 99.5% of the 5 km model grid is covered (95.8% even in the
89-90S band) versus ITS_LIVE's 87.8% / 0.0%. Where both are valid the two agree
closely (r = 0.963 on log10 speed, median ratio -1.0%), so the swap mostly *adds*
interior points rather than moving existing ones.

Speed and its error are derived from the component fields:
  v     = hypot(VX, VY)
  v_err = hypot(VX*ERRX, VY*ERRY) / v   (error propagated through the magnitude)

Single ~7 GB netCDF from NSIDC via earthaccess (Earthdata auth), cached once.
"""

import hashlib
import logging
from pathlib import Path

import earthaccess
import numpy as np
import xarray as xr

from ..sampling import sample_raster
from . import register

logger = logging.getLogger(__name__)

_SHORT_NAME = "NSIDC-0754"
_DOI = "10.5067/PZ3NJ5RXRH10"

# Generic column names: the velocity covariate is sourced per ice sheet
# (MEaSUREs for Antarctica, ITS_LIVE for Greenland), and the model needs one
# column. The manifest's per-sheet source_info records which product produced it.
_V_COL = "surface_v_m_yr"
_V_ERR_COL = "surface_v_error_m_yr"


@register
class MeasuresPhaseVelocity:
    name = "measures_vel"

    def __init__(self, region: str = "antarctic", version: str = "1"):
        if region != "antarctic":
            raise ValueError(f"{_SHORT_NAME} is Antarctica-only, got region={region!r}")
        self.region = region
        self.valid_region = region
        self.version = str(version)
        self.crs = "EPSG:3031"
        self.variables = [_V_COL, _V_ERR_COL]
        self.name = f"measures_phase_vel_antarctic_v{self.version}"
        self._source_url: str | None = None

    def fetch(self, cache_dir: Path) -> Path:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        earthaccess.login()  # environment -> netrc -> interactive
        results = earthaccess.search_data(short_name=_SHORT_NAME, version=self.version)
        if not results:
            raise RuntimeError(f"No granules for {_SHORT_NAME} v{self.version}")
        granule = next(g for g in results if g.data_links()[0].endswith(".nc"))
        self._source_url = granule.data_links()[0]
        path = Path(earthaccess.download(granule, local_path=str(cache_dir))[0])
        logger.info("MEaSUREs phase-based velocity -> %s", path.name)
        return path

    def open(self, path: Path) -> xr.Dataset:
        return xr.open_dataset(path, engine="netcdf4")

    def sample(self, ds: xr.Dataset, lon: np.ndarray, lat: np.ndarray) -> dict[str, np.ndarray]:
        vx = sample_raster(ds["VX"], lon, lat, self.crs, method="bilinear")
        vy = sample_raster(ds["VY"], lon, lat, self.crs, method="bilinear")
        ex = sample_raster(ds["ERRX"], lon, lat, self.crs, method="bilinear")
        ey = sample_raster(ds["ERRY"], lon, lat, self.crs, method="bilinear")
        speed = np.hypot(vx, vy)
        with np.errstate(invalid="ignore", divide="ignore"):
            err = np.hypot(vx * ex, vy * ey) / speed
        err = np.where(speed > 0, err, np.hypot(ex, ey))  # direction undefined at rest
        return {_V_COL: speed, _V_ERR_COL: err}

    def source_info(self, path: Path | None = None) -> dict:
        info = {
            "name": self.name,
            "version": self.version,
            "short_name": _SHORT_NAME,
            "doi": _DOI,
            "source_url": self._source_url,
            "crs": self.crs,
            "reference": "Mouginot, Rignot & Scheuchl (2019), doi:10.1029/2019GL083826",
        }
        if path is not None and Path(path).exists():
            info["sha256"] = _sha256(Path(path))
        return info

    def sampling_info(self) -> dict:
        return {col: {"method": "bilinear", "crs": self.crs} for col in self.variables}


def _sha256(path: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
