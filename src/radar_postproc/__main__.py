import json
import logging

import click

from .config import load_config
from .datasets import get_dataset, list_datasets
from .io_icechunk import resolve_snapshot


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def main(verbose: bool) -> None:
    """Join external gridded products onto per-trace radar metrics."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@main.command()
@click.argument("config_path", type=click.Path(exists=True))
@click.option("--out-dir", default=None, help="Override output directory")
def run(config_path: str, out_dir: str | None) -> None:
    """Run the full pipeline for a store config."""
    from .runner import run_pipeline

    result = run_pipeline(config_path, out_dir=out_dir)
    click.echo(json.dumps({"run_id": result["manifest"]["run_id"],
                           "n_traces": result["n_traces"],
                           **result["paths"]}, indent=2))


@main.command()
@click.argument("parquet_path", type=click.Path(exists=True))
@click.option("--out-dir", default=None, help="Plot output dir (default: <parquet dir>/plots)")
def plot(parquet_path: str, out_dir: str | None) -> None:
    """Write sanity-check map plots of each interpolated variable in a parquet."""
    from .plots import plot_variables

    paths = plot_variables(parquet_path, out_dir=out_dir)
    for p in paths:
        click.echo(p)


@main.command("to-csv")
@click.argument("parquet_path", type=click.Path(exists=True))
@click.option("--out", "csv_path", default=None, help="CSV path (default: <parquet>.csv)")
def to_csv(parquet_path: str, csv_path: str | None) -> None:
    """Convert a geoparquet output to a flat CSV (drops geometry; lat/lon kept)."""
    from .output import parquet_to_csv

    click.echo(parquet_to_csv(parquet_path, csv_path))


@main.command("list-datasets")
def list_datasets_cmd() -> None:
    """List registered dataset plugins."""
    for name in list_datasets():
        click.echo(name)


@main.command("validate-config")
@click.argument("config_path", type=click.Path(exists=True))
def validate_config(config_path: str) -> None:
    """Load a config, apply defaults, and instantiate its dataset plugins."""
    config = load_config(config_path)
    if not config["icechunk"]["snapshot_id"]:
        raise click.ClickException("icechunk.snapshot_id is not set")
    for entry in config["datasets"]:
        kwargs = {k: v for k, v in entry.items() if k != "name"}
        plugin = get_dataset(entry["name"], **kwargs)
        click.echo(f"ok: {entry['name']} -> {plugin.name} "
                   f"(crs={plugin.crs}, region={plugin.valid_region}, cols={plugin.variables})")
    click.echo("config valid")


@main.command("resolve-snapshot")
@click.argument("config_path", type=click.Path(exists=True))
def resolve_snapshot_cmd(config_path: str) -> None:
    """Print the latest snapshot id on the configured branch (for pinning)."""
    config = load_config(config_path)
    branch = config["icechunk"]["branch"]
    snap = resolve_snapshot(config["store"], branch=branch)
    click.echo(snap)


if __name__ == "__main__":
    main()
