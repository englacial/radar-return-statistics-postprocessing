# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow", "numpy", "openpyxl"]
# ///
"""Per-segment ADC saturation level for the surface channel, in product dB units.

The CSARP loader converts counts to volts with quantization_to_V =
Vpp_scale*2^bit_shifts/(2^adc_bits * presums) and divides by adc_gains; the
pulse-compression reference is normalized as an amplitude estimator. An ADC
sample clips at +-Vpp_scale/2 volts regardless of presums/bit_shifts, so the
maximum representable surface amplitude in product units is (Vpp_scale/2)/
adc_gains(wf_img1) exactly -- a bookkeeping constant, independent of hardware
gain accuracy. In power dB (same units as surface_power_dB):

    S_dB = 20*log10(Vpp_scale/2) - adc_gains_dB(wf1)

Deep clipping can overshoot this by up to ~+5 dB (clipped-chirp fundamental
4/pi, windowed-estimator gain). Derivation + validation:
claude_notes/20260831-saturation-level-derivation.md.

Outputs: outputs/multi_altitude_crossovers/saturation_levels.csv
Usage: uv run scripts/multi_altitude_crossovers/saturation_levels.py
(param sheets must be cached by img_comb_windows.py first).
"""

import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path("outputs/multi_altitude_crossovers")
PARAMS_DIR = OUT_DIR / "cache_img_comb" / "param_sheets"
CSV_OUT = OUT_DIR / "saturation_levels.csv"


def parse_gain_expr(s):
    """First-adc adc_gains_dB from the spreadsheet cell (handles legacy linear form)."""
    if s is None:
        return None
    t = str(s).strip().rstrip(";").strip()
    while t.startswith("(") and t.endswith(")"):
        t = t[1:-1].strip()
    pats = [
        (r"^10\.?\^\(\(([-\d.]+)\s*-\s*([-\d.]+)\s*\*\s*ones[^)]*\)\s*/\s*20\)", lambda m: float(m[1]) - float(m[2])),
        (r"^10\.?\^\(\(([-\d.]+)\s*-\s*\[\s*([-\d.]+)[^\]]*\]\s*\)\s*/\s*20\)", lambda m: float(m[1]) - float(m[2])),
        (r"^([-\d.]+)\s*-\s*([-\d.]+)\s*\*\s*ones", lambda m: float(m[1]) - float(m[2])),
        (r"^([-\d.]+)\s*-\s*\[\s*([-\d.]+)", lambda m: float(m[1]) - float(m[2])),
        (r"^([-\d.]+)\s*\*\s*ones", lambda m: float(m[1])),
        (r"^([-\d.]+)\s*$", lambda m: float(m[1])),
        (r"^\[?\s*([-\d.]+)", lambda m: float(m[1])),
    ]
    for pat, fn in pats:
        m = re.match(pat, t)
        if m:
            return fn(m)
    return None


def load_radar_sheet(season):
    import openpyxl

    wb = openpyxl.load_workbook(PARAMS_DIR / f"rds_param_{season}.xlsx", read_only=True)
    rows = list(wb["radar"].iter_rows(values_only=True))
    hdr, typ = rows[0], rows[1]

    def col(name, wf=None):
        for i in range(len(hdr)):
            if hdr[i] == name and (wf is None or (typ[i] and f"wfs({wf})" in str(typ[i]))):
                return i
        return None

    c_vpp, c_g1 = col("Vpp_scale"), col("adc_gains_dB", 1)
    out = {}
    for r in rows[2:]:
        if r[0] is None or r[1] is None:
            continue
        seg = f"{int(r[0]):08d}_{int(r[1]):02d}"
        out[seg] = {"vpp": r[c_vpp] if c_vpp is not None else None,
                    "adc_gains_dB_wf1": parse_gain_expr(r[c_g1]) if c_g1 is not None else None}
    return out


def main():
    win = pd.read_csv(OUT_DIR / "img_comb_windows.csv")
    win["seg"] = win.segment.str.replace("Data_", "")

    # observed ceilings from the full stores, restricted to safe-window traces
    frames = []
    for store in ["antarctica", "greenland"]:
        frames.append(pd.read_parquet(
            f"outputs/{store}/{store}.parquet",
            columns=["collection", "frame_id", "surface_twtt", "surface_power_dB"]))
    df = pd.concat(frames)
    df["seg"] = df.frame_id.astype(str).str.extract(r"Data_(\d{8}_\d{2})")[0]
    df = df[df.surface_power_dB.notna() & df.surface_twtt.notna()]
    df = df.merge(win[["season", "seg", "surf_valid_min_safe_s", "surf_valid_max_safe_s"]],
                  left_on=["collection", "seg"], right_on=["season", "seg"])
    df = df[(df.surface_twtt >= df.surf_valid_min_safe_s)
            & (df.surface_twtt <= df.surf_valid_max_safe_s)]
    obs = df.groupby(["season", "seg"]).surface_power_dB.agg(
        n_valid="size", obs_p995=lambda s: s.quantile(0.995)).reset_index()

    sheets = {}
    rows = []
    for season, seg in win[["season", "seg"]].itertuples(index=False):
        if season not in sheets:
            sheets[season] = load_radar_sheet(season)
        rec = sheets[season].get(seg, {})
        vpp = rec.get("vpp")
        g1 = rec.get("adc_gains_dB_wf1")
        notes = []
        if vpp is None:
            vpp = 2.0
            notes.append("Vpp_scale missing; assumed 2")
        if g1 is None:
            s_db = float("nan")
            notes.append("adc_gains_dB(wf1) missing from radar worksheet")
        else:
            s_db = 20 * math.log10(vpp / 2) - g1
        o = obs[(obs.season == season) & (obs.seg == seg)]
        n_valid = int(o.n_valid.iloc[0]) if len(o) else 0
        p995 = float(o.obs_p995.iloc[0]) if len(o) else float("nan")
        headroom = s_db - p995 if np.isfinite(s_db) and np.isfinite(p995) else float("nan")
        if not np.isfinite(s_db):
            conf = "unknown"
        elif not np.isfinite(headroom) or n_valid < 100:
            conf = "no-data"
        elif headroom < 3:
            conf = "validated-at-ceiling"
        elif headroom < 6:
            conf = "near-ceiling"
        else:
            conf = "inactive-bound"
        rows.append({
            "season": season, "segment": f"Data_{seg}",
            "S_dB": round(s_db, 2) if np.isfinite(s_db) else s_db,
            "vpp_scale": vpp, "adc_gains_dB_wf1": g1,
            "term_fullscale_dB": round(20 * math.log10(vpp / 2), 2),
            "obs_p995_dB": round(p995, 2) if np.isfinite(p995) else p995,
            "headroom_dB": round(headroom, 2) if np.isfinite(headroom) else headroom,
            "n_valid_traces": n_valid,
            "confidence": conf,
            "notes": "; ".join(notes),
        })
    out = pd.DataFrame(rows)
    out.to_csv(CSV_OUT, index=False)
    print(f"wrote {CSV_OUT} ({len(out)} rows)")
    print(out.confidence.value_counts())
    print(out[out.confidence == "validated-at-ceiling"][
        ["season", "segment", "S_dB", "obs_p995_dB", "headroom_dB"]].to_string())


if __name__ == "__main__":
    main()
