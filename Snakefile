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
OUT_DIR = config.get("out_dir", "outputs")

# Cross-store modeling pipeline (grid -> split -> train -> benchmark), driven by
# a single config that names its input stores and models. Invoked without store=:
#   uv run snakemake --cores 4 model_all
MODEL_CONFIG_PATH = config.get("model_config_path", "config/model.yaml")
import os as _os
from pathlib import Path as _Path
from radar_postproc.config import config_hash, load_config, load_model_config

_model_cfg = load_model_config(MODEL_CONFIG_PATH)
MODEL_STORES = sorted({s for stores in _model_cfg["inputs"].values() for s in stores})
MODELS = [m["name"] for m in _model_cfg["train"]["models"]]


def config_stamp(name: str, section: dict) -> str:
    """Path of a stamp file that changes exactly when `section` changes.

    Each stage hashes only its own config section into its run_id, so each
    rule depends on a stamp of that same section rather than on the whole
    config file: editing train settings does not rebuild the grid, and a
    comment-only edit rebuilds nothing. Written at parse time, only when the
    hash differs (so mtime moves only on a real change); a stamp created for
    the first time gets an epoch mtime, so migrating existing outputs does
    not trigger a rebuild.
    """
    path = _Path(OUT_DIR) / "config_stamps" / f"{name}.hash"
    digest = config_hash(section)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(digest)
        _os.utime(path, (0, 0))
    elif path.read_text() != digest:
        path.write_text(digest)
    return str(path)


def augment_stamp(store: str) -> str:
    """Stamp of a store's full augment config (what its run_id hashes)."""
    path = config.get("config_path", f"config/{store}.yaml")
    return config_stamp(f"augment_{store}", load_config(path))


# Section hashes below mirror grid.py / split.py / train.py exactly.
_tcfg = _model_cfg["train"]
STAMP_GRID = config_stamp("grid", {"inputs": _model_cfg["inputs"], "grid": _model_cfg["grid"]})
STAMP_SPLIT = config_stamp("split", {"inputs": _model_cfg["inputs"], "split": _model_cfg["split"]})
STAMP_TRAIN = {m["name"]: config_stamp(f"train_{m['name']}",
                                       {"train": {**_tcfg, "models": [m]}, "model": m["name"]})
               for m in _tcfg["models"]}

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
    # Depends on a stamp of the store's config, so re-pinning
    # icechunk.snapshot_id (or any other augment change) re-runs the
    # extraction without --forcerun.
    input:
        stamp=lambda w: augment_stamp(w.store),
    output:
        parquet=f"{OUT_DIR}/{{store}}/{{store}}.parquet",
        manifest=f"{OUT_DIR}/{{store}}/{{store}}.manifest.json",
    run:
        from radar_postproc.runner import run_pipeline

        # Derive the config from the wildcard (not the module-level CONFIG_PATH)
        # so cross-store DAGs (e.g. model_all) build the right store.
        run_pipeline(config.get("config_path", f"config/{wildcards.store}.yaml"),
                     out_dir=OUT_DIR)


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
    # only re-runs when config/model.yaml's inputs/grid sections change.
    input:
        STAMP_GRID,
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
        STAMP_SPLIT,
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
        lambda w: STAMP_TRAIN[w.model],
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
