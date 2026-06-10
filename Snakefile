# Top-level orchestration.
#
# Usage:
#   uv run snakemake --cores 4 --config store=ase
#
# The fetch/sample/merge stages live in src/radar_postproc and are called
# directly here, so the same code runs whether driven by Snakemake or by the
# `radar-postproc run` CLI. Per-dataset fetch caching is provided by each
# plugin's pooch/earthaccess cache under outputs/cache/.
#
# The output filename is the content-derived run_id, which isn't known until the
# inputs are hashed, so the stable Snakemake target is a small pointer JSON
# (outputs/{store}/latest.json) that records the run_id and output paths.

import json
from pathlib import Path

STORE = config.get("store", "ase")
CONFIG_PATH = config.get("config_path", f"config/{STORE}.yaml")
OUT_DIR = config.get("out_dir", "outputs")


rule all:
    input:
        f"{OUT_DIR}/{STORE}/latest.json",
        f"{OUT_DIR}/{STORE}/plots/_SUCCESS",
        f"{OUT_DIR}/{STORE}/csv/_SUCCESS",


rule run:
    output:
        pointer=f"{OUT_DIR}/{{store}}/latest.json",
    run:
        from radar_postproc.runner import run_pipeline

        result = run_pipeline(CONFIG_PATH, out_dir=OUT_DIR)
        pointer = {
            "run_id": result["manifest"]["run_id"],
            "n_traces": result["n_traces"],
            **result["paths"],
        }
        Path(output.pointer).write_text(json.dumps(pointer, indent=2))


rule plots:
    # Sanity-check map plots of each interpolated variable.
    input:
        pointer=f"{OUT_DIR}/{{store}}/latest.json",
    output:
        success=f"{OUT_DIR}/{{store}}/plots/_SUCCESS",
    run:
        from radar_postproc.plots import plot_variables

        pointer = json.loads(Path(input.pointer).read_text())
        out_dir = Path(output.success).parent
        written = plot_variables(pointer["parquet"], out_dir=out_dir)
        Path(output.success).write_text("\n".join(written) + "\n")


rule csv:
    # Flat CSV alongside the geoparquet ({run_id}.csv, geometry dropped).
    input:
        pointer=f"{OUT_DIR}/{{store}}/latest.json",
    output:
        success=f"{OUT_DIR}/{{store}}/csv/_SUCCESS",
    run:
        from radar_postproc.output import parquet_to_csv

        pointer = json.loads(Path(input.pointer).read_text())
        csv_path = parquet_to_csv(pointer["parquet"])
        Path(output.success).write_text(csv_path + "\n")
