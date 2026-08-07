# Ice Sounder Mission Design Tool

A dependency-free static web app: enter radar and platform design parameters,
get expected basal SNR maps and distributions over Greenland and Antarctica,
driven by the trained required-surface-SNR (RSSNR) model in this repo.

No server, no build step, no framework, no CDN — plain HTML, CSS, and ES
modules, with all computation done in the browser over typed arrays.

## Use it

Two builds, both produced from the same sources:

**Hosted (GitHub Pages).** Serve this directory as-is:

```bash
python -m http.server 8777   # then open http://localhost:8777/mission_design_tool/
```

`fetch()` is blocked on `file://`, so opening `index.html` by double-clicking
will not work — use the standalone build for that.

**Standalone.** `dist/mission_design_tool.html` is one self-contained file with
the data base64-inlined. Double-click it, or email it to a colleague.

## Rebuild after retraining the model

Snakemake owns this, so the payload cannot fall behind the model it ships:

```bash
uv run snakemake --cores 4 mission_tool     # data + standalone build
uv run snakemake --cores 4 model_all        # trains, then rebuilds the tool
```

`mission_tool_data` depends on `predictions.zarr`, `grid.parquet`, and the
model's manifest, so retraining marks it out of date automatically.
`config/model.yaml` names which model the tool ships:

```yaml
mission_tool:
  model: atten_refl
```

It sits in its own section, so it never enters a stage's section hash and
cannot perturb a `run_id`. Override per-invocation with
`--config mission_tool_model=linear`. The scripts still run standalone:

```bash
uv run python mission_design_tool/build_data.py --model atten_refl
uv run python mission_design_tool/build_standalone.py
```

`build_data.py` joins `outputs/model/<model>/predictions.zarr` (RSSNR mean and
predictive sd) with `outputs/model/grid.parquet` (ice thickness, BedMachine
mask) and writes one gzipped, valid-cells-only buffer per ice sheet:

| field | type | scale |
|---|---|---|
| `idx` | uint32 | flat row-major raster index |
| `thk` | uint16 | ice thickness, m |
| `mu` | int16 | RSSNR × 10, dB |
| `sd` | uint8 | predictive sd × 4, dB |
| `mask` | uint8 | BedMachine mask (2 grounded, 3 floating, 4 Lake Vostok) |

It also writes `data/coast.json.gz`: two sets of outlines per sheet, in
prediction-raster **pixel** coordinates, so the browser draws them with no
projection maths.

- `coast` — contour of BedMachine `mask > 0`, the same convention
  `scripts/prediction_summary_figures.py` uses.
- `grounding` — the boundary between grounded ice (2) and floating ice (3),
  with ocean, rock and Lake Vostok / non-Greenland land masked out so the
  calving front does not come back as a duplicate coastline.

Douglas-Peucker simplified to 0.6 px; 20 KB gzipped for both sheets and both
kinds. `--no-coastlines` skips them, and the maps render fine without them.

Widest fields first, so every field stays naturally aligned for a JS
`TypedArray` view regardless of the cell count. 596k cells total, 6.0 MB raw,
2.6 MB gzipped. `data/meta.json` carries the raster geometry, quantisation
scales, detection parameters, and the model's `run_id` for provenance.

## What it computes

```
SNR_bed = SNR_surface + G_pulse-comp + Δspreading(d) − RSSNR
```

`SNR_surface` is a conventional nadir-sounder link budget (`physics.js`),
excluding pulse-compression gain, which is added back once for the bed. The
transmitted pulse is the same everywhere, so that gain is a constant; only
geometric spreading (through ice thickness `d`) and the fitted RSSNR vary from
cell to cell, plus the surface/bed overlap treatment below. The map can show
either the median layer or the 20th percentile
(`mean − 0.8416σ`, the same conservative layer as the repo's `map_q80`), and the
distributions can show the posterior predictive mixture rather than the point
estimates.

**Radar equation.** The coherent surface return is computed in one of two forms
from **Haynes, M. S. et al. (2018), "Surface and Subsurface Radar Equations for
Planetary Radar Sounders", IEEE TGRS**,
[doi:10.1109/TGRS.2018.2811509](https://doi.org/10.1109/TGRS.2018.2811509):
eq 21 (infinite mirror, the default) which the paper recommends for terrestrial
airborne sounders and which CReSIS/OPR calibrate to, or eq 18 (Fresnel zone)
which it recommends for smooth coherent targets such as orbital sounders. They
differ by exactly 6.02 dB. Only the absolute surface term moves — the per-cell
surface-to-bed correction is a ratio, so the constant cancels there, and in
sidelobe-limited cells the surface term drops out of the answer altogether.

**Surface/bed overlap.** Where the ice is thin enough that the bed echo returns
before the transmitted pulse ends, the two returns overlap. Two treatments:

- *Charge for surface sidelobes* (default) — keep the pulse and add the
  compressed surface return's sidelobe pedestal at the bed's delay to the noise
  floor. That pedestal scales with the surface return, so **transmit power
  cancels out of it exactly**; only bandwidth, a shorter pulse, or receive
  weighting helps.
- *Shorten pulse to avoid overlap* — shorten the pulse per cell until the bed
  clears it, capped at the configured length. No overlap, less compression gain
  in thin ice. Note this implies a system that switches modes by region.

Hovering a map cell shows the working: the adapted pulse length in the first
mode, or the sidelobe and noise levels in dBm in the second, so you can see
directly which one is limiting. `overlapDetail()` produces that readout and the
self-test asserts it reproduces the map's own arithmetic.

Sidelobe levels come from `sidelobes.js`, tabulated by `build_sidelobes.py` from
a direct matched-filter simulation against the time-bandwidth product and the
normalised delay. Rectangular has a closed form (`−20 log10(π·B·Δt)`) but is
10–15 dB pessimistic near the end of the pulse, and Hann has none — at fixed
`B·Δt` its level still varies ~20 dB with pulse length — so both are tabulated.
Weighting is modelled on **receive**, costing 1.76 dB of SNR for Hann rather
than the 4.26 dB of transmit energy a transmit-side taper would cost.

Noise is composed as `T_sys = T_antenna + T0(F − 1)`, with the antenna
temperature defaulting to the **greater of** the galactic sky background and a
270 K floor. The sky term dominates at VHF (≈3700 K at 60 MHz against a few
hundred K from the receiver) and follows `Fam = 52 − 23 log10(f/MHz)` dB, the
median galactic noise figure from **ITU-R P.372** (*Radio noise*); the
underlying spectrum is **Cane, H. V. (1979), "Spectra of the non-thermal radio
radiation from the galactic polar regions", MNRAS 189, 465**.

The floor exists because a nadir sounder's main beam is aimed at the ice, not
the sky, and the antenna and feed contribute their own physical temperature
through ohmic loss — so the antenna cannot be much colder than the surface it
looks at. It binds above roughly 200 MHz, where the galactic term alone would
fall to a few tens of kelvin, and costs about 1.0 dB at 300 MHz and 1.3 dB at
450 MHz.

`selftest.mjs` checks the data path end to end and pins the link budget to the
two cases in `reference/UAV IPR Link Budget.ipynb` (surface SNR 100.74 dB and
112.0 dB), which it reproduces exactly — pinning the PRI and antenna temperature,
whose auto rules now deliberately differ from the notebook's.

## Files

The page reads top to bottom: what the tool is → presets → parameters (collapsed,
each showing its key values in the summary line) → results. Two sidenote boxes —
provenance for the model, and any parameter checks that are firing — sit in the
margin on wide screens and fold into the column on narrow ones, along with a
third holding the "what to show" controls next to the plots they affect. Each note tracks
the section it describes (`data-anchor` on the element) and slides down only far
enough to clear the note above it, the way Google Docs places comments;
`layoutRail()` in `app.js` does that placement.

**To add a preset**, add an entry to `PRESETS` in `presets.js`: an `id`, a `name`,
an `icon` key from `icons.js`, a `note`, and `values` in SI units. Anything
`values` omits falls back to the page's own default, so a preset always describes
a complete configuration — and a preset stays selected only while every parameter
still matches it, so touching anything drops the selection to Custom.

Brand colours are defined once at the top of `style.css` as `--eng-*`, with the
page's semantic roles (`--accent`, `--warn`, …) mapped onto them just below.

| | |
|---|---|
| `index.html` / `style.css` | markup and styling |
| `logos/` | Englacial and Astera marks for the attribution line (inlined into the bundle) |
| `presets.js` | **the presets** — edit or add entries here; buttons are generated from the list |
| `icons.js` | the aircraft / airship / satellite / sliders marks used to tag presets |
| `auto.js` | what the tool picks when a field is left on *auto* — one pure rule per field |
| `sidelobes.js` | **generated** by `build_sidelobes.py` — LFM range-sidelobe envelopes |
| `physics.js` | the link budget itself and the per-cell terms |
| `warnings.js` | plausibility and validity rules — one predicate per check |
| `app.js` | state, rendering, exports |
| `build_data.py` | model outputs → `data/` |
| `build_standalone.py` | → `dist/mission_design_tool.html` |
| `selftest.mjs` | headless checks of the physics and data (`node mission_design_tool/selftest.mjs`) |
| `smoketest.mjs` | runs the page against a fake DOM — catches a broken first paint |

```bash
node mission_design_tool/selftest.mjs                                  # physics + data
node mission_design_tool/smoketest.mjs                                 # the module page
node mission_design_tool/smoketest.mjs dist/mission_design_tool.html   # the bundled page
```

`smoketest.mjs` parses the HTML, stubs just enough DOM and canvas for `app.js`,
points `fetch` at `data/`, then loads the page and drives it: every preset, a
blanked field, an invalid one, the detection view, the floating split, the
exports, and a hover. It is not a browser — it will not catch a layout or paint
problem — but it does catch missing elements, ordering mistakes, and anything
that throws. Run it against the bundle too: that path concatenates the modules
and is the artifact people actually get sent.

The first three are DOM-free and pure, so they can be read and tested on their
own. `auto.js` resolves in dependency order (pulse length → PRI → duty cycle →
transmit power); `physics.js` calls `resolve()` and takes what it gets, so the
"what does the tool assume" rules live in exactly one place. `warnings.js`
classifies each rule as `error` (the arithmetic or model is not meaningful —
results are suppressed) or `warn` (possible, but an assumption is stretched);
`selftest.mjs` asserts every check names a real field, that the two notebook
presets raise no errors, and that representative bad inputs each trip the
expected rule.
