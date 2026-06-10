"""ITS_LIVE surface speed + formal error, from the v2 static velocity mosaics
(AWS Open Data, no auth).

The v2 static mosaics ship per-variable Cloud-Optimized GeoTIFFs per RGI region:
  antarctic -> RGI19A, EPSG:3031
  greenland -> RGI05A, EPSG:3413
We sample the speed band (`_v`, m/yr) and its formal error band (`_v_error`,
m/yr) with windowed reads straight off S3 over HTTPS — multi-GB COGs never land
fully in memory and nothing is downloaded. Set ``download: true`` to cache the
COGs locally instead.
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

# source band -> output column
_BANDS = {"v": "itslive_v_m_yr", "v_error": "itslive_v_error_m_yr"}


@register
class ITSLive:
    name = "itslive"

    def __init__(self, region: str, version: str = "02", download: bool = False):
        if region not in _REGION:
            raise ValueError(f"ITS_LIVE region must be one of {list(_REGION)}, got {region!r}")
        self.region = region
        self.valid_region = region
        self.version = str(version)
        self.download = download
        self.crs = _REGION[region]["crs"]
        rgi = _REGION[region]["rgi"]
        self.urls = {
            band: f"{_BASE}/ITS_LIVE_velocity_120m_{rgi}_0000_v{self.version}_{band}.tif"
            for band in _BANDS
        }
        self.url = self.urls["v"]  # primary, for provenance
        self.variables = list(_BANDS.values())
        self.name = f"itslive_{region}_v{self.version}"
        self._etag: str | None = None
        self._sources: dict[str, str] = {}

    def fetch(self, cache_dir: Path):
        # Capture the speed COG's S3 ETag as a stable integrity token for provenance.
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
            self._sources = {
                band: str(pooch.retrieve(url, known_hash=None, path=str(cache_dir),
                                         fname=Path(url).name))
                for band, url in self.urls.items()
            }
        else:
            self._sources = dict(self.urls)  # remote windowed reads
        return self._sources

    def open(self, path):
        return path  # sample() opens each COG (local or remote) directly

    def sample(self, src, lon: np.ndarray, lat: np.ndarray) -> dict[str, np.ndarray]:
        sources = src or self._sources
        return {
            col: sample_cog(sources[band], lon, lat, method="bilinear")
            for band, col in _BANDS.items()
        }

    def source_info(self, path=None) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "source_url": self.url,
            "error_url": self.urls["v_error"],
            "sha256": self._etag,   # S3 ETag stands in for a content hash on remote reads
            "crs": self.crs,
        }

    def sampling_info(self) -> dict:
        return {col: {"method": "bilinear", "crs": self.crs} for col in self.variables}
