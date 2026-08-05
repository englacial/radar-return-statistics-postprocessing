"""Explanatory figure showing RSSNR versus actual surface SNR by altitude

Produces (in outputs/explanation/):
  rssnr_altitude.png - Explanatory plot showing how RSSNR approximates surface SNR at
                        high altitudes.

Outputs are fixed. Just intended to illustrate the concept. Borrowed from:
https://github.com/thomasteisberg/hale_uas_ipr_link_budgets/blob/main/UAV%20IPR%20Link%20Budget.ipynb

Usage: uv run python scripts/rssnr_geometric_spreading.py
"""

import argparse
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="outputs")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    output_dir = out_dir / "explanation"
    output_dir.mkdir(parents=True, exist_ok=True)

    epsilon_r = 3.17 # relative permittivity of ice -- we usually assume this to be constant when the ice is thick enough that the firn is only a small percentage of the total thickness
    n_ice = np.sqrt(epsilon_r) # refractive index of ice

    ice_thicknesses = [1e3, 3e3, 6e3]
    platform_altitudes = np.geomspace(20, 500e3, 1000)

    fig, ax = plt.subplots(1, 1, figsize=(4, 6))

    for idx in range(len(ice_thicknesses)):

        G_s = 10*np.log10(1 / (4 * np.pi * platform_altitudes)**2)
        G_b = 10*np.log10(1 / (4 * np.pi * (platform_altitudes + ice_thicknesses[idx]/n_ice))**2)

        ax.semilogy(G_s - G_b, platform_altitudes, label=f'Ice Thickness = {ice_thicknesses[idx]/1e3} km')

    ax.set_xlabel('Difference between actual minimum\nSNR at surface for 0 dB SNR at bed\nand "RSSNR" figure [dB]')
    ax.grid()
    ax.set_ylabel('Platform Altitude [m]')
    ax.legend(loc='upper right')
    fig.tight_layout()

    fig.savefig(output_dir / 'rssnr_altitude.png')


if __name__ == "__main__":
    main()