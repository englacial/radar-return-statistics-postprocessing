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
# Outputs use fixed, human-readable names (outputs/{store}/{store}.parquet etc.);
# the content-derived run_id is embedded in the parquet/CSV/plot files rather than
# the filenames, so every rule can target a static path directly.

STORE = config.get("store", "ase")
CONFIG_PATH = config.get("config_path", f"config/{STORE}.yaml")
OUT_DIR = config.get("out_dir", "outputs")


rule all:
    input:
        f"{OUT_DIR}/{STORE}/{STORE}.parquet",
        f"{OUT_DIR}/{STORE}/{STORE}.csv",
        f"{OUT_DIR}/{STORE}/plots",


rule run:
    output:
        parquet=f"{OUT_DIR}/{{store}}/{{store}}.parquet",
        manifest=f"{OUT_DIR}/{{store}}/{{store}}.manifest.json",
    run:
        from radar_postproc.runner import run_pipeline

        run_pipeline(CONFIG_PATH, out_dir=OUT_DIR)


rule csv:
    # Flat CSV alongside the geoparquet (geometry dropped; run_id in a comment).
    input:
        parquet=f"{OUT_DIR}/{{store}}/{{store}}.parquet",
    output:
        csv=f"{OUT_DIR}/{{store}}/{{store}}.csv",
    run:
        from radar_postproc.output import parquet_to_csv

        parquet_to_csv(input.parquet, output.csv)


rule plots:
    # Sanity-check map plots of each interpolated variable (one PNG per column).
    input:
        parquet=f"{OUT_DIR}/{{store}}/{{store}}.parquet",
    output:
        plotdir=directory(f"{OUT_DIR}/{{store}}/plots"),
    run:
        from radar_postproc.plots import plot_variables

        plot_variables(input.parquet, out_dir=output.plotdir)
