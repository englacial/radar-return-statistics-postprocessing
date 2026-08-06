// Auto modes for the derived parameters.
//
// Each entry is a pure function of the user's parameters `p` and the values
// already resolved earlier in the same pass `x`. Nothing here touches the DOM
// or the link budget itself — physics.js calls resolve() and takes what it
// gets, so the rule for "what does the tool pick when the user doesn't" lives
// in exactly one auditable place.
//
// Order matters: pulse_length_s → pri_s → (duty cycle) → tx_power_W, with
// azimuth_distance_m reading the Fresnel geometry computed alongside them.
// physics.js resolves them in that order, and each rule only reads what is
// already on `x`.

/** Transmit power the payload budget can sustain at this duty cycle [W]. */
export function availableTxPower(p, x) {
  return (p.payload_power_W / x.duty_cycle) * p.pa_efficiency;
}

export const AUTO = {
  pulse_length_s: {
    label: 'Pulse length',
    why: 'longest pulse that ends before the surface echo arrives',
    of: (p, x) => {
      const t = x.twtt_surface;
      // Below 5 us the overlap is unavoidable; hold a 1 us floor.
      if (t < 5e-6) return 1e-6;
      // Otherwise round down to a 5 or 10 us increment that clears the echo.
      if (t < 20e-6) return Math.floor(t * 0.2e6) / 0.2e6;
      return Math.floor(t * 0.1e6) / 0.1e6;
    },
  },

  pri_s: {
    label: 'Pulse repetition interval',
    why: 'the echo burst fits between transmissions, with pulses allowed in flight',
    of: (p, x) => {
      // Only the echo *window* has to fit in the inter-pulse gap. Several pulses
      // may be in flight at once, as an orbital sounder must do — waiting out the
      // full round trip would cap a 500 km orbit near 150 Hz.
      const gap = Math.ceil(x.echo_window_s * 0.1e6) / 0.1e6;   // round up to 10 us
      return gap + x.pulse_length_s;
    },
  },

  azimuth_distance_m: {
    label: 'Azimuth integration distance',
    why: 'the unfocused aperture sqrt(lambda*h), over which returns add coherently',
    of: (p, x) => x.coherent_aperture_m,
  },

  noise_temp_K: {
    label: 'Antenna noise temperature',
    why: 'the galactic sky background at this frequency (ITU-R P.372)',
    of: (p, x) => Math.round(x.sky_temp_K),
  },

  tx_power_W: {
    label: 'Peak transmit power',
    why: 'all available payload power through the amplifier at this duty cycle',
    of: (p, x) => {
      const max = availableTxPower(p, x);
      if (!(max > 0)) return 0;
      // Round down to an increment a real amplifier would be specified at,
      // chosen from the value being rounded. Choosing it from payload power
      // instead made the result fall as available power rose across a decade.
      const step = max > 100 ? 100 : max > 10 ? 10 : max > 1 ? 1 : 0;
      return step ? Math.floor(max / step) * step : max;
    },
  },

};

export const AUTO_IDS = Object.keys(AUTO);

/**
 * The user's override for `id` if they set one, otherwise the auto value.
 * Overrides are always in SI units, whatever unit the input box displays.
 */
export function resolve(id, p, x, overrides = {}) {
  const o = overrides[id];
  if (o !== undefined && o !== null && o !== '' && Number.isFinite(+o)) return +o;
  return AUTO[id].of(p, x);
}

export const isOverridden = (id, overrides = {}) => {
  const o = overrides[id];
  return o !== undefined && o !== null && o !== '' && Number.isFinite(+o);
};
