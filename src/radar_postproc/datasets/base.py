"""Plugin interface for external gridded datasets.

A dataset plugin is a small class implementing the ExternalDataset protocol.
Duck-typed — no inheritance required, but BaseDataset gives a convenient default
sample() that delegates to sampling.sample_raster.
"""

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import numpy as np
import xarray as xr


@runtime_checkable
class ExternalDataset(Protocol):
    name: str                              # parquet/manifest identifier, e.g. "bedmachine_antarctic_v3"
    version: str                           # pinned DOI / release tag
    variables: list[str]                   # parquet column names produced
    crs: str                               # "EPSG:3031" | "EPSG:3413" | "EPSG:4326"
    valid_region: Literal["antarctic", "greenland", "global"]

    def fetch(self, cache_dir: Path) -> Path: ...
    def open(self, path: Path) -> xr.Dataset: ...
    def sample(self, ds: xr.Dataset, lon: np.ndarray, lat: np.ndarray) -> dict[str, np.ndarray]: ...

    def source_info(self) -> dict: ...     # {source_url, sha256, ...} for the manifest


REGION_CRS = {
    "antarctic": "EPSG:3031",
    "greenland": "EPSG:3413",
    "global": "EPSG:4326",
}
