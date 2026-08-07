// Validates the link budget against real OPR seasons.
//
//   uv run python -c "..."               # writes /tmp/season_obs.json
//   node mission_design_tool/validate_seasons.mjs
//
// Sources
//   radar settings   opr_params/rds_param_<season>.xlsx (prf, Tpd, f0, f1, adc_gains)
//   presums          opr/matlab/missions/create_configs_<season>_Greenland_P3*.m
//   power + antenna  OPR wiki "<season> RDS: system_dB" (Processing-Notes)
//   observed         outputs/greenland/greenland.parquet (CSARP_standard)
//
// The comparison is basal SNR against the deep-record noise floor. Surface SNR
// in the product is NOT comparable: at ~3 us surface delay the pre-surface
// window is transmit leakage, sitting 20-86 dB above the deep noise floor.
//
// Three corrections relative to the first pass, all documented:
//   1. MCoRDS splits the PRF across the waveform playlist by presums, so the
//      bed's 10 us waveform transmits at PRF*presums(bed)/sum(presums), not PRF.
//   2. The airborne form of the radar equation (Haynes 2018 eq 21) sits 6.02 dB
//      below the Fresnel-zone form (eq 18) the tool defaults to.
//   3. Window and taper losses are folded into the system loss term.

import { readFileSync } from 'node:fs';
import { scalars } from './physics.js';

const obs = JSON.parse(readFileSync('/tmp/season_obs.json'));
const db = (x) => 10 * Math.log10(x);

// fast-time Hanning mismatch 1.77 + transmit Tukey(0.2) 0.45 + receive array
// Hanning taper over 7 channels 1.18
const WINDOW_LOSS_dB = 3.4;
const FEED_LOSS_dB = 3.0;      // assumed; OPR quotes ~3 dB Tx + 3 dB Rx for the GV

const P3 = {
  velocity_ms: 130,
  gain_tx_dBi: 6,              // OPR system_dB: "each transmit element 6 dB"
  gain_rx_dBi: 6,              // "each receive element 6 dB" (single channel)
  noise_figure_dB: 3,          // CReSIS link budget uses F = 2 linear
  system_loss_dB: FEED_LOSS_dB + WINDOW_LOSS_dB,
  surface_reflectivity_dB: -11,
  epsilon_r: 3.17,
  max_ice_thickness_m: 3400,
  overlap_mode: 'sidelobe',
  sidelobe_window: 'rect',
  noise_temp_K: 270,
  radar_equation: 'infinite',  // Haynes 2018 eq 21, the airborne form
};

// tx_W is per-element power and n_tx the elements excited together, so the
// transmit term is Pt*(sum w)^2, exactly as OPR's system_dB writes it.
const SEASONS = {
  '2017_Greenland_P3': {
    system: 'MCoRDS 3 on P3, survey mode', tx_W: 150, n_tx: 7, n_rx: 15,
    f0: 180e6, f1: 210e6, prf: 12000,
    presums: [3, 3, 29], tpd: [1e-6, 3e-6, 10e-6], bed_wf: 2,
  },
  '2019_Greenland_P3': {
    system: 'MCoRDS 3 on P3, image mode', tx_W: 337, n_tx: 3, n_rx: 7,
    f0: 180e6, f1: 210e6, prf: 12000,
    presums: [3, 3, 3, 3, 11, 11], tpd: [1e-6, 1e-6, 3e-6, 3e-6, 10e-6, 10e-6], bed_wf: 4,
  },
};

console.log('Radar equation: Haynes 2018 eq 21 (airborne, infinite mirror)');
console.log(`System loss: ${FEED_LOSS_dB} dB feed + ${WINDOW_LOSS_dB} dB window/taper\n`);

for (const [season, cfg] of Object.entries(SEASONS)) {
  const o = obs[season];
  if (!o) continue;

  const B = cfg.f1 - cfg.f0;
  const sumPresums = cfg.presums.reduce((a, b) => a + b, 0);
  const bedRate = (cfg.prf * cfg.presums[cfg.bed_wf]) / sumPresums;
  const tauBed = cfg.tpd[cfg.bed_wf];

  const p = { ...P3, altitude_m: o.agl_m, frequency_Hz: (cfg.f0 + cfg.f1) / 2, bandwidth_Hz: B };
  const s = scalars(p, {
    tx_power_W: cfg.tx_W * cfg.n_tx ** 2,   // Pt * (sum of enabled tx weights)^2
    pulse_length_s: tauBed,
    pri_s: 1 / bedRate,                     // the bed waveform's own pulse rate
  });

  const arrayGain = db(cfg.n_rx);           // coherent receive-channel combining
  const spread = 20 * Math.log10(p.altitude_m / (p.altitude_m + o.thickness_m / s.n_ice));
  const pred = s.surface_snr_dB + s.pulse_compression_gain_dB + arrayGain + spread - o.rssnr_dB;
  const gap = pred - o.bed_snr_dB;

  console.log(`=== ${season}  (${cfg.system})`);
  console.log(`    presums ${JSON.stringify(cfg.presums)} of ${sumPresums} -> bed waveform `
    + `${(tauBed * 1e6).toFixed(0)} us at ${bedRate.toFixed(0)} Hz `
    + `(the full ${cfg.prf} Hz would over-credit by ${db(cfg.prf / bedRate).toFixed(1)} dB)`);
  console.log(`    Pt ${cfg.tx_W} W x ${cfg.n_tx} elements -> ${s.tx_power_dBm.toFixed(1)} dBm effective`);
  console.log(`    spreading ${s.spreading_surface_dB.toFixed(1)}   azimuth ${s.azimuth_gain_dB.toFixed(1)} `
    + `(${s.pulses_integrated} pulses)   pulse comp ${s.pulse_compression_gain_dB.toFixed(1)}   `
    + `array ${arrayGain.toFixed(1)} (${cfg.n_rx} ch)`);
  console.log(`    basal SNR:  predicted ${pred.toFixed(1)}   observed ${o.bed_snr_dB.toFixed(1)}`
    + ` [${o.bed_snr_p25.toFixed(0)}-${o.bed_snr_p75.toFixed(0)}]   `
    + `>>> ${gap >= 0 ? '+' : ''}${gap.toFixed(1)} dB\n`);
}
