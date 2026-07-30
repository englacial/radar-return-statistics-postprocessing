"""Run identity and manifest construction.

run_id = sha256(snapshot_id + config_hash + sorted(dataset_hashes))[:12]
Same inputs -> same run_id -> safe dedup.
"""

import hashlib
import subprocess
from datetime import datetime, timezone

from . import __version__
from .config import config_hash


def git_info(repo_dir: str = ".") -> dict:
    """Return {sha, dirty} for the working tree, or nulls if not a git repo."""
    def _run(args):
        return subprocess.run(
            ["git", "-C", repo_dir, *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    try:
        sha = _run(["rev-parse", "HEAD"])
        dirty = bool(_run(["status", "--porcelain"]))
        return {"sha": sha, "dirty": dirty}
    except Exception:
        return {"sha": None, "dirty": None}


def compute_run_id(snapshot_id: str, cfg_hash: str, dataset_hashes: list[str]) -> str:
    payload = snapshot_id + cfg_hash + "".join(sorted(dataset_hashes))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def compute_stage_run_id(input_ids: list[str], cfg_hash: str) -> str:
    """run_id for a downstream stage: chain upstream run_ids / content hashes + config.

    Same shape as compute_run_id, with upstream identities in place of the
    icechunk snapshot: sha256(sorted(input_ids) + cfg_hash)[:12].
    """
    payload = "".join(sorted(input_ids)) + cfg_hash
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def build_stage_manifest(
    stage: str,
    config: dict,
    section_hash: str,
    input_ids: list[str],
    inputs: dict,
    repo_dir: str = ".",
    **extra,
) -> dict:
    """Manifest for a downstream stage (grid/split/train).

    `inputs` records *what* was consumed (run_ids, dataset infos); `input_ids`
    are the identity strings actually hashed into the run_id. `section_hash`
    should hash only the config sections that affect this stage.
    """
    run_id = compute_stage_run_id(input_ids, section_hash)
    return {
        "run_id": run_id,
        "stage": stage,
        "tool_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git": git_info(repo_dir),
        "config_hash": section_hash,
        "config": config,
        "inputs": inputs,
        **extra,
    }


def build_manifest(
    config: dict,
    snapshot_id: str,
    dataset_infos: list[dict],
    sampling_info: dict,
    repo_dir: str = ".",
) -> dict:
    """Assemble the manifest dict and the derived run_id."""
    cfg_hash = config_hash(config)
    dataset_hashes = [d["sha256"] for d in dataset_infos if d.get("sha256")]
    run_id = compute_run_id(snapshot_id, cfg_hash, dataset_hashes)

    store = config.get("store", {})
    manifest = {
        "run_id": run_id,
        "tool_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "icechunk": {
            "bucket": store.get("s3_bucket"),
            "prefix": store.get("s3_prefix"),
            "snapshot_id": snapshot_id,
            "branch": config.get("icechunk", {}).get("branch"),
        },
        "git": git_info(repo_dir),
        "config_hash": cfg_hash,
        "config": config,
        "datasets": dataset_infos,
        "sampling": sampling_info,
    }
    return manifest
