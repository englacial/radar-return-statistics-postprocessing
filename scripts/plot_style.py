"""Shared plotting conventions (see AGENTS.md).

Color encodes ice sheet; linestyle encodes data source.
"""

C_ANT = "tab:blue"      # Antarctica
C_GRL = "tab:green"     # Greenland
C_OTHER = "tab:orange"  # both / neither / not sheet-specific
C_OTHER2 = "tab:purple"  # second non-sheet series alongside C_OTHER

SHEET_COLOR = {"antarctic": C_ANT, "antarctica": C_ANT, "greenland": C_GRL}

LS_OBS = "-"     # observations / training data
LS_PRED = "--"   # model predictions
LS_PPC = ":"     # posterior-predictive draws

INK = "#3a3f45"


def style_axis(ax):
    ax.grid(True, color="0.9", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=INK, labelsize=9)
