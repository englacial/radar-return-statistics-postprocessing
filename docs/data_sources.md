# Input data sources

Each external product has a plugin in `src/radar_postproc/datasets/` and is sampled onto each trace of the radar data.
All continuous fields are sampled bilinearly; categorical fields nearest. Output CRS handling is per-source (see each section); radar trace points are EPSG:4326.

> If you re-use this for any purpose (which you're welcome to do!), please be sure to cite the original datasets you're using.

---

## Base layer — radar return statistics (icechunk)

The per-trace rows come from a **pinned snapshot** of the upstream
`radar_return_statistics` icechunk stores
(`s3://opr-radar-metrics/icechunk/{greenland,antarctica}`, read anonymously). These
supply `latitude`, `longitude`, `bed_elevation`, `surface_elevation`, powers,
`qc_pass`, `frame_id`, and the derived `collection` (OPR season). The external
products below are joined onto these points.

See the main [radar-return-statitics](https://github.com/englacial/radar-return-statistics) repository for details.

---

## BedMachine — bed/surface/thickness/mask

**Citations**
- Antarctica: Morlighem, M. et al. (2020), *Deep glacial troughs and stabilizing
  ridges unveiled beneath the margins of the Antarctic ice sheet*, Nature
  Geoscience 13, 132–137. doi:10.1038/s41561-019-0510-8. Dataset: *MEaSUREs
  BedMachine Antarctica, Version 4* (NSIDC-0756).
- Greenland: Morlighem, M. et al. (2017), *BedMachine v3: Complete bed topography
  and ocean bathymetry mapping of Greenland…*, GRL 44, 11051–11061.
  doi:10.1002/2017GL074954. Dataset: *IceBridge BedMachine Greenland, Version 6*
  (IDBMG4).

**Data files** — From NSIDC, fetched via `earthaccess`. Antarctica is
one netCDF (`NSIDC-0756`, v4); Greenland is per-variable GeoTIFFs + a full netCDF
(`IDBMG4`, v6). CRS: EPSG:3031 (Antarctica), EPSG:3413 (Greenland).

| File variable | Output column | Units | Sampling |
|---|---|---|---|
| `bed` | `bedmachine_bed_m` | m (geoid-referenced) | bilinear |
| `surface` | `bedmachine_surface_m` | m | bilinear |
| `thickness` | `bedmachine_thickness_m` | m | bilinear |
| `mask` | `bedmachine_mask` | *categorical* | nearest |
| `errbed` | `bedmachine_errbed_m` | m | bilinear |

`mask` categories: 0 ocean, 1 ice-free land, 2 grounded ice, 3 floating ice,
4 Lake Vostok (Antarctica) / non-Greenland land (Greenland).

**Uncertainty**

`bedmachine_errbed_m` is BedMachine's estimated **bed-elevation error** (in meters). This is generally meant to represent a bound on plausible values, but specific interpretation varies by the interpolation type. Section 2 of the supplement to Morlighem et al., 2020 (see citations above) is the best reference for uncertainty analysis.

In regions employing mass conservation interpolation (fast-flowing regions, generally), error is propaged upstream and downstream from radar data using fixed assumptions about the error in SMB, velocity, and strain rate. The minimum of the upstream and downstream errors is treated as the error term.

For streamline diffusion regions (slow-flowing regions, generally), error is assumed to grow at 20 meters per kilometer from the nearest radar data constraint.

In both cases, error is clipped at 1000 m.

`surface` and `mask` carry no separate error field.

---

## Surface speed — `surface_v_m_yr`

The speed covariate comes from a **different product per ice sheet**, both written to
the same pair of columns (`surface_v_m_yr`, `surface_v_error_m_yr`); the manifest's
per-dataset `source_info` records which product produced a given sheet's values.

| Ice sheet | Product | Resolution | Epoch | Grid coverage |
|---|---|---|---|---|
| Antarctica | MEaSUREs Phase-Based Antarctica Ice Velocity Map v1 (NSIDC-0754) | 450 m | 1996–2018 composite | 99.5% |
| Greenland | ITS_LIVE v2 static mosaic (RGI05A) | 120 m | 2014–2022 climatology | 97.3% |

**Why not ITS_LIVE for Antarctica.** The ITS_LIVE v2 Antarctic mosaic has a polar
hole: on the 5 km model grid it covers 0.0% of cells poleward of 85°S and ~11% /
~55% in the 84–85°S / 82.5–84°S bands. Sampled the way the pipeline does
(bilinear), that blanks 66,082 of 541,277 Antarctic grid cells (12.2%) —
including 3,830 of the 13,796 grid cells that carry a radar observation (28% of the
Antarctic training data). The MEaSUREs annual maps (NSIDC-0720) and the MEaSUREs
Greenland annual mosaics (NSIDC-0725) do **not** fix this — 0720 has a larger,
year-varying hole (0% poleward of 87°S in every year checked) and 0725 has exactly
the same far-north Greenland gap as ITS_LIVE. NSIDC-0754 does: 99.5% of the grid,
95.8% even in the 89–90°S band. Where both are valid the two products agree closely
(Pearson r = 0.961 on log₁₀ speed, median ratio −1.0%, MAD 1.15 m/yr), so the swap
mostly *adds* interior points rather than moving existing ones.

**Caveat.** Using two products across the two sheets means any systematic
inter-product bias in speed is partly confounded with the model's `is_greenland`
indicator, since speed and that indicator both enter the attenuation and
reflectivity terms. Greenland keeps ITS_LIVE because NSIDC-0725 offers it nothing.

### Antarctica — MEaSUREs phase-based (NSIDC-0754)

**Citations**
- Mouginot, J., Rignot, E. & Scheuchl, B. (2019), *Continent-wide, interferometric
  SAR phase, mapping of Antarctic ice velocity*, GRL 46, 9710–9718.
  doi:10.1029/2019GL083826. Dataset: *MEaSUREs Phase-Based Antarctica Ice Velocity
  Map, Version 1* (NSIDC-0754), doi:10.5067/PZ3NJ5RXRH10.

**Data files** — One ~7 GB netCDF from NSIDC via `earthaccess`
(`antarctic_ice_vel_phase_map_v01.nc`), EPSG:3031, 450 m.

| File variable | Output column | Units | Sampling |
|---|---|---|---|
| `hypot(VX, VY)` | `surface_v_m_yr` | m/yr | bilinear (per component) |
| `hypot(VX·ERRX, VY·ERRY) / v` | `surface_v_error_m_yr` | m/yr | bilinear (per component) |

The speed error propagates `ERRX`/`ERRY` through the magnitude; where the speed is
zero the direction is undefined and `hypot(ERRX, ERRY)` is used instead.

**Uncertainty** — Phase-based interferometry is far more precise than feature
tracking, and it shows: over the Antarctic grid the derived speed error has a median
below 1 m/yr and a 99th percentile of ~5 m/yr, against ~58 m/yr for the ITS_LIVE
`v_error` field over Greenland. The two `surface_v_error_m_yr` columns are therefore
**not** on a comparable footing across sheets. Nothing in the model uses this column —
it is carried for provenance only. See
`uv run python scripts/error_histograms.py` → `outputs/error_histograms/surface_v_error.png`.

### Greenland — ITS_LIVE

**Citations**
- ITS_LIVE project (NASA MEaSUREs); see https://its-live.jpl.nasa.gov/#how-to-cite

**Data files** — AWS Open Data, from the velocity mosaics:
`https://its-live-data.s3.amazonaws.com/velocity_mosaic/v2/static/cog/ITS_LIVE_velocity_120m_RGI05A_0000_v02_v.tif`
(RGI05A = Greenland, EPSG:3413; 120 m).
The mosaic is a **2014–2022 climatology** (time-intercept 2018-01-01).

| File variable | Output column | Units | Sampling |
|---|---|---|---|
| `v` (`…_v.tif`, speed band) | `surface_v_m_yr` | m/yr | bilinear |
| `v_error` (`…_v_error.tif`, speed error) | `surface_v_error_m_yr` | m/yr | bilinear |

**Uncertainty** — The ITS_LIVE [documentation](http://its-live-data.jpl.nasa.gov.s3.amazonaws.com/documentation/ITS_LIVE-Regional-Glacier-and-Ice-Sheet-Surface-Velocities.pdf) describes the `v_error` field:

> The uncertainty of each image-pair velocity field is set equal to the standard error in component velocities relative to the stable surface velocity after applying the geolocation offset correction, if available. If an image-pair velocity field does not intersect a stable surface, the errors in vx and vy (parameters vx_err and vy_err in Table 1) are set to the RSS of the pointing uncertainty of both images. This error is updated to the standard deviation of the difference between the image-pair component velocities and the annual mean component velocities if the image-pair velocity is successfully co-registered during the creation of the annual mosaic that is described in the next section.

---

## ERA5 — mean 2 m air temperature

**Citations**
- Hersbach, H. et al. (2020), *The ERA5 global reanalysis*, QJRMS 146, 1999–2049.
  doi:10.1002/qj.3803 (Copernicus Climate Change Service / ECMWF).
- WeatherBench2 (cloud product): Rasp, S. et al. (2024), *WeatherBench 2…*, JAMES
  16, e2023MS004019. doi:10.1029/2023MS004019.

**Data files** — WeatherBench2 ERA5 **hourly climatology** on Google Cloud: `gs://weatherbench2/datasets/era5-hourly-climatology/1990-2019_6h_1440x721.zarr`.
A precomputed `(hour, dayofyear)` climatology at 0.25° over **1990–2019**
(EPSG:4326). The plugin averages over `hour` and `dayofyear` once to a global
long-term mean field (~6 GB read, cached at ~4 MB), reused by every store.

| File variable | Output column | Units | Sampling |
|---|---|---|---|
| `2m_temperature` (mean over hour, dayofyear) | `era5_t2m_mean_K` | K (native, not converted) | bilinear |

**Uncertainty** — Not available in the pre-computed data product used here.

---

## Geothermal heat flow (GHF) — value + lower/upper bounds

**Citations**
- Compilation / files: Fahrner, D. et al. (2025), *Re-gridded and topographically
  corrected geothermal heat flow data* (supplement to Lösing et al. 2026). Zenodo.
  doi:10.5281/zenodo.17745730.
- Recommendation paper: Lösing, M. et al. (2026), *Community heat flow
  recommendations: suitable basal boundary conditions for Greenland and Antarctica
  in ISMIP7*, GEUS Bulletin 62, 8411. doi:10.34194/r0w9rf81.
- Antarctica model: Lösing, M. & Ebbing, J. (2021), *Predicting geothermal heat
  flow in Antarctica with a machine learning approach*, JGR Solid Earth 126,
  e2020JB021499. doi:10.1029/2020JB021499.
- Greenland model: Colgan, W. et al. (2022), *Greenland geothermal heat flow
  database and map (version 1)*, ESSD 14, 2209–2238. doi:10.5194/essd-14-2209-2022.

**Data files** — Zenodo record 17745730, `GHF_Regridded.zip` (~1.5 GB). We use the **re-gridded, non-topographically-corrected** members at 500 m:
- Antarctica: `Loesing&Ebbing(2021)_GHF_resampled_500m.nc` (EPSG:3031)
- Greenland: `Colgan&Wansing(2021)_GHF_resampled_500m.nc` (EPSG:3413; `…_wNGRIP_…`
  with `ngrip: true`).

Region selects the model automatically (Antarctic → Lösing & Ebbing, Greenland →
Colgan). Source values are **W/m²**; the plugin converts to mW/m² (×1000).

| File variable | Output column | Units | Sampling |
|---|---|---|---|
| `HF` | `ghf_mW_m2` | mW/m² (from W/m²) | bilinear |
| `HFmin` | `ghf_lower_mW_m2` | mW/m² | bilinear |
| `HFmax` | `ghf_upper_mW_m2` | mW/m² | bilinear |

**Uncertainty** — `ghf_lower_mW_m2` / `ghf_upper_mW_m2` are a **min/max envelope**,
not a Gaussian 1σ, and may be asymmetric about `ghf_mW_m2`. Interpretation differs
by model:
- **Lösing & Ebbing (Antarctica):** bounds span alternative machine-learning model
  runs with different feature set-ups (envelope ~0–80 mW/m²). The file also has
  `HFmaxAbs` (max absolute difference across runs), which is **not** mapped.
- **Colgan (Greenland):** bounds from jackknife resampling of the borehole
  measurements (envelope ~0–60 mW/m²).

The topographically corrected product does not include an uncertainty.
