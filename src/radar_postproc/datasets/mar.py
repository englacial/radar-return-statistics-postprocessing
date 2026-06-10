"""MAR regional-climate SMB + 2 m air-temperature long-term mean.

Greenland first (MARv3.x, HTTP/pooch). Antarctic MAR outputs are available at
https://zenodo.org/records/4459259 and can be wired in the same way later.
Implemented in its own step.
"""

from pathlib import Path

import numpy as np
import xarray as xr

from . import register


@register
class MAR:
    name = "mar"

    def __init__(self, region: str = "greenland", **kwargs):
        if region != "greenland":
            raise ValueError(
                "MAR currently supports region='greenland'. Antarctic MAR "
                "(https://zenodo.org/records/4459259) is a follow-up."
            )
        self.region = region
        self.valid_region = region
        self.crs = "EPSG:3413"
        self.version = kwargs.get("version", "3.12")
        self.period = tuple(kwargs.get("period", [1980, 2020]))
        self.variables = ["smb_mean_mm_we_yr", "t2m_mean_K"]
        self.name = f"mar_{region}_v{self.version}"

    def fetch(self, cache_dir: Path) -> Path:  # pragma: no cover - implemented in next step
        raise NotImplementedError("MAR plugin not yet implemented")

    def open(self, path: Path) -> xr.Dataset:  # pragma: no cover
        raise NotImplementedError

    def sample(self, ds, lon, lat) -> dict[str, np.ndarray]:  # pragma: no cover
        raise NotImplementedError

    def source_info(self, path: Path | None = None) -> dict:
        return {"name": self.name, "version": self.version, "period": list(self.period), "crs": self.crs}

    def sampling_info(self) -> dict:
        return {v: {"method": "bilinear", "crs": self.crs} for v in self.variables}
