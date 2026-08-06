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

# Cross-store modeling pipeline (grid -> split -> train -> benchmark), driven by
# a single config that names its input stores and models. Invoked without store=:
#   uv run snakemake --cores 4 model_all
MODEL_CONFIG_PATH = config.get("model_config_path", "config/model.yaml")
import yaml as _yaml
with open(MODEL_CONFIG_PATH) as _f:
    _model_cfg = _yaml.safe_load(_f) or {}
MODEL_STORES = sorted({s for stores in _model_cfg.get(
    "inputs", {"antarctic": ["ase", "utig"], "greenland": ["greenland"]}).values() for s in stores})
MODELS = [m["name"] if isinstance(m, dict) else m
          for m in _model_cfg.get("train", {}).get("models", [{"name": "linear"}])]

# Which trained model the mission design tool ships. Lives in its own config
# section so it never enters a stage's section hash (and so never perturbs a
# run_id); override at the command line with --config mission_tool_model=linear.
TOOL_DIR = "mission_design_tool"
TOOL_MODEL = config.get("mission_tool_model",
                        _model_cfg.get("mission_tool", {}).get("model", MODELS[-1]))


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

        # Derive the config from the wildcard (not the module-level CONFIG_PATH)
        # so cross-store DAGs (e.g. model_all) build the right store.
        run_pipeline(
            config.get("config_path", f"config/{wildcards.store}.yaml"),
            out_dir=OUT_DIR,
        )


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


# --- Cross-store modeling pipeline -------------------------------------------


rule model_all:
    input:
        expand(f"{OUT_DIR}/model/{{model}}/metrics.json", model=MODELS),
        f"{OUT_DIR}/model/benchmark.csv",
        f"{TOOL_DIR}/dist/mission_design_tool.html",


rule grid:
    # Full-ice-sheet covariate grid (the prediction domain). Network + slow;
    # only re-runs when config/model.yaml's grid section changes.
    output:
        parquet=f"{OUT_DIR}/model/grid.parquet",
        manifest=f"{OUT_DIR}/model/grid.manifest.json",
    run:
        from radar_postproc.grid import run_grid

        run_grid(MODEL_CONFIG_PATH, out_dir=OUT_DIR)


rule split:
    # Attach the radar target to grid points, assign blocking cells + folds,
    # emit helper maps for hand-picking test cells.
    input:
        grid=f"{OUT_DIR}/model/grid.parquet",
        stores=expand(f"{OUT_DIR}/{{s}}/{{s}}.parquet", s=MODEL_STORES),
    output:
        parquet=f"{OUT_DIR}/model/split.parquet",
        cells=f"{OUT_DIR}/model/cells.csv",
        maps=directory(f"{OUT_DIR}/model/cell_maps"),
    run:
        from radar_postproc.split import run_split

        run_split(MODEL_CONFIG_PATH, out_dir=OUT_DIR)


rule train:
    input:
        f"{OUT_DIR}/model/split.parquet",
    output:
        metrics=f"{OUT_DIR}/model/{{model}}/metrics.json",
        posterior=f"{OUT_DIR}/model/{{model}}/posterior.nc",
        zarr=directory(f"{OUT_DIR}/model/{{model}}/predictions.zarr"),
        manifest=f"{OUT_DIR}/model/{{model}}/manifest.json",
    run:
        from radar_postproc.train import run_train

        run_train(MODEL_CONFIG_PATH, model_name=wildcards.model, out_dir=OUT_DIR)


rule benchmark:
    input:
        expand(f"{OUT_DIR}/model/{{model}}/metrics.json", model=MODELS),
    output:
        csv=f"{OUT_DIR}/model/benchmark.csv",
        md=f"{OUT_DIR}/model/benchmark.md",
    run:
        from radar_postproc.train import write_benchmark

        write_benchmark(list(input), output.csv, output.md)


# --- Mission design tool ------------------------------------------------------
# Keeps the web tool's payload in step with the model it ships: retraining
# regenerates it, so the deployed page can never quietly serve a stale run_id.
#   uv run snakemake --cores 4 mission_tool


rule mission_tool:
    input:
        f"{TOOL_DIR}/dist/mission_design_tool.html",


rule mission_tool_data:
    # Packed prediction layers + BedMachine outlines for the browser.
    input:
        zarr=f"{OUT_DIR}/model/{TOOL_MODEL}/predictions.zarr",
        manifest=f"{OUT_DIR}/model/{TOOL_MODEL}/manifest.json",
        metrics=f"{OUT_DIR}/model/{TOOL_MODEL}/metrics.json",
        grid=f"{OUT_DIR}/model/grid.parquet",
        script=f"{TOOL_DIR}/build_data.py",
    output:
        meta=f"{TOOL_DIR}/data/meta.json",
        antarctic=f"{TOOL_DIR}/data/antarctic.bin.gz",
        greenland=f"{TOOL_DIR}/data/greenland.bin.gz",
        outlines=f"{TOOL_DIR}/data/coast.json.gz",
    params:
        model=TOOL_MODEL,
    shell:
        "python {input.script} --model {params.model}"


rule mission_tool_standalone:
    # Single-file build with the data inlined, for use without a web server.
    input:
        data=rules.mission_tool_data.output,
        sources=[f"{TOOL_DIR}/{f}" for f in
                 ("index.html", "style.css", "icons.js", "presets.js", "sidelobes.js",
                  "auto.js", "physics.js", "warnings.js", "app.js")],
        script=f"{TOOL_DIR}/build_standalone.py",
    output:
        html=f"{TOOL_DIR}/dist/mission_design_tool.html",
    shell:
        "python {input.script}"
