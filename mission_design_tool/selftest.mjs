// Headless check of the data path + link budget. `node mission_design_tool/selftest.mjs`
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AUTO, AUTO_IDS } from './auto.js';
import { RADAR_EQUATION, T_ANTENNA_FLOOR, scalars, basalSNR, orbitalSpeeds,
         overlapDetail, percentileSNR, skyTemperature } from './physics.js';
import { CHECKS, check as runChecks, hasError } from './warnings.js';
import { WEIGHTING_LOSS_dB, sidelobeLevel } from './sidelobes.js';

const here = dirname(fileURLToPath(import.meta.url));
const meta = JSON.parse(readFileSync(join(here, 'data/meta.json')));
let fail = 0;
const check = (name, got, want, tol) => {
  const ok = Math.abs(got - want) <= tol;
  if (!ok) fail++;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}: ${got.toFixed(3)} (expected ${want} ±${tol})`);
};

function load(sheet) {
  const gz = readFileSync(join(here, `data/${sheet}.bin.gz`));
  const raw = gunzipSync(gz);
  const buf = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength);
  const n = buf.byteLength / 10;
  let o = 0;
  const idx = new Uint32Array(buf, o, n); o += 4 * n;
  const thk = new Uint16Array(buf, o, n); o += 2 * n;
  const mu16 = new Int16Array(buf, o, n); o += 2 * n;
  const sd = new Uint8Array(buf, o, n); o += n;
  const mask = new Uint8Array(buf, o, n);
  const mu = new Float32Array(n);
  for (let i = 0; i < n; i++) mu[i] = mu16[i] / meta.mu_scale;
  return { n, idx, thk, mu, sd, mask };
}

const base = {
  altitude_m: 12500, velocity_ms: 22, payload_power_W: 45, pa_efficiency: 0.5,
  max_ice_thickness_m: 5000, frequency_Hz: 60e6, bandwidth_Hz: 10e6,
  gain_tx_dBi: 3, gain_rx_dBi: 3, system_loss_dB: 0, noise_figure_dB: 4,
  surface_reflectivity_dB: -10, epsilon_r: 3.17,
  overlap_mode: 'sidelobe', sidelobe_window: 'rect',
  // The reference notebook uses lambda^2/(4*pi*h)^2 — Haynes 2018 eq 18. The
  // tool now defaults to eq 21, which is 6.02 dB lower, so pin the form here
  // or the notebook cases cannot reproduce.
  radar_equation: 'fresnel',
};

// --- link budget vs the notebooks -------------------------------------------
// Two auto rules deliberately differ from the notebook now: the PRI lets pulses
// fly simultaneously, and the antenna temperature follows the sky rather than
// sitting at T0. Pin both to the notebook's values so this still checks the
// budget chain itself rather than the policy on top of it.
const NOTEBOOK = { pri_s: 230e-6, noise_temp_K: 290 };
const s15 = scalars(base, NOTEBOOK);
check('15 m: pulse length [us]', s15.pulse_length_s * 1e6, 80, 0.01);
check('15 m: PRF [Hz]', s15.prf, 4347.83, 0.5);
check('15 m: duty cycle [%]', s15.duty_cycle * 100, 34.78, 0.01);
check('15 m: Tx power [W]', s15.tx_power_W, 60, 0.01);
check('15 m: azimuth gain [dB]', s15.azimuth_gain_dB, 46.94, 0.02);
check('15 m: surface SNR [dB]', s15.surface_snr_dB, 100.74, 0.02);   // notebook value
const s25 = scalars({ ...base, payload_power_W: 620 }, NOTEBOOK);
check('25 m: surface SNR [dB]', s25.surface_snr_dB, 112.0, 0.02);    // notebook value

// Every field app.js shows as "auto" must come back from scalars() under the
// same name as its input ID, or the box silently renders blank.
const html = readFileSync(join(here, 'index.html'), 'utf8');
const derived = [...html.matchAll(/id="([^"]+)"[^>]*data-derived/g)].map((m) => m[1]);
check('auto inputs found in index.html', derived.length, AUTO_IDS.length, 0);
for (const id of derived) {
  check(`auto.js has a rule for ${id}`, AUTO[id] ? 0 : 1, 0, 0);
  check(`scalars() returns ${id}`, Number.isFinite(s15[id]) ? 0 : 1, 0, 0);
}
check('every auto rule has an input box',
      AUTO_IDS.filter((id) => !derived.includes(id)).length, 0, 0);

// An override is stored in SI units and must survive the round trip.
check('override: pulse length honoured [us]',
      scalars(base, { pulse_length_s: 70e-6 }).pulse_length_s * 1e6, 70, 1e-9);
// echo window 59.4 + 70 = 129.4 us -> 130 us gap, + 70 us transmit = 200 us
check('override: duty cycle follows',
      scalars(base, { pulse_length_s: 70e-6 }).duty_cycle * 100, 100 * 70 / 200, 0.01);

// --- warnings ---------------------------------------------------------------
// Every check must have a unique id and name a field that exists on the page.
const htmlIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
check('check ids are unique', new Set(CHECKS.map((c) => c.id)).size - CHECKS.length, 0, 0);
check('every check names a real field',
      CHECKS.filter((c) => !htmlIds.has(c.field)).length, 0, 0);
check('every check has a valid level',
      CHECKS.filter((c) => !['warn', 'error'].includes(c.level)).length, 0, 0);

// The notebook cases must be clean, or the tool cries wolf on its own presets.
const check15 = runChecks(base, s15);
check('15 m preset raises no errors', hasError(check15) ? 1 : 0, 0, 0);
check('25 m preset raises no errors',
      hasError(runChecks({ ...base, payload_power_W: 620 }, s25)) ? 1 : 0, 0, 0);
console.log(`     15 m preset warnings: ${check15.map((h) => h.id).join(', ') || 'none'}`);

// Representative failures must be caught, each by the expected rule.
const fires = (over, id) => {
  const p = { ...base, ...over };
  let s; try { s = scalars(p); } catch { s = scalars(base); }
  return runChecks(p, s).some((h) => h.id === id) ? 0 : 1;
};
check('catches zero altitude', fires({ altitude_m: 0 }, 'altitude'), 0, 0);
check('catches efficiency > 1', fires({ pa_efficiency: 1.5 }, 'efficiency'), 0, 0);
check('catches positive reflectivity', fires({ surface_reflectivity_dB: 3 }, 'reflectivity'), 0, 0);
check('catches bandwidth wider than carrier', fires({ bandwidth_Hz: 200e6 }, 'bandwidth_vs_carrier'), 0, 0);
check('catches odd permittivity', fires({ epsilon_r: 6 }, 'permittivity_odd'), 0, 0);
check('catches out-of-band frequency', fires({ frequency_Hz: 5e9 }, 'band'), 0, 0);
check('catches azimuth undersampling', fires({ velocity_ms: 7500 }, 'azimuth_aliasing'), 0, 0);

// --- noise composition ------------------------------------------------------
// T_sys = T_ant + T0(F-1): a higher noise figure must always raise the floor,
// and the old T*F form was only correct at T_ant = T0.
{
  const at = (T, NF) => scalars({ ...base, noise_figure_dB: NF }, { noise_temp_K: T });
  check('T_sys at T0 equals T0*F', at(290, 4).system_temp_K, 290 * 10 ** 0.4, 0.5);
  check('cold antenna, 5 dB NF', at(50, 5).system_temp_K, 50 + 290 * (10 ** 0.5 - 1), 0.5);
  check('hot antenna, 5 dB NF', at(4000, 5).system_temp_K, 4000 + 290 * (10 ** 0.5 - 1), 0.5);
  let mono = 0, prev = -Infinity;
  for (let nf = 0; nf <= 15; nf += 0.25) {
    const n = at(1000, nf).noise_dBm;
    if (n < prev - 1e-9) mono = 1;
    prev = n;
  }
  check('noise floor rises monotonically with noise figure', mono, 0, 0);
  check('a hot antenna is no longer double-counted',
        at(4000, 5).noise_dBm, 10 * Math.log10(1.380649e-23 * (4000 + 290 * (10 ** 0.5 - 1)) * 10e6 / 1e-3), 0.02);
}

// --- sky temperature (ITU-R P.372 galactic noise) ---------------------------
check('sky temperature at 60 MHz [K]', skyTemperature(60e6), 3738, 5);
check('sky temperature at 450 MHz [K]', skyTemperature(450e6), 36, 2);
check('sky temperature falls with frequency',
      skyTemperature(60e6) > skyTemperature(300e6) ? 0 : 1, 0, 0);
check('sky temperature floors rather than vanishing', skyTemperature(20e9) >= 20 ? 0 : 1, 0, 0);

// The antenna is aimed at the ice and has ohmic loss of its own, so its
// temperature cannot follow the sky down to a few tens of kelvin.
{
  const at = (f) => scalars({ ...base, frequency_Hz: f }).noise_temp_K;
  check('VHF is sky-dominated, not floored', at(60e6), 3738, 5);
  check('UHF falls back to the antenna floor', at(450e6), T_ANTENNA_FLOOR, 0.5);
  check('the floor is never below the sky', at(150e6) >= skyTemperature(150e6) - 0.5 ? 0 : 1, 0, 0);
  let bad = 0;
  for (let f = 5e6; f < 2e9; f *= 1.2) if (at(f) < T_ANTENNA_FLOOR - 0.5) bad++;
  check('antenna temperature never drops below the floor', bad, 0, 0);
}

// --- transmit power is monotonic in payload power ---------------------------
{
  let breaks = 0, worst = 0, prev = -Infinity;
  for (let pw = 0.5; pw < 2000; pw *= 1.01) {
    const v = scalars({ ...base, payload_power_W: pw }).tx_power_W;
    if (v < prev - 1e-9) { breaks++; worst = Math.max(worst, prev - v); }
    prev = v;
  }
  check('more payload power never selects less transmit power', breaks, 0, 0);
  check('the notebook increments still apply', scalars({ ...base, payload_power_W: 620 }).tx_power_W, 800, 0);
}

// --- pulse repetition: echo window, not the full round trip ------------------
{
  const s = scalars(base);
  check('PRI accommodates the echo window',
        s.pri_s >= s.echo_window_s + s.pulse_length_s ? 0 : 1, 0, 0);
  // With a pulse sized to the ice column rather than the altitude, an orbital
  // case is no longer capped near 150 Hz by the 3.3 ms round trip.
  const orbit = scalars({ ...base, altitude_m: 500e3, velocity_ms: 7062 },
                        { pulse_length_s: 30e-6 });
  check('pulses may fly simultaneously at orbital altitude',
        orbit.pulses_in_flight >= 1 ? 0 : 1, 0, 0);
  check('orbital PRF is set by the echo window, not the round trip',
        orbit.prf > 5000 ? 0 : 1, 0, 0);
  console.log(`     orbital PRF ${orbit.prf.toFixed(0)} Hz, `
    + `${orbit.pulses_in_flight} pulses in flight, echo window ${(orbit.echo_window_s * 1e6).toFixed(0)} us`);
  // The old rule waited out the whole round trip; keep a record of the gain.
  const roundTrip = 1 / (Math.ceil(orbit.twtt_bed_max * 0.1e6) / 0.1e6 + 30e-6);
  console.log(`     (round-trip rule would give ${roundTrip.toFixed(0)} Hz)`);
}

// --- orbital mechanics ------------------------------------------------------
const o500 = orbitalSpeeds(500e3);
check('500 km: inertial speed [m/s]', o500.orbital, 7617, 2);
check('500 km: ground-track speed [m/s]', o500.ground, 7062, 2);
check('500 km: effective speed [m/s]', o500.effective, 7334, 2);
check('500 km: period [min]', o500.period_s / 60, 94.5, 0.1);
check('ground track is slower than inertial', o500.ground < o500.orbital ? 0 : 1, 0, 0);
// The rule fires only above 100 km, and only when the speed does not fit the altitude.
check('orbital-velocity rule fires at 500 km with an aircraft speed',
      fires({ altitude_m: 500e3, velocity_ms: 22 }, 'orbital_velocity'), 0, 0);
check('orbital-velocity rule flags the inertial speed',
      fires({ altitude_m: 500e3, velocity_ms: 7617 }, 'orbital_velocity'), 0, 0);
check('orbital-velocity rule accepts the ground-track speed',
      fires({ altitude_m: 500e3, velocity_ms: 7062 }, 'orbital_velocity'), 1, 0);
check('orbital-velocity rule is silent below 100 km',
      fires({ altitude_m: 99e3, velocity_ms: 22 }, 'orbital_velocity'), 1, 0);
check('orbital-velocity rule is silent for the default aircraft case',
      fires({}, 'orbital_velocity'), 1, 0);
// --- azimuth aperture -------------------------------------------------------
// Auto matches the first Fresnel zone, so the notebook cases still reproduce.
{
  const sA = scalars(base);   // the page's own auto rules, not the notebook pins
  check('auto azimuth distance = the coherent aperture',
        sA.azimuth_distance_m, sA.coherent_aperture_m, 1e-9);
  check('auto azimuth distance raises no warning',
        runChecks(base, sA).some((h) => h.id === 'azimuth_too_long') ? 1 : 0, 0, 0);

  const zero = scalars(base, { azimuth_distance_m: 0 });
  check('zero aperture gives no azimuth gain', zero.azimuth_gain_dB, 0, 0);
  check('zero aperture integrates no pulses', zero.pulses_integrated, 0, 0);
  check('zero aperture warns', runChecks(base, zero).some((h) => h.id === 'no_azimuth') ? 0 : 1, 0, 0);
  check('zero aperture drops the surface SNR by the azimuth gain',
        sA.surface_snr_dB - zero.surface_snr_dB, sA.azimuth_gain_dB, 1e-9);

  const half = scalars(base, { azimuth_distance_m: sA.coherent_aperture_m / 2 });
  check('half aperture halves the pulses', half.pulses_integrated,
        Math.floor(sA.pulses_integrated / 2), 1);
  check('half aperture costs ~3 dB', sA.azimuth_gain_dB - half.azimuth_gain_dB, 3.01, 0.02);
  check('half aperture raises no length warning',
        runChecks(base, half).some((h) => h.id === 'azimuth_too_long') ? 1 : 0, 0, 0);

  const long = scalars(base, { azimuth_distance_m: sA.coherent_aperture_m * 2 });
  check('over-long aperture warns',
        runChecks(base, long).some((h) => h.id === 'azimuth_too_long') ? 0 : 1, 0, 0);
  check('over-long aperture does not also claim zero gain',
        runChecks(base, long).some((h) => h.id === 'no_azimuth') ? 1 : 0, 0, 0);
}
// A duty cycle at/over 100% is a hard error, not a warning.
{
  const p = { ...base };
  const s = scalars(p, { pri_s: 40e-6 });      // PRI shorter than the 80 us pulse
  check('duty cycle >= 1 is an error', hasError(runChecks(p, s)) ? 0 : 1, 0, 0);
}
// An override beyond the payload's power budget must be flagged.
{
  const s = scalars(base, { tx_power_W: 5000 });
  check('over-budget transmit power flagged',
        runChecks(base, s).some((h) => h.id === 'over_power') ? 0 : 1, 0, 0);
}
// No check may throw, whatever it is handed.
{
  const junk = Object.fromEntries(Object.keys(base).map((k) => [k, NaN]));
  let threw = 0;
  try { runChecks(junk, scalars(junk)); } catch { threw = 1; }
  check('checks survive all-NaN input', threw, 0, 0);
}

// --- data integrity ---------------------------------------------------------
for (const sheet of ['antarctic', 'greenland']) {
  const c = load(sheet);
  const g = meta.sheets[sheet];
  check(`${sheet}: cell count`, c.n, g.n, 0);
  const maxIdx = g.shape[0] * g.shape[1];
  let bad = 0, badMask = 0;
  for (let i = 0; i < c.n; i++) {
    if (c.idx[i] >= maxIdx) bad++;
    if (![2, 3, 4].includes(c.mask[i])) badMask++;
    if (i > 0 && c.idx[i] <= c.idx[i - 1]) bad++;   // strictly increasing
  }
  check(`${sheet}: indices in range & sorted`, bad, 0, 0);
  check(`${sheet}: mask values valid`, badMask, 0, 0);

  let muMin = Infinity, muMax = -Infinity, thkMax = 0;
  for (let i = 0; i < c.n; i++) {
    muMin = Math.min(muMin, c.mu[i]); muMax = Math.max(muMax, c.mu[i]);
    thkMax = Math.max(thkMax, c.thk[i]);
  }
  console.log(`     ${sheet}: RSSNR ${muMin.toFixed(1)}..${muMax.toFixed(1)} dB, ` +
              `max thickness ${thkMax} m, floating cells ` +
              `${c.mask.reduce((a, m) => a + (m === 3), 0)}`);

  // --- end-to-end ----------------------------------------------------------
  const snr = new Float32Array(c.n);
  basalSNR(s25, { ...base, payload_power_W: 620 }, c.thk, c.mu, snr);
  const frac = (t) => snr.reduce((a, v) => a + (v >= t), 0) / c.n;
  console.log(`     ${sheet} @112 dB surface SNR: ` +
              `>=0 dB ${(frac(0) * 100).toFixed(1)}%, >=10 dB ${(frac(10) * 100).toFixed(1)}%, ` +
              `>=40 dB ${(frac(40) * 100).toFixed(1)}%`);
}

// --- radar equation form (Haynes et al. 2018) -------------------------------
// eq 18 (Fresnel zone) and eq 21 (infinite mirror) differ by exactly a factor
// of 4 in the constant; only the absolute surface term should move.
{
  const f = scalars({ ...base, radar_equation: 'fresnel' });
  const i = scalars({ ...base, radar_equation: 'infinite' });
  check('eq 18 vs eq 21 spreading differ by 6.02 dB',
        f.spreading_surface_dB - i.spreading_surface_dB, 10 * Math.log10(4), 0.001);
  check('eq 18 is the more optimistic', f.surface_snr_dB > i.surface_snr_dB ? 0 : 1, 0, 0);
  check('an unknown form falls back to the default (eq 21)',
        scalars({ ...base, radar_equation: 'nonsense' }).spreading_surface_dB,
        i.spreading_surface_dB, 1e-9);
  check('both forms are labelled', RADAR_EQUATION.fresnel.label && RADAR_EQUATION.infinite.label ? 0 : 1, 0, 0);
  // The per-cell surface-to-bed correction is a ratio, so the constant cancels
  // and the form shifts every cell by the same 6.02 dB — but only where the bed
  // is noise-limited. Where the surface sidelobe dominates, the surface term
  // cancels out of the answer entirely and the form makes no difference at all.
  const c = load('antarctic');
  const runBoth = (mode) => {
    const pf = { ...base, radar_equation: 'fresnel', overlap_mode: mode };
    const pi = { ...base, radar_equation: 'infinite', overlap_mode: mode };
    const a = new Float32Array(c.n), b2 = new Float32Array(c.n);
    basalSNR(scalars(pf), pf, c.thk, c.mu, a);
    basalSNR(scalars(pi), pi, c.thk, c.mu, b2);
    let worst = 0;
    for (let k = 0; k < c.n; k += 97) worst = Math.max(worst, Math.abs((a[k] - b2[k]) - 10 * Math.log10(4)));
    return worst;
  };
  check('noise-limited: the form shifts every cell by 6.02 dB', runBoth('adaptive'), 0, 1e-4);
  check('sidelobe-limited: the form makes no difference (surface cancels)',
        runBoth('sidelobe') > 6 ? 0 : 1, 0, 0);
}

// --- surface/bed overlap ----------------------------------------------------
// The sidelobe table must reproduce the direct matched-filter simulation, and
// the two branches must agree wherever the bed clears the pulse.
check('sidelobe table, rect  tau=90us B*dt=150', sidelobeLevel('rect', 15e6 * 90e-6, 150 / 15e6 / 90e-6), -53.5, 0.2);
check('sidelobe table, hann  tau=90us B*dt=150', sidelobeLevel('hann', 15e6 * 90e-6, 150 / 15e6 / 90e-6), -72.1, 0.2);
check('sidelobe table, hann  tau=300us B*dt=50', sidelobeLevel('hann', 15e6 * 300e-6, 50 / 15e6 / 300e-6), -100.8, 0.3);
check('hann is always below rect',
      sidelobeLevel('hann', 1350, 0.1) < sidelobeLevel('rect', 1350, 0.1) ? 0 : 1, 0, 0);
check('no response past the pulse', sidelobeLevel('rect', 1350, 1.5) === -Infinity ? 0 : 1, 0, 0);
check('hann weighting loss', WEIGHTING_LOSS_dB.hann, 1.76, 0.001);
{
  const c = load('antarctic');
  const run = (over) => {
    const p = { ...base, payload_power_W: 620, ...over };
    const out = new Float32Array(c.n);
    basalSNR(scalars(p), p, c.thk, c.mu, out);
    return out;
  };
  // A short pulse leaves the bed outside it everywhere, so the branches agree.
  // (base's own 80 us pulse does overlap, hence the explicit override here.)
  const runShort = (mode) => {
    const p = { ...base, payload_power_W: 620, overlap_mode: mode };
    const out = new Float32Array(c.n);
    basalSNR(scalars(p, { pulse_length_s: 1e-6 }), p, c.thk, c.mu, out);
    return out;
  };
  const shortSide = runShort('sidelobe');
  const shortAdapt = runShort('adaptive');
  let same = 0;
  for (let i = 0; i < c.n; i++) if (Math.abs(shortSide[i] - shortAdapt[i]) > 1e-6) same = 1;
  check('with a 1 us pulse the two branches agree exactly', same, 0, 0);

  // With a long pulse they diverge, and the sidelobe branch must be the harsher.
  const longSide = run({ overlap_mode: 'sidelobe', altitude_m: 500e3 });
  const longAdapt = run({ overlap_mode: 'adaptive', altitude_m: 500e3 });
  const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  console.log(`     500 km, 3330 us pulse: adaptive ${mean(longAdapt).toFixed(1)} dB mean, `
    + `sidelobe/rect ${mean(longSide).toFixed(1)} dB`);
  check('the two branches diverge on a long pulse',
        Math.abs(mean(longSide) - mean(longAdapt)) > 1 ? 0 : 1, 0, 0);

  // The whole point: the sidelobe pedestal scales with the surface, so transmit
  // power cancels out of it. Coverage must not move with 20 dB more power.
  const cov = (arr, t) => arr.reduce((a, v) => a + (v >= t ? 1 : 0), 0) / arr.length;
  const p1 = run({ overlap_mode: 'sidelobe', altitude_m: 500e3 });
  const p100 = run({ overlap_mode: 'sidelobe', altitude_m: 500e3, payload_power_W: 62000 });
  check('transmit power does not move a sidelobe-limited result',
        cov(p100, 10) - cov(p1, 10), 0, 0.005);
  const wide = run({ overlap_mode: 'sidelobe', altitude_m: 500e3, bandwidth_Hz: 200e6 });
  check('bandwidth does move it', cov(wide, 10) > cov(p1, 10) ? 0 : 1, 0, 0);
}

// The hover readout must describe the same arithmetic the map used.
{
  const thk = new Uint16Array([200, 800, 1500, 2500, 3500, 4500]);
  const mu = new Float32Array([40, 55, 68, 78, 84, 90]);
  for (const over of [{ overlap_mode: 'sidelobe', sidelobe_window: 'rect' },
                      { overlap_mode: 'sidelobe', sidelobe_window: 'hann' },
                      { overlap_mode: 'adaptive', sidelobe_window: 'rect' }]) {
    const p = { ...base, ...over };
    const sc = scalars(p);
    const out = new Float32Array(thk.length);
    basalSNR(sc, p, thk, mu, out);
    let worst = 0;
    for (let i = 0; i < thk.length; i++) {
      const d = overlapDetail(sc, p, thk[i]);
      const spread = 20 * Math.log10(p.altitude_m / (p.altitude_m + thk[i] / sc.n_ice));
      const implied = sc.surface_snr_dB + spread + d.gain_dB - d.degradation_dB - mu[i];
      worst = Math.max(worst, Math.abs(implied - out[i]));
    }
    // 1e-5 dB, not 0: the map is stored Float32, the readout computed in double.
    check(`hover detail matches the map (${over.overlap_mode}/${over.sidelobe_window})`,
          worst, 0, 1e-5);
  }
}

// --- percentile layer -------------------------------------------------------
{
  const c = load('antarctic');
  const snr = new Float32Array(c.n);
  basalSNR(s25, { ...base, payload_power_W: 620 }, c.thk, c.mu, snr);
  const q20 = percentileSNR(snr, c.sd, meta.sd_scale, 0.8416, new Float32Array(c.n));
  let worst = 0, mean = 0;
  for (let i = 0; i < c.n; i++) {
    const drop = snr[i] - q20[i];
    mean += drop / c.n;
    if (drop > worst) worst = drop;
    if (drop < 0) { console.log('FAIL percentile above the mean'); fail++; break; }
  }
  check('q20 is 0.8416 sigma below the mean',
        (snr[0] - q20[0]) / (c.sd[0] / meta.sd_scale), 0.8416, 1e-4);
  console.log(`     q20 sits ${mean.toFixed(1)} dB below the mean on average `
              + `(worst cell ${worst.toFixed(1)} dB)`);
}

// --- posterior predictive mixture -------------------------------------------
// Convolving with each cell's own sigma must conserve mass and broaden, not
// shift: same mean, larger variance, by exactly sigma^2.
{
  const c = load('antarctic');
  const snr = new Float32Array(c.n);
  basalSNR(s25, { ...base, payload_power_W: 620 }, c.thk, c.mu, snr);
  let mean = 0, sig2 = 0;
  for (let i = 0; i < c.n; i++) {
    mean += snr[i] / c.n;
    sig2 += (c.sd[i] / meta.sd_scale) ** 2 / c.n;
  }
  let v0 = 0;
  for (let i = 0; i < c.n; i++) v0 += (snr[i] - mean) ** 2 / c.n;
  console.log(`     point-estimate spread ${Math.sqrt(v0).toFixed(1)} dB, `
    + `per-cell sigma ${Math.sqrt(sig2).toFixed(1)} dB `
    + `-> predictive ${Math.sqrt(v0 + sig2).toFixed(1)} dB`);
  check('predictive is broader than the point estimates',
        Math.sqrt(v0 + sig2) > Math.sqrt(v0) ? 0 : 1, 0, 0);
  // A percentile layer moves the whole distribution instead of widening it.
  const q20 = percentileSNR(snr, c.sd, meta.sd_scale, 0.8416, new Float32Array(c.n));
  let vq = 0, mq = 0;
  for (let i = 0; i < c.n; i++) mq += q20[i] / c.n;
  for (let i = 0; i < c.n; i++) vq += (q20[i] - mq) ** 2 / c.n;
  check('the percentile layer shifts but does not broaden',
        Math.sqrt(vq), Math.sqrt(v0), 0.05);
}

// --- monotonicity sanity ----------------------------------------------------
const c = load('antarctic');
const snrA = new Float32Array(c.n), snrB = new Float32Array(c.n);
basalSNR(scalars(base), base, c.thk, c.mu, snrA);
basalSNR(scalars({ ...base, payload_power_W: 620 }), { ...base, payload_power_W: 620 }, c.thk, c.mu, snrB);
let mono = true;
for (let i = 0; i < c.n; i += 997) if (snrB[i] < snrA[i]) mono = false;
check('more power never lowers basal SNR', mono ? 0 : 1, 0, 0);

console.log(fail ? `\n${fail} FAILURES` : '\nall checks passed');
process.exit(fail ? 1 : 0);
