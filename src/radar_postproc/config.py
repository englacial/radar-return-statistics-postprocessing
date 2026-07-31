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
    # Drop traces with radar-derived ice thickness below this (metres); None = keep all.
    config["extract"].setdefault("min_thickness_m", None)
    # Also keep attempted-but-unpicked bed traces (non-detections) where the store
    # provides the pick flags (reprocessed stores only).
    config["extract"].setdefault("include_nondetections", False)
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
            "surface_twtt",
            "required_surface_snr_dB",
            "pre_surface_noise_dB",
            "post_bed_noise_dB",
            # Reprocessed-store columns (warn-skipped where absent): pick-free
            # at-depth noise window stats + bed-pick availability flags.
            "post_bed_noise_interp_dB",
            "post_bed_peak_interp_dB",
            "bed_pick_available",
            "bed_pick_attempted",
            "qc_surface_pass",
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


def load_model_config(config_path: str | Path) -> dict:
    """Load the cross-store model config (grid/split/train stages), applying defaults.

    Separate from the per-store augment configs so modeling parameters never
    perturb the augment run_ids. Same plain-dict + setdefault style.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Which augment stores feed each ice sheet (merged per sheet, native CRS).
    config.setdefault("inputs", {"antarctic": ["ase", "utig"], "greenland": ["greenland"]})

    # Full-ice-sheet covariate grid.
    config.setdefault("grid", {})
    grid = config["grid"]
    grid.setdefault("resolution_m", 5000)      # actual = native_res * round(target/native)
    grid.setdefault("mask_values", [2, 3, 4])  # BedMachine: grounded, floating, Lake Vostok
    grid.setdefault("datasets", [])
    grid["datasets"] = [{"name": d} if isinstance(d, str) else d for d in grid["datasets"]]

    # Spatially-blocked test/fold split.
    config.setdefault("split", {})
    split = config["split"]
    split.setdefault("target", "required_surface_snr_dB")
    split.setdefault("nn_cutoff_m", 1000)
    split.setdefault("cell_size_km", 500)
    split.setdefault("n_folds", 5)
    split.setdefault("seed", 42)
    split.setdefault("test_cells", [])  # e.g. ["ant:-3:1"]; empty -> warning, no test set

    # Bayesian model training / prediction.
    config.setdefault("train", {})
    train = config["train"]
    train.setdefault("seed", 42)
    train.setdefault("draws", 1000)
    train.setdefault("tune", 1000)
    train.setdefault("chains", 4)
    train.setdefault("cv_chains", 2)   # cheaper CV fits; final fit uses `chains`
    train.setdefault("predict_batch_size", 100_000)
    # No training or prediction where BedMachine thickness is below this (metres).
    train.setdefault("min_thickness_m", None)
    # SNR saturation: obs whose bed pick sits within margin_threshold_dB of the
    # post-bed noise floor are treated as right-censored (lower bounds) via a
    # Tobit likelihood instead of exact values.
    train.setdefault("censoring", {})
    train["censoring"].setdefault("enabled", False)
    train["censoring"].setdefault("margin_threshold_dB", 10.0)
    train.setdefault("models", [{"name": "linear"}])
    train["models"] = [{"name": m} if isinstance(m, str) else m for m in train["models"]]

    config.setdefault("output", {})
    config["output"].setdefault("dir", "outputs")

    return config


def config_hash(config: dict) -> str:
    """sha256 over canonical json.dumps(config, sort_keys=True)."""
    canonical = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
