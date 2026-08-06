// Simple single-colour vector marks for tagging presets by platform type.
// Each is a self-contained <svg> drawn in currentColor on a 24×24 grid, so it
// takes the colour and size of whatever it sits in.

const wrap = (body, extra = '') =>
  `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" ${extra}>${body}</svg>`;

export const ICONS = {
  // Fixed-wing aircraft, plan view, nose up.
  aircraft: wrap(`<path fill="currentColor" d="M12 1.8c.9 0 1.45 1.25 1.54 3l.09 2.03 7.62 4.4v1.98l-7.6-2.2.03 3.63 2.55 1.78v1.63L12 16.98l-4.23 1.07v-1.63l2.55-1.78.03-3.63-7.6 2.2v-1.98l7.62-4.4.09-2.03c.09-1.75.64-3 1.54-3z"/>`),

  // Airship, side view, nose right: envelope, tail fins, gondola.
  airship: wrap(`<ellipse cx="11.4" cy="10.8" rx="8.6" ry="4.3" fill="currentColor"/>
<path fill="currentColor" d="M4.6 7.9 1 5.9v9.8l3.6-2z"/>
<rect x="8.4" y="14.6" width="6" height="2.7" rx="1.35" fill="currentColor"/>`),

  // Satellite: bus, two solar wings, dish on a mast.
  satellite: wrap(`<rect x="9.5" y="9.4" width="5" height="7" rx="1" fill="currentColor"/>
<rect x="1.6" y="11" width="7.2" height="3.8" rx=".6" fill="currentColor"/>
<rect x="15.2" y="11" width="7.2" height="3.8" rx=".6" fill="currentColor"/>
<path d="M12 9.4V6.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
<circle cx="12" cy="4.6" r="2" fill="currentColor"/>`),

  // Sliders, for the hand-tuned "custom" state.
  sliders: wrap(`<path d="M3 6.5h9M17.5 6.5H21M3 12h4.5M12.5 12H21M3 17.5h12M20 17.5h1"
  stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>
<circle cx="14.8" cy="6.5" r="2.1" fill="currentColor"/>
<circle cx="10" cy="12" r="2.1" fill="currentColor"/>
<circle cx="17.5" cy="17.5" r="2.1" fill="currentColor"/>`),
};
