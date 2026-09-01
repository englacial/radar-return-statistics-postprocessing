# Example frames with image-combining or surface-saturation artifacts

Companion to claude_notes/20260831-dc8-gain-investigation.md and the
img_comb window screen (reference/OPR_Toolbox_Guide.md, "img_comb fields").
Frames below were mined from the joined parquets
(`outputs/{antarctica,greenland}/*.parquet`) so that each artifact is visible
WITHIN a single frame — same flight, same settings, aircraft climbing or
descending through the boundary — minimizing geology confounds. Useful as
echogram spot-check targets and as regression cases for the QC screen.

## 1. Image-combining (gate-crossing) frames

The surface pick crosses image 1's record gate mid-frame; the combine falls
back to the absolute splice at T_end - T_guard, and beyond it "surface power"
is image 2's saturated surface response. Selection: >= 4 traces on each side
of the season's cliff; drop = median(below) - median(above).

| Frame | Season | Boundary | P below -> above [dB] | Drop [dB] | AGL [m] |
|---|---|---|---|---|---|
| Data_20161105_05_038 | 2016_Antarctica_DC8 | 8 us | -30.3 -> -61.8 | 31.5 | 436-2295 |
| Data_20141115_07_005 | 2014_Antarctica_DC8 | 8 us | -38.5 -> -68.1 | 29.5 | 468-4062 |
| Data_20161103_06_040 | 2016_Antarctica_DC8 | 8 us | -31.5 -> -60.9 | 29.4 | 415-2138 |
| Data_20161031_06_019 | 2016_Antarctica_DC8 | 8 us | -37.9 -> -66.3 | 28.4 | 366-2265 |
| Data_20161114_04_003 | 2016_Antarctica_DC8 | 8 us | -29.4 -> -57.6 | 28.2 | 591-1932 |
| Data_20181020_01_025 | 2018_Antarctica_DC8 | 8 us | -28.8 -> -53.5 | 24.7 | 453-2812 |
| Data_20140501_01_046 | 2014_Greenland_P3 | 5.5 us | -37.3 -> -62.8 | 25.4 | 470-1626 |
| Data_20140412_03_001 | 2014_Greenland_P3 | 5.5 us | -41.1 -> -65.6 | 24.5 | 425-1076 |
| Data_20140421_01_026 | 2014_Greenland_P3 | 5.5 us | -43.3 -> -69.5 | 26.2 | 435-1892 |

The tell: above the boundary the power is not just lower but FLAT — in-frame
std 1.7-4.6 dB across 2-4 km of altitude change. In the echogram, the surface
return crosses the img1 -> img2 splice and abruptly becomes the clipped img2
response. The P-3 frames put the boundary near 5.5 us vs the DC-8's 8 us —
direct evidence the valid windows are per-config, not global.

## 2. Surface saturation / blanking frames

Median surface power RISES with altitude at short range — unphysical for a
real echo; the signature of the surface arriving inside the receiver blanking
window (T_blank) or clipping image 1 itself. Selection: >= 5 traces both
below 2.5 us and in 2.5-6 us TWTT; rise = median(2.5-6 us) - median(< 2.5 us)
> 5 dB.

| Frame | Season | P (<375 m) -> P (375-900 m) [dB] | Rise [dB] | AGL [m] |
|---|---|---|---|---|
| Data_20221212_01_017 | 2022_Antarctica_BaslerMKB | -65.1 -> -43.7 | +21.4 | 129-660 |
| Data_20221212_01_018 | 2022_Antarctica_BaslerMKB | -63.9 -> -45.3 | +18.6 | 120-887 |
| Data_20140419_03_010 | 2014_Greenland_P3 | -55.9 -> -37.5 | +18.4 | 120-455 |
| Data_20130420_02_011 | 2013_Greenland_P3 | -83.2 -> -54.8 | +28.4 | 260-715 |
| Data_20130404_02_015 | 2013_Greenland_P3 | -78.7 -> -60.4 | +18.3 | 330-565 |
| Data_20171205_03_015 | 2017_Antarctica_Basler | -55.2 -> -40.5 | +14.7 | 260-535 |
| Data_20121101_04_026 | 2012_Antarctica_DC8 | -52.9 -> -36.5 | +16.4 | 316-600 |

2013_Greenland_P3 is the worst offender by frame count (11 qualifying
frames). In the echogram, expect the surface return suppressed or clipped
inside the first ~2-2.5 us, recovering as the aircraft climbs.

## 3. Negative result: suspected high-config gates look weak in-frame

The pooled gate_scan.py jumps at ~58 us (2012 DC-8) and ~49 us (2018 DC-8)
do NOT reproduce within single frames: the best straddling candidates show
only 1.7-2.6 dB steps —

| Frame | Season | Boundary tested | Step [dB] | AGL [m] |
|---|---|---|---|---|
| Data_20121015_02_009 | 2012_Antarctica_DC8 | 58 us | 2.1 | 7369-8963 |
| Data_20181112_01_037 | 2018_Antarctica_DC8 | 49 us | 2.6 | 7216-7453 |

— so those pooled jumps were probably config/geography mixing, not record
gates. The param-derived img_comb windows
(outputs/multi_altitude_crossovers/img_comb_windows.csv) are the authority.
