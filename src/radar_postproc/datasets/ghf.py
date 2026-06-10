"""Geothermal heat flow (GHF) with lower/upper uncertainty bounds.

Community-recommended, re-gridded (NON topographically corrected) GHF fields from
Fahrner et al. (2025) / Lösing et al. (2026), Zenodo 10.5281/zenodo.17745730:
  antarctic -> Lösing & Ebbing (2021), EPSG:3031
  greenland -> Colgan et al. (2022),  EPSG:3413  (without NGRIP by default)

We use the regridded version (not the topographically corrected one) because only
it ships uncertainty layers. Both models express uncertainty the same way — a
min/max envelope — so the output is uniform across ice sheets:
  HF -> ghf_mW_m2 (central), HFmin -> ghf_lower_mW_m2, HFmax -> ghf_upper_mW_m2
all mW/m^2 on a 500 m polar-stereographic grid, sampled bilinearly.

Downloads GHF_Regridded.zip (~1.5 GB) once, extracts the region's NetCDF, caches
the extracted file so later runs skip the zip entirely.
"""

import hashlib
import logging
import shutil
import zipfile
from pathlib import Path

import numpy as np
import xarray as xr

from ..sampling import sample_raster
from . import register

logger = logging.getLogger(__name__)

_ZIP_URL = "https://zenodo.org/records/17745730/files/GHF_Regridded.zip?download=1"
_ZIP_MD5 = "b59a35da08cff55c1e47c83b19061b7f"
_DOI = "10.5281/zenodo.17745730"

_REGION = {
    "antarctic": {"crs": "EPSG:3031", "model": "Lösing & Ebbing (2021)"},
    "greenland": {"crs": "EPSG:3413", "model": "Colgan et al. (2022)"},
}

# HF=central, HFmin=lower bound, HFmax=upper bound in both models. The regridded
# NetCDFs store these in W/m^2 (per their `unit` attr); we convert to mW/m^2.
_VARMAP = {"HF": "ghf_mW_m2", "HFmin": "ghf_lower_mW_m2", "HFmax": "ghf_upper_mW_m2"}
_W_TO_MW = 1000.0


@register
class GHF:
    name = "ghf"

    def __init__(self, region: str, ngrip: bool = False, resolution: str = "500m", **kwargs):
        if region not in _REGION:
            raise ValueError(f"ghf region must be one of {list(_REGION)}, got {region!r}")
        self.region = region
        self.valid_region = region
        self.ngrip = bool(ngrip) and region == "greenland"
        self.resolution = resolution
        self.crs = _REGION[region]["crs"]
        self.model = _REGION[region]["model"]
        self.variables = list(_VARMAP.values())
        self.version = "regridded"  # non-topographically-corrected
        suffix = "_wNGRIP" if self.ngrip else ""
        self.name = f"ghf_{region}{'_wNGRIP' if self.ngrip else ''}_regridded"
        self._member = self._member_name(region, suffix, resolution)
        self._nc_path: Path | None = None

    @staticmethod
    def _member_name(region: str, suffix: str, resolution: str) -> str:
        if region == "antarctic":
            # Lösing & Ebbing is provided at 500 m only.
            return "GHF_Regridded/Loesing&Ebbing(2021)_GHF_resampled_500m.nc"
        return f"GHF_Regridded/Colgan&Wansing(2021)_GHF{suffix}_resampled_{resolution}.nc"

    def fetch(self, cache_dir: Path) -> Path:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        nc_path = cache_dir / Path(self._member).name
        if not nc_path.exists():
            import pooch
            zip_path = pooch.retrieve(_ZIP_URL, known_hash=f"md5:{_ZIP_MD5}",
                                      path=str(cache_dir), fname="GHF_Regridded.zip")
            logger.info("GHF: extracting %s", self._member)
            with zipfile.ZipFile(zip_path) as z, z.open(self._member) as src, open(nc_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
        self._nc_path = nc_path
        return nc_path

    def open(self, path: Path) -> xr.Dataset:
        return xr.open_dataset(path)

    def sample(self, ds: xr.Dataset, lon: np.ndarray, lat: np.ndarray) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for src_var, col in _VARMAP.items():
            out[col] = sample_raster(ds[src_var], lon, lat, self.crs, method="bilinear") * _W_TO_MW
        return out

    def source_info(self, path=None) -> dict:
        info = {
            "name": self.name,
            "version": self.version,
            "model": self.model,
            "region": self.region,
            "ngrip": self.ngrip,
            "topographically_corrected": False,
            "doi": _DOI,
            "source_url": _ZIP_URL.split("?")[0],
            "member": self._member,
            "crs": self.crs,
        }
        if self._nc_path is not None and self._nc_path.exists():
            info["sha256"] = _sha256(self._nc_path)
        return info

    def sampling_info(self) -> dict:
        return {col: {"method": "bilinear", "crs": self.crs} for col in self.variables}


def _sha256(path: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
