"""Maps of where the multi-altitude crossover sites are located.

One map per ice sheet: all radar traces in light gray, crossover sites from
the (within-season) model table colored by their number of separated
altitude levels.

Usage: uv run python scripts/multi_altitude_crossovers/sites_map.py
Outputs: outputs/multi_altitude_crossovers/sites_map_{antarctica,greenland}.png
"""

import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import SHEET_COLOR, INK, style_axis  # noqa: E402

OUT = Path("outputs/multi_altitude_crossovers")
CRS = {"antarctica": "EPSG:3031", "greenland": "EPSG:3413"}


def main():
    t = pd.read_parquet(OUT / "model_table.parquet")
    sites = (t.groupby(["sheet", "site_id"])
             .agg(latitude=("latitude", "mean"), longitude=("longitude", "mean"),
                  n_levels=("n_levels", "first"))
             .reset_index())
    for sheet, crs in CRS.items():
        bg = pd.read_parquet(f"outputs/{sheet}/{sheet}.parquet",
                             columns=["latitude", "longitude"]).dropna()
        tx = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        bx, by = tx.transform(bg["longitude"].to_numpy(),
                              bg["latitude"].to_numpy())
        s = sites[sites["sheet"] == sheet]
        sx, sy = tx.transform(s["longitude"].to_numpy(),
                              s["latitude"].to_numpy())
        fig, ax = plt.subplots(figsize=(7.5, 6.5))
        ax.scatter(np.asarray(bx) / 1e3, np.asarray(by) / 1e3, s=1, c="0.88",
                   rasterized=True, label=None)
        color = SHEET_COLOR[sheet]
        sx, sy = np.asarray(sx), np.asarray(sy)
        two = (s["n_levels"] == 2).to_numpy()
        n3 = int((~two).sum())
        ax.scatter(sx[two] / 1e3, sy[two] / 1e3, s=8, color=color, alpha=0.5,
                   ec="none", zorder=3, label=f"2 levels ({int(two.sum())})")
        ax.scatter(sx[~two] / 1e3, sy[~two] / 1e3, s=34, color=color,
                   ec="black", lw=0.6, zorder=4,
                   label=f"$\\geq$ 3 levels ({n3})")
        ax.set_aspect("equal")
        ax.set_xlabel("x [km]", color=INK)
        ax.set_ylabel("y [km]", color=INK)
        ax.legend(fontsize=8, loc="lower right")
        ax.set_title(f"{sheet}: multi-altitude crossover sites "
                     f"({s['site_id'].nunique()} within-season sites)\n"
                     f"pairwise $\\leq$ 1 km lateral, $\\geq$ 200 m vertical "
                     f"separation", fontsize=10, color=INK)
        style_axis(ax)
        fig.tight_layout()
        out = OUT / f"sites_map_{sheet}.png"
        fig.savefig(out, dpi=140)
        plt.close(fig)
        print(f"{sheet}: {s['site_id'].nunique()} sites -> {out}")


if __name__ == "__main__":
    main()
