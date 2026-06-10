"""Sanity check: BedMachine bed elevation vs the radar's own bed_elevation.

The two are referenced differently (BedMachine bed is relative to the geoid;
radar bed_elevation is WGS84 ellipsoidal), so a constant offset of tens of
metres is expected. We check the *correlation* and report the median offset, not
strict equality.

    uv run python scripts/check_bed_sanity.py outputs/ase/<run_id>.parquet
"""

import sys

import geopandas as gpd
import numpy as np

path = sys.argv[1]
gdf = gpd.read_parquet(path)

a = gdf["bedmachine_bed_m"].to_numpy()
b = gdf["bed_elevation"].to_numpy()
mask = np.isfinite(a) & np.isfinite(b)
a, b = a[mask], b[mask]

r = np.corrcoef(a, b)[0, 1]
diff = a - b
print(f"n traces (finite both):   {mask.sum()} / {len(gdf)}")
print(f"pearson r:                {r:.4f}")
print(f"median (bedmachine-radar):{np.median(diff):.1f} m")
print(f"IQR of diff:              {np.percentile(diff,75)-np.percentile(diff,25):.1f} m")
print(f"bedmachine range:         [{a.min():.0f}, {a.max():.0f}] m")
print(f"radar range:              [{b.min():.0f}, {b.max():.0f}] m")
print("PASS" if r > 0.9 else "WEAK CORRELATION — investigate")
