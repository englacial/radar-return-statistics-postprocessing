// Presets — edit or add entries here; the buttons are generated from this list.
//
//   id          stable key, also what appears in a shared URL
//   name        button title
//   icon        a key from icons.js ('aircraft' | 'airship' | 'satellite' | 'sliders')
//   note        one or two sentences under the title
//   values      parameters to set, in SI units (metres, seconds, hertz, watts).
//               Anything omitted returns to the page's own default, so a preset
//               always describes a complete configuration. Use `null` for the
//               "custom" marker, which sets nothing.
//
// Fields left on "auto" — transmit power, pulse length, PRI, azimuth distance,
// antenna temperature — are derived by auto.js. A preset does not normally set
// them, but naming one here pins it, exactly as typing into the box would:
//
//     tx_power_W: 800,        // peak transmit power, W (not derived from duty cycle)
//     pulse_length_s: 70e-6,  // SI: seconds, though the box shows microseconds
//
// The field then shows as overridden, with the "revert to auto" control active.
//
// A preset stays selected only while every parameter still matches it; change
// anything and the selection falls back to Custom.

export const PRESETS = [
  {
    id: 'uav',
    name: 'Low-altitude UAV',
    icon: 'aircraft',
    note: '300 MHz, 200 m AGL, 100 W transmit power',
    values: {
      altitude_m: 200,
      velocity_ms: 20,
      payload_power_W: 80,
      pa_efficiency: 0.5,
      tx_power_W: 100,
      frequency_Hz: 300e6,
      bandwidth_Hz: 50e6,
      gain_tx_dBi: 2,
      gain_rx_dBi: 2,
    },
  },
  {
    id: 'haps',
    name: 'Stratospheric UAV',
    icon: 'airship',
    note: '60 MHz, 14 km altitude (polar stratosphere), 100 W transmit power',
    values: {
      altitude_m: 14000,
      velocity_ms: 20,
      payload_power_W: 200,
      pa_efficiency: 0.5,
      pulse_length_s: 20e-6,
      tx_power_W: 100,
      frequency_Hz: 60e6,
      bandwidth_Hz: 15e6,
      gain_tx_dBi: 8,
      gain_rx_dBi: 8,
      overlap_mode: 'adaptive',   // shortens its pulse rather than eating surface sidelobes
    },
  },
  {
    id: 'orbital',
    name: 'Orbital sounder',
    icon: 'satellite',
    note: '450 MHz (P-band), 500 km orbit, 300 W transmit power',
    values: {
      altitude_m: 500000,
      velocity_ms: 7062,   // ground-track speed for a circular orbit at 500 km
      payload_power_W: 300,
      pa_efficiency: 0.5,
      pulse_length_s: 10e-6,
      frequency_Hz: 450e6,
      bandwidth_Hz: 10e6,
      gain_tx_dBi: 10,
      gain_rx_dBi: 10,
    },
  },
  {
    id: 'custom',
    name: 'Custom',
    icon: 'sliders',
    note: 'Selects when you change anything below. Copy a link to share:',
    values: null,
  },
];

export const CUSTOM_ID = 'custom';

/** Which preset the page opens on. A shared URL still wins over this. */
export const DEFAULT_PRESET = 'haps';

/** Parameters that are view options, not part of the design a preset describes. */
export const VIEW_FIELDS = ['quantity', 'target_snr_dB', 'split_floating',
                            'posterior_predictive'];

export const getPreset = (id) => PRESETS.find((p) => p.id === id) || null;
