// Plausibility and validity checks on the parameter set.
//
// Every check is a pure predicate over the user parameters `p` and the
// resolved link budget `s`. No DOM, no side effects — so the whole rule set
// can be read in one sitting and exercised from selftest.mjs.
//
//   level 'error' — the arithmetic or the underlying model is not meaningful;
//                   the results are suppressed.
//   level 'warn'  — physically possible, but an assumption is being stretched
//                   or the value is outside what the model was fit on.
//
// `field` names the input the message is about, so the UI can badge the
// section it lives in and highlight the box.

import { availableTxPower } from './auto.js';
import { orbitalSpeeds } from './physics.js';

const dB = (x) => 10 * Math.log10(x);

export const CHECKS = [
  // ── hard errors ──────────────────────────────────────────────────────────
  { id: 'altitude', level: 'error', field: 'altitude_m',
    when: (p) => !(p.altitude_m > 0),
    msg: () => 'Altitude must be greater than zero.' },

  { id: 'velocity', level: 'error', field: 'velocity_ms',
    when: (p) => !(p.velocity_ms > 0),
    msg: () => 'Ground velocity must be greater than zero.' },

  { id: 'efficiency', level: 'error', field: 'pa_efficiency',
    when: (p) => !(p.pa_efficiency > 0 && p.pa_efficiency <= 1),
    msg: () => 'Amplifier efficiency must be between 0 and 1.' },

  { id: 'efficiency_ab', level: 'warn', field: 'pa_efficiency',
    when: (p) => (p.pa_efficiency > 0.5),
    msg: () => 'Amplifiers with efficiency >50% likely have significant non-linearity which is not considered here.' },

  { id: 'payload_power', level: 'error', field: 'payload_power_W',
    when: (p) => !(p.payload_power_W > 0),
    msg: () => 'Payload power must be greater than zero.' },

  { id: 'duty', level: 'error', field: 'pulse_length_s',
    when: (p, s) => s.duty_cycle >= 1,
    msg: (p, s) => `Pulse length (${(s.pulse_length_s * 1e6).toFixed(1)} µs) is at or beyond the `
      + `pulse repetition interval (${(s.pri_s * 1e6).toFixed(1)} µs) — the radar would never stop `
      + 'transmitting.' },

  { id: 'frequency', level: 'error', field: 'frequency_Hz',
    when: (p) => !(p.frequency_Hz > 0),
    msg: () => 'Center frequency must be greater than zero.' },

  { id: 'bandwidth', level: 'error', field: 'bandwidth_Hz',
    when: (p) => !(p.bandwidth_Hz > 0),
    msg: () => 'Bandwidth must be greater than zero.' },

  { id: 'bandwidth_vs_carrier', level: 'error', field: 'bandwidth_Hz',
    when: (p) => p.bandwidth_Hz >= 2 * p.frequency_Hz,
    msg: (p) => `A ${(p.bandwidth_Hz / 1e6).toFixed(0)} MHz band around `
      + `${(p.frequency_Hz / 1e6).toFixed(0)} MHz extends below zero frequency.` },

  { id: 'reflectivity', level: 'error', field: 'surface_reflectivity_dB',
    when: (p) => p.surface_reflectivity_dB > 0,
    msg: () => 'Surface reflectivity cannot exceed 0 dB.' },

  { id: 'noise_temp', level: 'error', field: 'noise_temp_K',
    when: (p, s) => !(s.noise_temp_K > 0),
    msg: () => 'Noise temperature must be greater than zero.' },

  { id: 'noise_figure', level: 'error', field: 'noise_figure_dB',
    when: (p) => p.noise_figure_dB < 0,
    msg: () => 'A negative noise figure would mean the receiver adds no noise at all.' },

  { id: 'permittivity', level: 'error', field: 'epsilon_r',
    when: (p) => !(p.epsilon_r >= 1),
    msg: () => 'Relative permittivity must be at least 1.' },



  // ── warnings ─────────────────────────────────────────────────────────────

  { id: 'tx_blinding', level: 'warn', field: 'pulse_length_s',
    when: (p, s) => s.pulse_length_s > s.twtt_surface,
    msg: (p, s) => `The pulse (${(s.pulse_length_s * 1e6).toFixed(1)} µs) is still going out when the `
      + `surface echo returns (${(s.twtt_surface * 1e6).toFixed(1)} µs). The top of the record may be.`
      + `saturated or have a higher noise floor, which this tool does not model.`},

  { id: 'range_ambiguity', level: 'warn', field: 'pri_s',
    when: (p, s) => s.pri_s < s.echo_window_s + s.pulse_length_s,
    msg: (p, s) => `The echo burst spans ${(s.echo_window_s * 1e6).toFixed(0)} µs but only `
      + `${((s.pri_s - s.pulse_length_s) * 1e6).toFixed(0)} µs is free between transmissions, so returns `
      + 'from the ice column will land on top of an outgoing pulse. Pulses may be in flight '
      + 'simultaneously, but their echo windows must not collide with a transmission.' },

  { id: 'over_power', level: 'warn', field: 'tx_power_W',
    when: (p, s) => s.tx_power_W > availableTxPower(p, s) * 1.001,
    msg: (p, s) => `${s.tx_power_W.toFixed(0)} W peak exceeds the `
      + `${availableTxPower(p, s).toFixed(0)} W this payload budget supports at `
      + `${(s.duty_cycle * 100).toFixed(0)}% duty cycle and ${(p.pa_efficiency * 100).toFixed(0)}% `
      + 'amplifier efficiency.' },

  { id: 'azimuth_aliasing', level: 'warn', field: 'velocity_ms',
    when: (p, s) => s.azimuth_distance_m > 0 && s.distance_per_pulse_m > s.lambda / 4,
    msg: (p, s) => `The platform moves ${s.distance_per_pulse_m.toFixed(1)} m between pulses, more than `
      + `λ/4 (${(s.lambda / 4).toFixed(1)} m). The Doppler history is undersampled, so the `
      + `${s.azimuth_gain_dB.toFixed(0)} dB of coherent azimuth gain is likely optimistic.` },

  { id: 'few_pulses', level: 'warn', field: 'azimuth_distance_m',
    when: (p, s) => s.azimuth_distance_m > 0 && s.pulses_integrated < 10,
    msg: (p, s) => `Only ${s.pulses_integrated} pulse(s) fall inside the ${s.azimuth_distance_m.toFixed(0)} m `
      + 'integration distance, so there is little coherent gain to be had at this speed and PRF.' },

  { id: 'azimuth_too_long', level: 'warn', field: 'azimuth_distance_m',
    when: (p, s) => s.azimuth_distance_m > s.coherent_aperture_m * 1.001,
    msg: (p, s) => `Integrating over ${s.azimuth_distance_m.toFixed(0)} m exceeds the unfocused aperture `
      + `√(λh) = ${s.coherent_aperture_m.toFixed(0)} m. Past that the returns no longer add in phase on their own, `
      + 'so realising this gain needs focused SAR processing — and the flat-phase assumption behind this '
      + 'budget no longer holds.' },


  // Above ~100 km nothing sustains flight, so altitude and speed are no longer
  // independent: a circular orbit fixes both. Compared against the ground-track
  // speed, which is what the Fresnel-zone dwell and along-track sampling use.
  { id: 'orbital_velocity', level: 'warn', field: 'velocity_ms',
    when: (p) => p.altitude_m > 100e3 && p.velocity_ms > 0
      && Math.abs(p.velocity_ms - orbitalSpeeds(p.altitude_m).ground)
         / orbitalSpeeds(p.altitude_m).ground > 0.05,
    msg: (p) => {
      const o = orbitalSpeeds(p.altitude_m);
      const inertial = Math.abs(p.velocity_ms - o.orbital) / o.orbital < 0.05;
      return `At ${(p.altitude_m / 1e3).toFixed(0)} km the platform must be orbiting, and a `
        + `circular orbit there sweeps its nadir footprint across the ground at `
        + `${o.ground.toFixed(0)} m/s (${o.orbital.toFixed(0)} m/s inertial, `
        + `${(o.period_s / 60).toFixed(0)} min period). `
        + (inertial
            ? 'The value entered is the inertial orbital speed — azimuth gain and along-track '
              + 'sampling both depend on how fast the footprint crosses the ground, which is slower '
              + 'by the ratio of radii.'
            : `${p.velocity_ms.toFixed(0)} m/s is not consistent with that altitude.`);
    } },

  // The insight that is easy to miss: the sidelobe pedestal scales with the
  // surface return, so it cancels transmit power exactly.
  { id: 'pulse_exceeds_ice', level: 'warn', field: 'pulse_length_s',
    when: (p, s) => p.overlap_mode === 'sidelobe'
      && s.pulse_length_s > s.twtt_bed_max - s.twtt_surface,
    msg: (p, s) => `The ${(s.pulse_length_s * 1e6).toFixed(0)} µs pulse is longer than the `
      + `${((s.twtt_bed_max - s.twtt_surface) * 1e6).toFixed(0)} µs two-way delay through `
      + `${(p.max_ice_thickness_m / 1e3).toFixed(1)} km of ice, so the bed sits inside the transmitted `
      + 'pulse everywhere and the map is limited by surface sidelobes rather than by noise. '
      + 'Transmit power cancels out of that limit — only more bandwidth, a shorter pulse, or '
      + 'receive weighting will help.' },

  { id: 'low_altitude', level: 'warn', field: 'altitude_m',
    when: (p) => p.altitude_m > 0 && p.altitude_m < 80,
    msg: (p) => `At ${p.altitude_m.toFixed(0)} m the platform is close enough to the surface that the `
      + 'geometric spreading correction dominates and the far-field assumptions may break down.' },

  { id: 'band', level: 'warn', field: 'frequency_Hz',
    when: (p) => p.frequency_Hz > 0 && (p.frequency_Hz < 1e6 || p.frequency_Hz > 1e9),
    msg: (p) => `${(p.frequency_Hz / 1e6).toFixed(0)} MHz is outside the ~1–1000 MHz range where `
      + 'ice-penetrating sounders normally operate and where the attenuation behaviour behind '
      + 'this model applies.' },

  { id: 'permittivity_odd', level: 'warn', field: 'epsilon_r',
    when: (p) => p.epsilon_r >= 1 && (p.epsilon_r < 2.8 || p.epsilon_r > 3.5),
    msg: (p) => `ε_r = ${p.epsilon_r} is well away from the ~3.17 usually assumed for glacial ice.` },

  { id: 'gain_high', level: 'warn', field: 'gain_tx_dBi',
    when: (p) => Math.max(p.gain_tx_dBi, p.gain_rx_dBi) > 20,
    msg: (p, s) => `${Math.max(p.gain_tx_dBi, p.gain_rx_dBi).toFixed(0)} dBi at `
      + `λ = ${s.lambda.toFixed(1)} m implies a very large aperture.` },

  { id: 'noise_figure_odd', level: 'warn', field: 'noise_figure_dB',
    when: (p) => p.noise_figure_dB >= 0 && (p.noise_figure_dB < 0.5 || p.noise_figure_dB > 20),
    msg: (p) => `A ${p.noise_figure_dB} dB noise figure is outside the range typical of real `
      + 'sounder receivers.' },

  { id: 'reflectivity_low', level: 'warn', field: 'surface_reflectivity_dB',
    when: (p) => p.surface_reflectivity_dB < -25,
    msg: (p) => `${p.surface_reflectivity_dB} dB is very low for an air–ice interface, which is `
      + 'usually around −10 dB.' },

  { id: 'no_azimuth', level: 'warn', field: 'azimuth_distance_m',
    when: (p, s) => !(s.azimuth_distance_m > 0),
    msg: () => 'Coherent azimuth integration is off, so the budget carries no along-track processing '
      + 'gain at all — this is a deliberately pessimistic bound.' },

  { id: 'noise_bandwidth', level: 'warn', field: 'bandwidth_Hz',
    when: (p, s) => p.bandwidth_Hz > 0 && dB(p.bandwidth_Hz * s.pulse_length_s) < 0,
    msg: (p, s) => `Time–bandwidth product is ${(p.bandwidth_Hz * s.pulse_length_s).toFixed(2)}, below 1.` },
];

/** Every check that fires, errors first. */
export function check(p, s) {
  const hits = [];
  for (const c of CHECKS) {
    let fired = false;
    try {
      fired = !!c.when(p, s);
    } catch {
      fired = false;   // a check must never be able to take the page down
    }
    if (fired) hits.push({ id: c.id, level: c.level, field: c.field, message: c.msg(p, s) });
  }
  return hits.sort((a, b) => (a.level === b.level ? 0 : a.level === 'error' ? -1 : 1));
}

export const hasError = (hits) => hits.some((h) => h.level === 'error');
