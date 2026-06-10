"""BedMachine bed/surface/thickness/mask via NSIDC (Earthdata auth through earthaccess).

One plugin, parametrized by region and a list of variables:
  region="antarctic" -> BedMachine Antarctica (NSIDC-0756, v4), EPSG:3031, single netCDF
  region="greenland" -> BedMachine Greenland  (IDBMG4, v6),     EPSG:3413, netCDF (+ per-variable bed GeoTIFF)

Continuous fields (bed/surface/thickness, metres) are sampled bilinearly; the
categorical `mask` (ocean/ice-free-land/grounded-ice/floating-ice/...) is sampled
nearest. Bed elevation is the headline sanity check against the radar bed_elevation.
"""

import hashlib
import logging
from pathlib import Path

import earthaccess
import numpy as np
import rioxarray  # noqa: F401  (registers .rio accessor)
import xarray as xr

from ..sampling import sample_raster
from . import register

logger = logging.getLogger(__name__)

_SPEC = {
    "antarctic": {"short_name": "NSIDC-0756", "crs": "EPSG:3031"},
    "greenland": {"short_name": "IDBMG4", "crs": "EPSG:3413"},
}

# Categorical variables -> nearest; everything else continuous -> bilinear.
_CATEGORICAL = {"mask", "source"}


@register
class BedMachine:
    name = "bedmachine"  # registry key

    def __init__(self, region: str, version: str,
                 variables: list[str] | None = None, variable: str | None = None,
                 out_columns: dict | None = None):
        if region not in _SPEC:
            raise ValueError(f"BedMachine region must be one of {list(_SPEC)}, got {region!r}")
        self.region = region
        self.version = str(version)
        # Accept a list of variables; fall back to the legacy single `variable`.
        if variables is None:
            variables = [variable] if variable else ["bed"]
        self.src_variables = list(variables)
        self.crs = _SPEC[region]["crs"]
        self.short_name = _SPEC[region]["short_name"]
        self.valid_region = region
        out_columns = out_columns or {}
        self.out_map = {v: out_columns.get(v, self._default_column(v)) for v in self.src_variables}
        self.variables = list(self.out_map.values())  # output parquet column names
        # Instance-level name shadows the class registry key for the manifest.
        self.name = f"bedmachine_{region}_v{self.version}"
        self._source_url: str | None = None

    @staticmethod
    def _default_column(var: str) -> str:
        return f"bedmachine_{var}" if var in _CATEGORICAL else f"bedmachine_{var}_m"

    @staticmethod
    def _method(var: str) -> str:
        return "nearest" if var in _CATEGORICAL else "bilinear"

    def _pick_granule(self, results):
        """Use a per-variable GeoTIFF only for a single bed request; else the netCDF."""
        def fname(g):
            return g.data_links()[0].rsplit("/", 1)[-1]

        if len(self.src_variables) == 1:
            var = self.src_variables[0]
            tif = [g for g in results if fname(g).endswith(".tif") and f"_{var}-" in fname(g)]
            if tif:
                return tif[0]
        nc = [g for g in results if fname(g).endswith(".nc")]
        if nc:
            return nc[0]
        return results[0]

    def fetch(self, cache_dir: Path) -> Path:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        earthaccess.login()  # environment -> netrc -> interactive
        results = earthaccess.search_data(short_name=self.short_name, version=self.version)
        if not results:
            raise RuntimeError(f"No granules for {self.short_name} v{self.version}")
        granule = self._pick_granule(results)
        try:
            self._source_url = granule.data_links()[0]
        except Exception:
            self._source_url = None
        paths = earthaccess.download(granule, local_path=str(cache_dir))
        path = Path(paths[0])
        logger.info("BedMachine %s -> %s", self.name, path.name)
        return path

    def open(self, path: Path) -> xr.Dataset:
        path = Path(path)
        if path.suffix == ".tif":
            # Single-variable per-variable GeoTIFF.
            da = rioxarray.open_rasterio(path, masked=True).squeeze("band", drop=True)
            return da.rename(self.src_variables[0]).to_dataset()
        ds = xr.open_dataset(path, engine="netcdf4")
        return ds.rio.write_crs(self.crs, inplace=False)

    def sample(self, ds: xr.Dataset, lon: np.ndarray, lat: np.ndarray) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for var in self.src_variables:
            if var not in ds:
                raise KeyError(f"{var!r} not in BedMachine {self.region} dataset; have {list(ds.data_vars)}")
            out[self.out_map[var]] = sample_raster(ds[var], lon, lat, self.crs, method=self._method(var))
            logger.info("BedMachine sampled %s -> %s (%s)", var, self.out_map[var], self._method(var))
        return out

    def source_info(self, path: Path | None = None) -> dict:
        info = {
            "name": self.name,
            "version": self.version,
            "short_name": self.short_name,
            "variables": self.src_variables,
            "source_url": self._source_url,
            "crs": self.crs,
        }
        if path is not None and Path(path).exists():
            info["sha256"] = _sha256(Path(path))
        return info

    def sampling_info(self) -> dict:
        return {self.out_map[v]: {"method": self._method(v), "crs": self.crs} for v in self.src_variables}


def _sha256(path: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
