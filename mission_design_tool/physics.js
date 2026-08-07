// Link budget for a nadir-pointing ice-penetrating radar sounder.
//
// Derived from reference/UAV IPR Link Budget.ipynb (scalar chain) and
// reference/Antarctica Expected SNR Map.ipynb (per-cell terms). selftest.mjs
// reproduces both notebook cases exactly, pinning the two auto rules that now
// deliberately differ (see NOTEBOOK there). The presets are separate designs and
// are not expected to match the notebooks.
//
// The rules for what the tool picks when the user leaves a field on "auto"
// live in auto.js; the plausibility rules live in warnings.js. This file is
// only the budget itself.

import { availableTxPower, resolve } from './auto.js';
import { WEIGHTING_LOSS_dB, sidelobeLevel } from './sidelobes.js';

export const C = 299792458;
export const K_B = 1.380649e-23;

const db = (x) => 10 * Math.log10(x);

/**
 * Scalar link budget. `p` holds every user parameter; `ov` holds overrides
 * (in SI units) for the auto fields — any key present wins over the auto rule.
 */
export function scalars(p, ov = {}) {
  const n_ice = Math.sqrt(p.epsilon_r);
  const lambda = C / p.frequency_Hz;

  const twtt_surface = (2 * p.altitude_m) / C;
  const twtt_bed_max = (2 * p.max_ice_thickness_m * n_ice) / C + twtt_surface;

  // Standard unfocused-SAR aperture, sqrt(lambda*h) — the along-track span over
  // which the returns stay within a quarter-wave of flat phase, and the default
  // azimuth aperture. (The first Fresnel zone *diameter* is sqrt(2*lambda*h);
  // this is the more conservative of the two and matches the reference notebook.)
  const coherent_aperture_m = Math.sqrt(p.altitude_m * lambda);

  // Resolved in dependency order; each auto rule reads only what precedes it.
  const x = { n_ice, lambda, twtt_surface, twtt_bed_max, coherent_aperture_m };
  const pulse_length_s = x.pulse_length_s = resolve('pulse_length_s', p, x, ov);
  // Duration of the echo burst: the spread of returns through the ice column,
  // smeared by the transmitted pulse. This, not the whole round trip, is what
  // the pulse repetition interval has to accommodate.
  const echo_window_s = x.echo_window_s = twtt_bed_max - twtt_surface + pulse_length_s;
  const pri_s = x.pri_s = resolve('pri_s', p, x, ov);
  const duty_cycle = x.duty_cycle = pulse_length_s / pri_s;

  const max_tx_W = availableTxPower(p, x);
  const tx_power_W = x.tx_power_W = resolve('tx_power_W', p, x, ov);
  const tx_power_dBm = db(tx_power_W / 1e-3);

  const spreading_surface_dB = db(lambda ** 2 / (4 * Math.PI * p.altitude_m) ** 2);
  const surface_return_dBm =
    tx_power_dBm + p.gain_tx_dBi + p.gain_rx_dBi + spreading_surface_dB +
    p.surface_reflectivity_dB - p.system_loss_dB;

  // T_sys = T_antenna + T0(F - 1). Multiplying the antenna temperature by the
  // noise factor instead would only be right when T_antenna is exactly T0.
  const sky_temp_K = x.sky_temp_K = skyTemperature(p.frequency_Hz);
  x.antenna_floor_K = T_ANTENNA_FLOOR;
  const noise_temp_K = x.noise_temp_K = resolve('noise_temp_K', p, x, ov);
  const noise_factor = 10 ** (p.noise_figure_dB / 10);
  const system_temp_K = noise_temp_K + T0_REF * (noise_factor - 1);
  const noise_dBm = db((K_B * system_temp_K * p.bandwidth_Hz) / 1e-3);

  // Coherent along-track summation over the chosen aperture. A zero-length
  // aperture means no azimuth processing at all, not one pulse.
  const azimuth_distance_m = x.azimuth_distance_m = resolve('azimuth_distance_m', p, x, ov);
  const integration_time_s = azimuth_distance_m / p.velocity_ms;
  const pulses_integrated = Math.max(0, Math.floor(integration_time_s / pri_s));
  const azimuth_gain_dB = pulses_integrated >= 1 ? db(pulses_integrated) : 0;

  // Receive weighting buys lower sidelobes at a fixed SNR cost, so fold that
  // cost into the compression gain and everything downstream follows. It only
  // applies where sidelobes are being accounted for.
  const weighting_loss_dB = p.overlap_mode === 'sidelobe'
    ? (WEIGHTING_LOSS_dB[p.sidelobe_window] ?? 0) : 0;
  const pulse_compression_gain_dB =
    db(p.bandwidth_Hz * pulse_length_s) - weighting_loss_dB;

  // Surface SNR *without* pulse-compression gain: that gain is common to the
  // surface and bed returns, so it cancels in RSSNR and is added back per-cell.
  const surface_snr_dB = surface_return_dBm + azimuth_gain_dB - noise_dBm;

  return {
    n_ice, lambda, twtt_surface, twtt_bed_max, pulse_length_s, pri_s,
    prf: 1 / pri_s, duty_cycle, max_tx_W, tx_power_W, tx_power_dBm,
    spreading_surface_dB, surface_return_dBm, noise_dBm, coherent_aperture_m,
    noise_factor, sky_temp_K, noise_temp_K, system_temp_K, echo_window_s,
    antenna_floor_K: T_ANTENNA_FLOOR,
    weighting_loss_dB,
    pulses_in_flight: Math.max(0, Math.floor(twtt_surface / pri_s)),
    azimuth_distance_m, integration_time_s, pulses_integrated, azimuth_gain_dB,
    pulse_compression_gain_dB, surface_snr_dB,
    distance_per_pulse_m: p.velocity_ms * pri_s,
  };
}

/**
 * Per-cell basal SNR, in place into `out` (Float32Array).
 * thk: Uint16Array of ice thickness [m]; mu: RSSNR [dB].
 *
 * Where the ice is thin enough that the bed echo returns before the transmitted
 * pulse has finished, the surface and bed returns overlap. Two treatments, per
 * `p.overlap_mode`:
 *
 *   'adaptive'  shorten the pulse per cell until the bed clears it, capped at
 *               the configured length. No overlap, less compression gain in
 *               thin ice. A system switching modes by region could do this.
 *   'sidelobe'  keep the pulse, and charge the bed for the compressed surface
 *               return's sidelobe pedestal at the bed's delay. Note this term
 *               scales with the surface, so transmit power cancels out of it
 *               entirely — only bandwidth or a shorter pulse helps.
 */
export function basalSNR(s, p, thk, mu, out) {
  const h = p.altitude_m, n = s.n_ice, B = p.bandwidth_Hz;
  const tau = s.pulse_length_s;
  const gpc = s.pulse_compression_gain_dB;
  const adaptive = p.overlap_mode === 'adaptive';
  const minTau = 1 / B;                 // a time-bandwidth product below 1 gains nothing
  const tb = B * tau;
  const surfacePeak = s.surface_snr_dB + gpc;

  for (let i = 0; i < thk.length; i++) {
    const d = thk[i];
    const spread = 20 * Math.log10(h / (h + d / n));
    const dt = (2 * d * n) / C;         // surface-to-bed delay
    let gain = gpc;

    if (adaptive) {
      gain = db(B * Math.min(Math.max(dt, minTau), tau));
    } else if (dt < tau) {
      const slnr = surfacePeak + sidelobeLevel(p.sidelobe_window, tb, dt / tau);
      gain -= db(1 + 10 ** (slnr / 10));
    }
    out[i] = s.surface_snr_dB + spread + gain - mu[i];
  }
  return out;
}

export const T0_REF = 290;   // reference temperature for noise figure [K]

/**
 * Floor on antenna temperature [K].
 *
 * A nadir sounder's main beam is on the ice, not the sky, and the antenna and
 * its feed contribute their own physical temperature through ohmic loss. So the
 * antenna cannot be much colder than the surface it looks at, whatever the
 * galactic background is doing — which matters above ~200 MHz, where the sky
 * term alone would fall to a few tens of kelvin.
 */
export const T_ANTENNA_FLOOR = 270;

/**
 * Sky noise temperature from the galactic background.
 *
 * ITU-R P.372 gives the median galactic noise figure for an omnidirectional
 * antenna as Fam = 52 - 23 log10(f/MHz) dB above kT0, over roughly 10 MHz-1 GHz;
 * the underlying spectrum is Cane (1979). At VHF this dominates a sounder's
 * noise floor: ~3700 K at 60 MHz against a receiver contribution of a few
 * hundred K. Floored at 20 K, below which cosmic and atmospheric terms take over.
 */
export function skyTemperature(frequency_Hz) {
  const fMHz = frequency_Hz / 1e6;
  if (!(fMHz > 0)) return T0_REF;
  return Math.max(20, T0_REF * 10 ** ((52 - 23 * Math.log10(fMHz)) / 10));
}

export const MU_EARTH = 3.986004418e14;   // geocentric gravitational constant [m^3/s^2]
export const R_EARTH = 6371e3;            // mean radius [m]

/**
 * Circular-orbit speeds at `altitude_m`, independent of inclination.
 *
 * `orbital` is the inertial speed; `ground` is how fast the nadir footprint
 * sweeps the surface, slower by the ratio of radii; `effective` is the
 * geometric mean conventionally used for spaceborne azimuth processing, since
 * the phase history follows range rate to a ground target while the aperture is
 * traced out by the platform. This tool's velocity is used for footprint dwell
 * time and along-track sample spacing, so `ground` is the relevant one.
 */
export function orbitalSpeeds(altitude_m) {
  const r = R_EARTH + altitude_m;
  const orbital = Math.sqrt(MU_EARTH / r);
  const ground = (orbital * R_EARTH) / r;
  return {
    orbital, ground,
    effective: Math.sqrt(orbital * ground),
    period_s: 2 * Math.PI * Math.sqrt(r ** 3 / MU_EARTH),
  };
}

/**
 * The overlap treatment's working, for one cell — what the hover readout shows.
 *
 * Mirrors the inner loop of basalSNR(); selftest.mjs asserts the two agree, so
 * the readout can never quietly describe different arithmetic than the map.
 */
export function overlapDetail(s, p, thickness_m) {
  const B = p.bandwidth_Hz, tau = s.pulse_length_s;
  const dt = (2 * thickness_m * s.n_ice) / C;

  if (p.overlap_mode === 'adaptive') {
    const pulse_length_s = Math.min(Math.max(dt, 1 / B), tau);
    return { mode: 'adaptive', pulse_length_s, bed_delay_s: dt,
             gain_dB: db(B * pulse_length_s), degradation_dB: 0 };
  }

  const overlaps = dt < tau;
  const sidelobe_dB = overlaps
    ? sidelobeLevel(p.sidelobe_window, B * tau, dt / tau) : -Infinity;
  const over_noise_dB = s.surface_snr_dB + s.pulse_compression_gain_dB + sidelobe_dB;
  return {
    mode: 'sidelobe', overlaps, bed_delay_s: dt, sidelobe_dB,
    sidelobe_dBm: s.noise_dBm + over_noise_dB,
    noise_dBm: s.noise_dBm,
    gain_dB: s.pulse_compression_gain_dB,
    degradation_dB: overlaps ? db(1 + 10 ** (over_noise_dB / 10)) : 0,
  };
}

/**
 * A lower-tail percentile of basal SNR, cell by cell.
 *
 * Basal SNR is a shifted, negated copy of RSSNR, so its 20th percentile is the
 * 80th percentile of RSSNR — the same conservative layer the repo's map_q80
 * figures show, at the same z = 0.8416.
 */
export function percentileSNR(snr, sd, sdScale, z, out) {
  for (let i = 0; i < snr.length; i++) out[i] = snr[i] - (z * sd[i]) / sdScale;
  return out;
}

/** Normal CDF (Abramowitz & Stegun 7.1.26 based erf). */
export function normCdf(z) {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989422804014327 * Math.exp((-z * z) / 2);
  const pr = d * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 +
    t * (-1.821255978 + t * 1.330274429))));
  return z > 0 ? 1 - pr : pr;
}

