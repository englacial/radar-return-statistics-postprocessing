# 2016_Antarctica_DC8 high- vs low-altitude radiometry investigation

Question: did the DC-8 high-altitude legs (~9-11 km AGL) use different
waveform/gain settings than the low-altitude survey legs, and can the
resulting offset be corrected so both regimes fit one power-vs-range model?

## Verdict

(a) **Settings differ — definitively.** Two distinct radar configurations were
flown (documented in `create_configs_2016_Antarctica_DC8.m` and the season
param spreadsheet):

| | Low-altitude survey mode | High-altitude survey mode |
|---|---|---|
| design AGL | 0–2500 ft (0–762 m) | 20000–35000 ft (6.1–10.7 km) |
| PRF | 12000 Hz | 7500 Hz |
| waveforms | 3 (1 us / 3 us / 10 us) | 2 (3 us / 10 us) |
| wf1 (surface) rx attenuation | 33 dB (adc_gains_dB = 43-33 = 10) | 20 dB (adc_gains_dB = 43-20 = 23) |
| deeper wfs attenuation | 0 dB (43 dB gain) | 0 dB (43 dB gain) |
| presums | [3 3 25] | [3 15] |

Segment assignments confirmed from `rds_param_2016_Antarctica_DC8.xlsx`
(radar worksheet): high-altitude config on 20161026_05, 20161028_05,
20161031_07, 20161115_03, 20161115_05; low-altitude 3-wf config on
20161014_05 (4-wf variant), 20161105_05, 20161117_04; 20161111_04 used the
5-waveform ping-pong/image mode (wf1 atten 23 dB). The 2014 and 2018 DC-8
seasons show the same dual-config pattern (low: 3-wf 1 us atten 33; high:
2-wf 3 us atten 20–25).

(b) **No dB correction is needed — the posted CSARP products already
compensate the settings deterministically.** Verified three ways:

1. The exact toolbox commit that produced the posted files
   (`param_csarp.sw_version.rev = 8f0b9de1937c...`, 2017-08-18, available at
   github.com/CReSIS/cresis-toolbox) divides raw data by
   `wfs(wf).adc_gains(adc)` (load_mcords2_data.m line 393) and by presums
   (line 428), and normalizes the pulse-compression reference by its energy —
   "Normalize reference function so that it is an estimator -- Accounts for
   pulse duration differences" (mcords/load_mcords_wfs.m lines 430–436).
   The modern OPR toolbox (gitlab.com/openpolarradar/opr, data_load.m) does
   the same via `adc_gains_dB`.
2. The `param_csarp` struct embedded in the downloaded product files carries
   the correct per-segment values: high frame wf1 adc_gains = 14.125
   (= 23 dB), low frame wf1 adc_gains = 3.162 (= 10 dB), deep wfs 141.25
   (= 43 dB) — i.e. the per-segment gains were fed to the processor.
3. **Empirical crossover check.** Data_20161026_05_036 (high, R ≈ 9.46 km)
   and Data_20161117_04_003 (low) overlap for ~25 km on the 70W line
   (lat -73.34 to -73.10). Matched by latitude, the products give
   low (R = 633 m) −34.2 dB vs high (R = 9463 m) −61.6 dB: a 27.4 dB drop
   over 1.17 decades of range → implied x ≈ 2.3. Physically sensible; the
   two configs are radiometrically consistent as posted.

(c) **The actual cause of the ~1 dB "flat" anomaly: the surface leaves wf1's
staged-recording range gate on the low-altitude config above ~1.2 km AGL.**
Within the single frame Data_20161117_04_003 (constant geology, |roll| < 1.5
deg), surface peak power vs surface TWTT shows a razor-sharp ~19 dB cliff at
exactly 8.0 us (~1200 m AGL):

    TWTT 3.5–8.0 us  : median −34 to −38 dB   (valid, wf1 in gate)
    TWTT 8.0–13.0 us : median −56 to −59 dB   (flat garbage floor)

The low config's wf1 gate was designed for 0–762 m AGL (2·(H+guard)/c ≈
5.1 us + Tpd + trigger guard ≈ 8 us). Beyond it, the combined echogram's
surface region contains wf1 gate-edge/rolloff garbage (img_comb keeps img1
through surface + 1 us), so the picked "surface power" saturates at ≈ −56 dB
regardless of true power. The crossover comparison "1.9 km vs 9.3 km ≈ 1 dB"
was comparing that invalid −56 dB floor against a valid high-altitude −61 dB —
not two calibrated measurements.

Parquet confirmation (outputs/antarctica/antarctica.parquet):

    20161117_04_003: AGL 475–1947 m; below-gate median −34.7 dB, above-gate −57.1 dB
    20161111_04_003: AGL 1814–1920 m; all above gate; median −56.0 dB (invalid)
    20161105_05_008: AGL 403–485 m; −32.8 dB (valid)
    20161014_05_019: AGL 463–630 m; −42.0 dB (valid)
    high frames (60–74 us TWTT): −52 to −62 dB (valid, within high-config gate)

## Implications for the exponent fit

- **Invert the exclusion logic in the plan's Stage 2.** The high-altitude legs
  are usable as-is (no delta offset needed in principle). What must be
  excluded/QC-flagged is **low-config surface power where surface TWTT >
  ~7.7 us (AGL > ~1.15 km)** — this includes the nominal "low-altitude" passes
  at 1.2–2 km that anchored the anomaly. Valid data then spans ~0.4–1.15 km
  plus ~9–11 km: > 1 decade of range leverage.
- The same gate check applies at the top of the high config: gate end ≈
  2·(10.67 km)/c + Tpd + guard ≈ 74–76 us. Passes at 11+ km AGL
  (20161115_05_031 reaches 73.8 us; 20161031_07_002 at 72.2 us) are within
  a couple of us of it — spot-check those frames for the same cliff before
  trusting them (their values, ≈ −61 dB, currently look consistent with the
  9.3 km passes rather than obviously broken).
- A per-(season, regime) free offset delta in the model remains a cheap
  robustness term (attenuator steps are only accurate to ~1 dB, and Tsys/
  chan_equal were derived per season), but no 10-20 dB settings correction
  exists or is needed.
- Bed powers come from the final long-pulse waveform whose gate covers the
  deep ice; the 8 us cliff artifact is specific to the surface channel.
  (Low-altitude surface saturation below ~1.2 km is a separate open question —
  cf. saturation_diagnostics.py.)
- Calibration status generally: CSARP products are **relatively** calibrated
  (quantization-to-volts, presums, receiver attenuation, chan_equal, and pulse
  duration all normalized at load) but not absolutely calibrated (no system_dB
  / antenna-gain / sigma0 scaling in the 2017-era processing). Cross-segment,
  within-season power comparisons are legitimate; absolute levels are not.

## QC rule to implement in Stage 1

For CReSIS DC-8 seasons, flag surface_power_dB invalid when the surface TWTT
falls within ~1 us of (or beyond) the wf1 gate end for that segment's config:

- 2016 low/survey config (segments with 3-4 wfs, wf1 Tpd = 1 us): invalid for
  surface TWTT > ~7.7 us  (AGL > ~1.15 km)
- 2016 ping-pong config (20161111_04 etc.): same gate design → same cutoff
- 2016 high config (2 wfs, wf1 Tpd = 3 us): valid ~44–72 us; treat > ~72 us
  with caution
- 2014/2018: same structure; exact per-segment wf1 gates derivable from the
  season spreadsheets (or empirically from the per-frame cliff)

The clean empirical alternative (no gate arithmetic): within each
(segment-config, season), fit/plot surface power vs surface TWTT and drop the
flat floor population (here −56 to −59 dB) — the cliff is unmistakable.

## Sources

- Param spreadsheets: https://gitlab.com/openpolarradar/opr_params
  (`rds_param_2016_Antarctica_DC8.xlsx`, also 2014/2018) — radar worksheet,
  per-segment `Tpd` / `adc_gains_dB` columns.
- Radar configs: https://gitlab.com/openpolarradar/opr —
  `matlab/missions/create_configs_2016_Antarctica_DC8.m` ("Survey Mode"
  vs "Survey Mode High Altitude" sections),
  `matlab/missions/default_radar_params_2016_Antarctica_DC8_mcords.m`
  (rx_gain = 51.5−8.5 = 43 dB).
- Processing code at the posted products' exact commit:
  https://github.com/CReSIS/cresis-toolbox @ 8f0b9de1937c7c5c1b886728d701304b5113e811 —
  `cresis-toolbox/mcords2/load_mcords2_data.m` (adc_gains, presums division),
  `cresis-toolbox/mcords/load_mcords_wfs.m` (energy-normalized reference).
- Products inspected (posted 2017-08): 
  https://data.cresis.ku.edu/data/rds/2016_Antarctica_DC8/CSARP_standard/20161026_05/Data_20161026_05_036.mat and
  .../20161117_04/Data_20161117_04_003.mat (embedded `param_csarp` structs;
  crossover overlap comparison; 8.0 us cliff scan).
- Local wiki (cresis-wiki.wiki): Echogram_File_Guide.md ("Data ... linear
  power units"), Radiometric,_Waveform,_Spectrum.md (in-flight radiometric
  check procedure; no season-specific cal notes for 2016 DC-8).

## Validation: per-segment img_comb surface-validity windows (added later on 2026-08-31)

Follow-up deliverable: `outputs/multi_altitude_crossovers/img_comb_windows.csv`
(one row per model-table segment, 267 rows, 17 seasons), generated by
`scripts/multi_altitude_crossovers/img_comb_windows.py`. Method: per-segment
`img_comb` [T_comb, T_blank, T_guard] and image list from the **array worksheet**
of the season's `rds_param_*.xlsx` (gitlab.com/openpolarradar/opr_params), and
image 1's time gate [t_start, T_end] read directly, in product time, from the
`Time` axis of one posted `Data_img_01_*` file per segment on
data.cresis.ku.edu (byte-range reads; v7.3 via h5py, v5 via scipy). Window:
`max(T_blank, t_start) <= td_surface <= T_end - T_guard`. Single-image segments
(21 rows: 5x 2012 DC8, 9x 2013 Basler, misc.) use the posted record gate itself.
"Safe" columns additionally guard by Tpd(img1) at both ends. Coverage: all 267
segments resolved (no NaN gates); the 2018_Greenland_P3 records dir is 403 on the
data server but img_01 products are open, so it resolved anyway.

**Per-segment check (the strong test).** For every CSV segment whose store data
straddles its predicted `surf_valid_max`, median surface power in the 4 us below
vs the 4 us above the boundary (>= 30 traces each side, 23 segments testable):

- 17/23 drop by >= 6 dB across the boundary (median step -14 dB, up to -34 dB) —
  2014/2016/2018 DC8, 2014/2016/2017/2018 Greenland P3 all validate.
- 4/23 (all 2017_Antarctica_Basler) JUMP UP by +4 to +13 dB — the boundary is
  real but crossing into image 2 reads a hotter (saturated/low-atten) surface
  response instead of a floor. Same QC conclusion: out-of-window = invalid.
- 2/23 show |step| < 3 dB (20140516_01, 20171218_01): boundary not visible there.

Frame-level checks agree tightly where a sharp cliff is measurable:
2016 DC8 cliffs at 8.0/8.5 us vs predicted 7.7 us (0.5 us bin quantization),
2014 DC8 8.0 vs 7.8, 2017_Greenland_P3 7.0 vs 7.0, 2019_Greenland_P3 season
cliff 12-13 us vs predicted 12.2 us. The 2014/2018 DC8 high-altitude configs'
gate STARTS (37.4-37.5 us) also show up in gate_scan as the -38/+38 dB notch at
38-40 us (surfaces just above the img1 gate start are invalid, then valid again).

**Agreements with the coordinator's expected cliffs** (gate_scan.py, season-pooled):
2014 DC8 -10 dB at 8-9 us vs vmax 7.8 (ok); 2018 DC8 -15.7 dB at 8-9 us vs 7.9
(ok); Basler blanking: 2013 Basler +10.5 dB recovery at 1-2 us vs t_start
1.2 us / vmin ~0.2-1 us (ok), 2022 MKB +19.7 dB recovery at 2-3 us vs vmin
-2.2 us (window too permissive at the start by ~4 us — receiver recovery after
the transmit event is not in the spreadsheet; use vmin_safe or an empirical
~3 us floor for MKB).

**Disagreements / features the windows do NOT explain:**

- 2014_Greenland_P3 season-pooled cliff at 5-6 us vs per-segment vmax 6.8-7.3 us.
  Per-segment, 20140509_01 shows the drop at ~6.0 and 20140502_01 validates at
  7.3; the pooled 5-6 us feature mixes segments (incl. ones outside the model
  table) and likely saturation — not a window error, but windows alone are not
  sufficient QC for this season.
- 2018 DC8 -17.2 dB at 49-50 us and 2012 DC8 -12.1 dB at 58-59 us: no
  corresponding boundary in the CSV. The 2018 feature comes from segments outside
  the model table (7-waveform configs exist in that season's sheet). The 2012
  season is mcords2, where low/high-gain waveforms were merged at LOAD time
  (wf_adc_comb), a mechanism invisible to img_comb; the 5 single-image 2012 rows
  (window 25-108 us) are the least trustworthy in the CSV — gate 2012 empirically.
- Scattered in-window drops at ~4-6 us in 2016_Greenland_P3, 2018 DC8/GL P3, and
  ~19-22 us in 2022/2023 MKB: not gate artifacts (likely close-range saturation
  ending, terrain/margin crossings). The window is a necessary condition, not a
  sufficient one.

**Recommendation for Stage 1:** join `img_comb_windows.csv` on (season, segment)
and require `surf_valid_min_safe_s <= surface_twtt <= surf_valid_max_safe_s`
(safe variant also covers the Basler/MKB transmit-recovery region and pulse
rolloff), replacing the heuristic ~7.7 us TWTT cut; additionally drop 2012 DC8
high segments or gate them empirically, and keep the existing saturation flag
for the near-range end.
