"""ITS_LIVE surface speed from the v2 static velocity mosaics (AWS Open Data, no auth).

The v2 static mosaics ship per-variable Cloud-Optimized GeoTIFFs per RGI region:
  antarctic -> RGI19A, EPSG:3031
  greenland -> RGI05A, EPSG:3413
We sample the speed band (`_v`, m/yr) with windowed reads straight off S3 over
HTTPS — a multi-GB COG never lands fully in memory and nothing is downloaded.
Set ``download: true`` in config to cache the COG locally instead.
"""

import logging
import urllib.request
from pathlib import Path

import numpy as np

from ..sampling import sample_cog
from . import register

logger = logging.getLogger(__name__)

_REGION = {
    "antarctic": {"rgi": "RGI19A", "crs": "EPSG:3031"},
    "greenland": {"rgi": "RGI05A", "crs": "EPSG:3413"},
}
_BASE = "https://its-live-data.s3.amazonaws.com/velocity_mosaic/v2/static/cog"


@register
class ITSLive:
    name = "itslive"

    def __init__(self, region: str, version: str = "02", variable: str = "v",
                 download: bool = False, out_column: str | None = None):
        if region not in _REGION:
            raise ValueError(f"ITS_LIVE region must be one of {list(_REGION)}, got {region!r}")
        self.region = region
        self.valid_region = region
        self.version = str(version)
        self.variable = variable
        self.download = download
        self.crs = _REGION[region]["crs"]
        rgi = _REGION[region]["rgi"]
        self.url = f"{_BASE}/ITS_LIVE_velocity_120m_{rgi}_0000_v{self.version}_{variable}.tif"
        self.out_column = out_column or f"itslive_{variable}_m_yr"
        self.variables = [self.out_column]
        self.name = f"itslive_{region}_v{self.version}"
        self._etag: str | None = None

    def fetch(self, cache_dir: Path) -> str:
        # Capture the S3 ETag as a stable integrity token for provenance / run_id.
        try:
            req = urllib.request.Request(self.url, method="HEAD")
            with urllib.request.urlopen(req, timeout=30) as r:
                self._etag = r.headers.get("ETag", "").strip('"') or None
        except Exception as e:  # pragma: no cover - network hiccup
            logger.warning("ITS_LIVE HEAD failed: %s", e)

        if self.download:
            import pooch
            cache_dir = Path(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            local = pooch.retrieve(self.url, known_hash=None, path=str(cache_dir),
                                   fname=Path(self.url).name)
            return str(local)
        return self.url  # remote windowed read

    def open(self, path):
        return path  # sample() opens the COG (local or remote) directly

    def sample(self, src, lon: np.ndarray, lat: np.ndarray) -> dict[str, np.ndarray]:
        values = sample_cog(src, lon, lat, method="bilinear")
        return {self.out_column: values}

    def source_info(self, path=None) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "source_url": self.url,
            "sha256": self._etag,   # S3 ETag stands in for a content hash on remote reads
            "crs": self.crs,
        }

    def sampling_info(self) -> dict:
        return {self.out_column: {"method": "bilinear", "crs": self.crs}}
