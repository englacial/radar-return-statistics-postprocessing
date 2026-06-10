import hashlib
import json
from pathlib import Path

import yaml


def load_config(config_path: str | Path) -> dict:
    """Load and return a postprocessing config from YAML, applying defaults.

    Same plain-dict + setdefault style as the upstream radar_return_statistics
    project (no pydantic / typer). The config selects an input icechunk store
    pinned at a snapshot, plus the list of external datasets to join.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    config.setdefault("store", {})
    config.setdefault("icechunk", {})
    config.setdefault("extract", {})
    config.setdefault("datasets", [])
    config.setdefault("output", {})

    # Input icechunk store (read-only). Mirrors radar_return_statistics store config.
    config["store"].setdefault("backend", "s3")
    config["store"].setdefault("s3_region", "us-west-2")

    # Pin to an immutable snapshot. branch is informational only — reads always
    # go through snapshot_id so a re-run months later is byte-reproducible.
    config["icechunk"].setdefault("branch", "main")
    config["icechunk"].setdefault("snapshot_id", None)  # required at run time

    # Point extraction options.
    config["extract"].setdefault("qc_only", True)        # keep only qc_pass traces
    config["extract"].setdefault("max_traces", None)     # cap for smoke runs
    # Radar columns carried through to the output (for sanity checks / context).
    config["extract"].setdefault(
        "carry_columns",
        [
            "frame_id",
            "slow_time",
            "latitude",
            "longitude",
            "elevation",
            "surface_elevation",
            "bed_elevation",
            "surface_power_dB",
            "bed_power_dB",
            "qc_pass",
        ],
    )

    config["output"].setdefault("dir", "outputs")

    # Normalize each dataset entry to a dict with at least a "name".
    norm = []
    for d in config["datasets"]:
        if isinstance(d, str):
            d = {"name": d}
        norm.append(d)
    config["datasets"] = norm

    return config


def config_hash(config: dict) -> str:
    """sha256 over canonical json.dumps(config, sort_keys=True)."""
    canonical = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
