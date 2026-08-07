import { AUTO_IDS, isOverridden } from './auto.js';
import { ICONS } from './icons.js';
import { CUSTOM_ID, DEFAULT_PRESET, PRESETS, VIEW_FIELDS, getPreset } from './presets.js';
import { scalars, basalSNR, normCdf, overlapDetail, percentileSNR } from './physics.js';
import { check, hasError } from './warnings.js';

const INFERNO = ['#000003','#030212','#0a0723','#140b36','#220b4c','#300a5c','#3e0966','#4b0c6b','#5a116d','#66156e','#731a6d','#801f6b','#8e2468','#9b2864','#a72d5f','#b43358','#c13a50','#cc4148','#d64a3f','#df5436','#e8612b','#ef6d21','#f47a16','#f8880c','#fb9906','#fba80d','#fbb71c','#f9c72f','#f5d948','#f1e864','#f2f485','#fcfea4'];
const SHEETS = ['antarctic', 'greenland'];
const Z20 = 0.8416;   // standard-normal 20th percentile; matches the repo's map_q80
// Map quantities, both in dB. `short` is how the coverage tiles name the layer.
const QUANTITY = {
  snr: { title: 'Median basal SNR', axis: 'Median basal SNR [dB]', short: 'median basal SNR' },
  p20: { title: '20th-percentile basal SNR', axis: '20th-percentile basal SNR [dB]',
         short: '20th-pct basal SNR' },
};
const SHEET_LABEL = { antarctic: 'Antarctica', greenland: 'Greenland' };

// Fail loudly and by name: a null here almost always means the HTML and the JS
// have drifted apart (or a stale copy of one of them is cached).
// Margin notes track the section they describe, and slide down only as far as
// needed to clear the note above — the layout Google Docs uses for comments.
const WIDE = window.matchMedia('(min-width: 1440px)');
const RAIL_GAP = 12;

function layoutRail() {
  const rail = $('rail');
  if (!WIDE.matches) {              // narrow: the rail is a plain stacked column
    for (const n of rail.children) n.style.top = '';
    return;
  }
  const railTop = rail.getBoundingClientRect().top + window.scrollY;
  let cursor = 0;                   // lowest y already claimed by a note above
  for (const note of rail.children) {
    if (note.classList.contains('is-empty')) continue;
    const anchor = $opt(note.dataset.anchor);
    const wanted = anchor
      ? anchor.getBoundingClientRect().top + window.scrollY - railTop : 0;
    const top = Math.max(wanted, cursor);
    note.style.top = `${Math.round(top)}px`;
    cursor = top + (note.offsetHeight || 0) + RAIL_GAP;
  }
}

let railQueued = false;
function scheduleRail() {
  if (railQueued) return;
  railQueued = true;
  requestAnimationFrame(() => { railQueued = false; layoutRail(); });
}

// Parameter controls live in the panel and in the "what to show" sidenote.
const PARAM_SELECTOR = '#panel input, #panel select, #view-box input, #view-box select,'
  + ' #dist-box input, #dist-box select';
const paramEls = () => document.querySelectorAll(PARAM_SELECTOR);
const DEFAULTS = {};   // each input's initial value, captured before anything changes

const $ = (id) => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`no element #${id} — index.html and app.js are out of sync `
    + '(try a hard reload: ctrl/cmd-shift-R)');
  return el;
};
const $opt = (id) => document.getElementById(id);
const rgb = (hex) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];

// ─────────────────────────── data loading ───────────────────────────

// The standalone build defines __INLINE_DATA__ (see build_standalone.py) so the
// same code runs from file://, where fetch() is blocked.
const INLINE = globalThis.__INLINE_DATA__ || null;

const loadMeta = async () =>
  INLINE ? INLINE.meta : (await fetch('data/meta.json')).json();

/** Bytes of a data asset, from the inline bundle or over HTTP, gunzipped. */
async function assetBuffer(inlineKey, url) {
  let buf;
  if (INLINE) {
    const bin = atob(INLINE[inlineKey]);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    buf = u8.buffer;
  } else {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    buf = await res.arrayBuffer();
  }
  const head = new Uint8Array(buf, 0, 2);
  if (head[0] === 0x1f && head[1] === 0x8b) {  // not already decoded by the server
    const ds = new DecompressionStream('gzip');
    buf = await new Response(new Blob([buf]).stream().pipeThrough(ds)).arrayBuffer();
  }
  return buf;
}

/** Coastline and grounding-line polylines, in raster pixel coordinates.
    Optional: the maps work without them, so a missing or stale file must not
    take the page down. */
async function loadCoast() {
  if (INLINE && !INLINE.coast) return {};
  try {
    return JSON.parse(new TextDecoder().decode(
      await assetBuffer('coast', 'data/coast.json.gz')));
  } catch (e) {
    console.warn('coastline unavailable:', e);
    return {};
  }
}

async function loadSheet(sheet, MU_SCALE) {
  const buf = await assetBuffer(sheet, `data/${sheet}.bin.gz`);
  // Widest fields first so every offset stays naturally aligned (see build_data.py).
  const n = buf.byteLength / 10;  // u32 + u16 + i16 + u8 + u8
  if (!Number.isInteger(n)) throw new Error(`${sheet}: ${buf.byteLength} bytes is not a whole number of records`);
  let o = 0;
  const idx = new Uint32Array(buf, o, n); o += 4 * n;
  const thk = new Uint16Array(buf, o, n); o += 2 * n;
  const mu16 = new Int16Array(buf, o, n); o += 2 * n;
  const sd = new Uint8Array(buf, o, n); o += n;
  const mask = new Uint8Array(buf, o, n);
  // RSSNR in dB, dequantised once at load.
  const mu = new Float32Array(n);
  for (let i = 0; i < n; i++) mu[i] = mu16[i] / MU_SCALE;
  return { n, idx, thk, mu16, mu, sd, mask };
}

// ─────────────────────────── parameter state ───────────────────────────

const DERIVED = AUTO_IDS;              // the fields auto.js can supply
const overrides = {};                  // always SI, whatever the box displays

const unitOf = (el) => (el.dataset.unit ? parseFloat(el.dataset.unit) : 1);

/**
 * A number as it should appear in an input box.
 *
 * Converting SI to display units divides by the unit, and that division is not
 * exact in binary floating point — 20e-6 / 1e-6 is 20.000000000000004. Every
 * path that writes a computed number into a box goes through here, so none of
 * them can leak a tail of digits.
 */
function formatForBox(v) {
  if (!Number.isFinite(v)) return '';
  if (v === 0) return '0';
  const abs = Math.abs(v);
  const digits = abs >= 100 ? 0 : abs >= 1 ? 2 : Math.max(2, 2 - Math.floor(Math.log10(abs)));
  return String(Number(v.toFixed(digits)));
}
const toSI = (el) => parseFloat(el.value) * unitOf(el);

/** Record an auto field's override, or clear it if the box is empty/unparseable.
    Storing NaN instead would leave a key behind and pin the page on Custom. */
function setOverride(el) {
  const v = toSI(el);
  if (Number.isFinite(v)) overrides[el.id] = v;
  else delete overrides[el.id];
}

function readParams() {
  const p = {};
  for (const el of paramEls()) {
    if (DERIVED.includes(el.id)) continue;
    if (el.type === 'checkbox') p[el.id] = el.checked;
    else if (el.tagName === 'SELECT') p[el.id] = el.value;
    else p[el.id] = toSI(el);
  }
  return p;
}

function writeDerived(s) {
  for (const id of DERIVED) {
    const el = $(id), lab = el.closest('label');
    const on = isOverridden(id, overrides);
    lab.classList.toggle('is-overridden', on);
    el.classList.toggle('overridden', on);
    const tag = lab.querySelector('.revert');
    if (tag) { tag.textContent = on ? 'revert to auto' : 'auto'; tag.disabled = !on; }
    // Never write into the box being typed in. Clearing an auto field leaves it
    // un-overridden, and refilling it there would overwrite the keystroke the
    // user is part-way through; the blur handler restores it on the way out.
    if (!on && el !== document.activeElement) {
      el.value = formatForBox(s[id] / unitOf(el));
    }
  }
}

// ─────────────────────────── rendering ───────────────────────────

function makeLUT(stops) {
  const lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = (i / 255) * (stops.length - 1);
    const a = rgb(stops[Math.floor(t)]), b = rgb(stops[Math.min(stops.length - 1, Math.ceil(t))]);
    const f = t - Math.floor(t);
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = a[c] + (b[c] - a[c]) * f;
  }
  return lut;
}
const LUT = makeLUT(INFERNO);

// Map outlines, both from the BedMachine mask: the coastline (mask > 0, the
// convention the repo's figures use) and the grounding line (grounded ice
// against floating ice). Each is stroked twice — a wide halo in the surface
// colour, then a thin line — so they read over the colour ramp, over blank
// ocean, and in either theme. The grounding line is thinner and more
// transparent, letting the halo lighten it so it reads as the secondary
// feature without becoming a second colour to define per theme.
function strokePath(ctx, segs, sx, sy, dpr, halo, ink, width, alpha) {
  if (!segs?.length) return;
  ctx.save();
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.beginPath();
  for (const seg of segs) {
    for (let i = 0; i < seg.length; i += 2) {
      // +0.5: contour coordinates index cell centres, canvas pixels their corners.
      const x = (seg[i] + 0.5) * sx, y = (seg[i + 1] + 0.5) * sy;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
  }
  ctx.strokeStyle = halo;
  ctx.globalAlpha = 0.7;
  ctx.lineWidth = (width + 2.1) * dpr;
  ctx.stroke();
  ctx.strokeStyle = ink;
  ctx.globalAlpha = alpha;
  ctx.lineWidth = width * dpr;
  ctx.stroke();
  ctx.restore();
}

function drawOutlines(ctx, outlines, nx, ny, w, h, dpr) {
  if (!outlines) return;
  const sx = w / nx, sy = h / ny;
  const css = getComputedStyle(document.body);
  const halo = css.getPropertyValue('--card').trim() || '#ffffff';
  const ink = css.getPropertyValue('--muted').trim() || '#666666';
  strokePath(ctx, outlines.coast, sx, sy, dpr, halo, ink, 1.1, 0.9);
  strokePath(ctx, outlines.grounding, sx, sy, dpr, halo, ink, 0.9, 0.45);
}

/** Small scale bar, bottom-left, at a round distance about a fifth of the map. */
function drawScaleBar(ctx, geom, nx, w, h, dpr) {
  const mPerPx = (Math.abs(geom.dx) * nx) / w;
  const target = w * 0.22;
  const choices = [50e3, 100e3, 200e3, 250e3, 500e3, 1000e3, 2000e3];
  const len = choices.reduce((a, b) =>
    Math.abs(b / mPerPx - target) < Math.abs(a / mPerPx - target) ? b : a);
  const px = len / mPerPx;
  const x0 = 12 * dpr, y = h - 14 * dpr, tick = 4 * dpr;
  const css = getComputedStyle(document.body);
  const ink = css.getPropertyValue('--ink').trim() || '#111111';
  const halo = css.getPropertyValue('--card').trim() || '#ffffff';

  ctx.save();
  ctx.lineCap = 'butt';
  ctx.beginPath();
  ctx.moveTo(x0, y - tick); ctx.lineTo(x0, y + tick);
  ctx.moveTo(x0, y); ctx.lineTo(x0 + px, y);
  ctx.moveTo(x0 + px, y - tick); ctx.lineTo(x0 + px, y + tick);
  ctx.strokeStyle = halo; ctx.globalAlpha = 0.8; ctx.lineWidth = 4 * dpr; ctx.stroke();
  ctx.strokeStyle = ink; ctx.globalAlpha = 1; ctx.lineWidth = 1.5 * dpr; ctx.stroke();

  const label = `${len / 1000} km`;
  ctx.font = `${11 * dpr}px system-ui, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'bottom';
  ctx.lineWidth = 3 * dpr;
  ctx.strokeStyle = halo;
  ctx.globalAlpha = 0.85;
  ctx.strokeText(label, x0 + px / 2, y - tick - 1 * dpr);
  ctx.globalAlpha = 1;
  ctx.fillStyle = ink;
  ctx.fillText(label, x0 + px / 2, y - tick - 1 * dpr);
  ctx.restore();
}

/**
 * Render one sheet. `size` is {cssWidth, scale}: on screen, scale is the device
 * pixel ratio; for export it is a larger multiplier, so the vector overlays
 * (coastline, grounding line, scale bar) are drawn at full resolution rather
 * than being upscaled from a screen-sized bitmap.
 */
function drawMap(canvas, sheetMeta, cells, values, vmin, vmax, lut, coast, size) {
  const [ny, nx] = sheetMeta.shape;
  const off = document.createElement('canvas');
  off.width = nx; off.height = ny;
  const img = off.getContext('2d').createImageData(nx, ny);
  const d = img.data;
  const under = [110, 110, 118];
  for (let i = 0; i < cells.n; i++) {
    const v = values[i];
    const o = cells.idx[i] * 4;
    if (v < vmin) { d[o] = under[0]; d[o + 1] = under[1]; d[o + 2] = under[2]; d[o + 3] = 255; continue; }
    const t = Math.min(255, Math.max(0, Math.round(((v - vmin) / (vmax - vmin)) * 255))) * 3;
    d[o] = lut[t]; d[o + 1] = lut[t + 1]; d[o + 2] = lut[t + 2]; d[o + 3] = 255;
  }
  off.getContext('2d').putImageData(img, 0, 0);

  const onScreen = !size;
  const cssWidth = (size && size.cssWidth) || canvas.clientWidth || 400;
  const dpr = (size && size.scale) || window.devicePixelRatio || 1;
  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round((cssWidth * dpr * ny) / nx);
  if (onScreen) canvas.style.height = `${Math.round((cssWidth * ny) / nx)}px`;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(off, 0, 0, canvas.width, canvas.height);
  drawOutlines(ctx, coast, nx, ny, canvas.width, canvas.height, dpr);
  drawScaleBar(ctx, sheetMeta, nx, canvas.width, canvas.height, dpr);
}

// simple axes-and-lines chart helper
function chart(canvas, { xlim, ylim, xlabel, ylabel, series, yfmt, vlines }) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 340, h = 200;
  canvas.width = w * dpr; canvas.height = h * dpr; canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const css = getComputedStyle(document.body);
  const muted = css.getPropertyValue('--muted').trim();
  const line = css.getPropertyValue('--line').trim();
  const L = 46, R = 8, T = 8, B = 34;
  const px = (x) => L + ((x - xlim[0]) / (xlim[1] - xlim[0])) * (w - L - R);
  const py = (y) => h - B - ((y - ylim[0]) / (ylim[1] - ylim[0])) * (h - T - B);

  ctx.font = '10px ui-monospace, monospace';
  ctx.strokeStyle = line; ctx.fillStyle = muted; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const yv = ylim[0] + (i / 4) * (ylim[1] - ylim[0]);
    ctx.beginPath(); ctx.moveTo(L, py(yv)); ctx.lineTo(w - R, py(yv)); ctx.stroke();
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(yfmt ? yfmt(yv) : yv.toFixed(2), L - 5, py(yv));
  }
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  for (const t of niceTicks(xlim[0], xlim[1])) {
    ctx.strokeStyle = line;
    ctx.beginPath(); ctx.moveTo(px(t.v), T); ctx.lineTo(px(t.v), h - B); ctx.stroke();
    ctx.fillStyle = muted;
    ctx.fillText(t.label, px(t.v), h - B + 6);
  }
  ctx.fillStyle = muted;
  ctx.fillText(xlabel, L + (w - L - R) / 2, h - 13);
  ctx.save(); ctx.translate(11, T + (h - T - B) / 2); ctx.rotate(-Math.PI / 2);
  ctx.textBaseline = 'top'; ctx.fillText(ylabel, 0, 0); ctx.restore();

  for (const s of series) {
    ctx.strokeStyle = s.color; ctx.lineWidth = 2;
    ctx.setLineDash(s.dash || []);
    ctx.beginPath();
    s.x.forEach((x, i) => (i ? ctx.lineTo(px(x), py(s.y[i])) : ctx.moveTo(px(x), py(s.y[i]))));
    ctx.stroke();
  }
  ctx.setLineDash([]);

  for (const v of vlines || []) {
    if (!Number.isFinite(v.x) || v.x < xlim[0] || v.x > xlim[1]) continue;
    const vx = px(v.x);
    ctx.strokeStyle = v.color;
    ctx.lineWidth = 1.4;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(vx, T); ctx.lineTo(vx, h - B); ctx.stroke();
    ctx.setLineDash([]);
    if (v.label) {
      const right = vx > L + (w - L - R) * 0.62;   // flip the label near the edge
      ctx.fillStyle = v.color;
      ctx.font = '10px system-ui';
      ctx.textAlign = right ? 'right' : 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(v.label, vx + (right ? -4 : 4), T + 2);
    }
  }

  ctx.strokeStyle = muted; ctx.strokeRect(L, T, w - L - R, h - T - B);
  return { px, py, L, R, T, B, w, h };
}

// ─────────────────────────── main ───────────────────────────

const state = { meta: null, data: {}, values: {}, snr: {}, coast: {}, snrRange: null, vlim: [0, 80] };
globalThis.__APP_STATE__ = state;   // debug/test hook: inspectable from the console

async function init() {
  state.meta = await loadMeta();
  for (const s of SHEETS) state.data[s] = await loadSheet(s, state.meta.mu_scale);
  state.coast = await loadCoast();
  for (const s of SHEETS) {
    state.values[s] = new Float32Array(state.data[s].n);
    state.snr[s] = new Float32Array(state.data[s].n);
  }
  for (const el of paramEls()) {
    DEFAULTS[el.id] = el.type === 'checkbox' ? el.checked : el.value;
  }
  renderInfo();
  // $('model-line').textContent =
  //   `RSSNR comes from the "${state.meta.model}" model (run ${state.meta.run_id}) on a `
  //   + `${state.meta.resolution_m / 1000} km grid.`;
  $('model-rmse').textContent = `${state.meta.cv_rmse_dB.toFixed(1)} dB`;
  $('footer-rmse').textContent = `${state.meta.cv_rmse_dB.toFixed(1)} dB`;

  paramEls().forEach((el) => {
    el.addEventListener('input', () => {
      if (DERIVED.includes(el.id)) setOverride(el);
      update();
    });
  });
  // Leaving a cleared auto field puts its auto value back.
  for (const id of DERIVED) {
    $(id).addEventListener('blur', () => update());
  }
  for (const id of DERIVED) {
    const tag = document.createElement('button');
    tag.type = 'button'; tag.className = 'revert'; tag.textContent = 'auto';
    tag.addEventListener('click', () => { delete overrides[id]; update(); });
    $(id).closest('label').appendChild(tag);
  }
  renderPresets();
  $('share').addEventListener('click', shareLink);
  $('export-png').addEventListener('click', exportPNG);
  // Open on the default preset, unless a shared link says otherwise.
  if (!location.hash.slice(1)) setPresetValues(DEFAULT_PRESET);
  applyURLState();
  window.addEventListener('resize', () => update());
  window.addEventListener('scroll', scheduleRail, { passive: true });
  WIDE.addEventListener?.('change', scheduleRail);
  // Opening a section moves its anchor; a note changing size moves its neighbour.
  for (const d of document.querySelectorAll('#panel > details')) {
    d.addEventListener('toggle', scheduleRail);
  }
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(scheduleRail);
    for (const el of [$('sec-intro'), $('sec-params'), $('rail'), ...$('rail').children]) ro.observe(el);
  }
  SHEETS.forEach(hookHover);

  update();
}

function renderPresets() {
  const host = $('presets');
  for (const preset of PRESETS) {
    // The custom card is a marker rather than a control, and it carries the
    // share button — which a <button> could not legally contain.
    const marker = !preset.values;
    const card = document.createElement(marker ? 'div' : 'button');
    if (!marker) card.type = 'button';
    card.className = 'preset' + (marker ? ' marker' : '');
    card.dataset.preset = preset.id;
    card.setAttribute('aria-pressed', 'false');
    card.innerHTML = `<span class="icon">${ICONS[preset.icon] || ''}</span>`
      + `<b>${preset.name}</b><span>${preset.note}</span>`;
    if (marker) {
      const share = document.createElement('button');
      share.type = 'button';
      share.id = 'share';
      share.className = 'btn sm';
      share.textContent = 'Copy link';
      card.append(share);
    } else {
      card.addEventListener('click', () => applyPreset(preset.id));
    }
    host.append(card);
  }
}

/**
 * What a preset implies for one control: its own value converted to the box's
 * display unit, or the page default where the preset is silent. Numbers are
 * unit-converted; strings (selects) and booleans (checkboxes) are not — dividing
 * those by a unit yields NaN, which silently breaks preset matching.
 *
 * Applying and matching both go through here, so they cannot drift apart.
 */
function expectedValue(preset, el) {
  const raw = preset.values[el.id];
  if (raw === undefined) return DEFAULTS[el.id];
  return typeof raw === 'number' ? raw / unitOf(el) : raw;
}

/** Set every design field from a preset; anything it omits returns to the page
    default. Returns false if `id` names no preset, so start-up can fall back. */
function setPresetValues(id) {
  const preset = getPreset(id);
  if (!preset?.values) return false;
  for (const k of DERIVED) delete overrides[k];   // every auto field back to auto first
  for (const el of paramEls()) {
    if (VIEW_FIELDS.includes(el.id)) continue;
    const want = expectedValue(preset, el);
    if (DERIVED.includes(el.id)) {
      // A preset may pin an auto field — the same thing as typing into the box.
      if (preset.values[el.id] !== undefined) {
        overrides[el.id] = preset.values[el.id];   // stored in SI, like any override
        el.value = formatForBox(want);
      }
      continue;
    }
    if (el.type === 'checkbox') el.checked = !!want;
    else el.value = typeof want === 'number' ? formatForBox(want) : want;
  }
  return true;
}

function applyPreset(id) {
  if (setPresetValues(id)) update();
}

/** Which preset the boxes currently describe — CUSTOM_ID if none matches exactly. */
function currentPresetId() {
  for (const preset of PRESETS) {
    if (preset.values && presetMatches(preset)) return preset.id;
  }
  return CUSTOM_ID;
}

function presetMatches(preset) {
  // The pinned auto fields must be exactly the ones this preset pins: an extra
  // override means the user has gone beyond the preset, a missing one means
  // they reverted something the preset set.
  const pinned = DERIVED.filter((id) => preset.values[id] !== undefined);
  const held = Object.keys(overrides);
  if (held.length !== pinned.length) return false;
  for (const id of pinned) {
    const want = preset.values[id];
    if (!(Math.abs(overrides[id] - want) <= 1e-9 * Math.max(1, Math.abs(want)))) return false;
  }
  for (const el of paramEls()) {
    if (VIEW_FIELDS.includes(el.id) || DERIVED.includes(el.id)) continue;
    const want = expectedValue(preset, el);
    if (el.type === 'checkbox') {
      if (el.checked !== !!want) return false;
    } else if (el.tagName === 'SELECT') {
      if (String(el.value) !== String(want)) return false;
    } else {
      const a = parseFloat(el.value), b = parseFloat(want);
      if (!(Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(b)))) return false;
    }
  }
  return true;
}

function markPresets(id) {
  for (const el of document.querySelectorAll('.preset')) {
    el.setAttribute('aria-pressed', String(el.dataset.preset === id));
  }
}

/** Provenance box: which model produced the RSSNR layer, and from what. */
function renderInfo() {
  const m = state.meta;
  const cells = Object.values(m.sheets).reduce((a, s) => a + s.n, 0);
  const sources = m.sources || [];
  $('info-box').innerHTML = `
    <h4>Where these numbers come from</h4>
    <p>
      See <a href="https://github.com/englacial/radar-return-statistics-postprocessing/" target="_blank">radar-return-statistics-postprocessing</a>
      on GitHub. Below are the details of the specific model loaded here for traceability.
      These values are dynamically fetched from the data source.
    </p>
    <dl>
      <dt>Model</dt><dd>${m.model}</dd>
      <dt>Run</dt><dd>${m.run_id}</dd>
      <dt>Trained</dt><dd>${(m.created_at || '').slice(0, 10)}</dd>
      <dt>Grid</dt><dd>${m.resolution_m / 1000} km · ${cells.toLocaleString()} cells</dd>
      <dt>CV RMSE</dt><dd>${m.cv_rmse_dB.toFixed(1)} dB</dd>
    </dl>
    <div class="src"><b>RSSNR observations</b>
      Reprocessed airborne sounding data over both ice sheets, fitted with a Bayesian
      model of required surface SNR.</div>
    ${sources.length ? `<div class="src"><b>Covariate datasets</b><ul>`
      + sources.map((x) => `<li>${x}</li>`).join('') + '</ul></div>' : ''}`;
}

// Key values echoed into each collapsed <summary>, so the panel stays readable
// without opening every section.
const SUMMARY = {
  platform: (p, s) => `${fmtSI(p.altitude_m, 'm')} · ${p.velocity_ms} m/s`,
  power: (p, s) => `${p.payload_power_W} W in · ${s.tx_power_W.toFixed(0)} W peak`,
  timing: (p, s) => `${(s.pulse_length_s * 1e6).toFixed(1)} µs · ${(s.prf / 1e3).toFixed(1)} kHz · `
                    + `${(s.duty_cycle * 100).toFixed(0)}%`,
  rf: (p, s) => `${(p.frequency_Hz / 1e6).toFixed(0)} MHz · ${(p.bandwidth_Hz / 1e6).toFixed(0)} MHz BW · `
                + `T_sys ${s.system_temp_K.toFixed(0)} K`,
  processing: (p, s) => (s.azimuth_distance_m > 0
                          ? `${s.azimuth_distance_m.toFixed(0)} m azimuth` : 'no azimuth gain')
                        + (p.overlap_mode === 'adaptive'
                            ? ' · adaptive pulse'
                            : ` · ${p.sidelobe_window} sidelobes`),
};

function fmtSI(v, unit) {
  return v >= 1000 ? `${(v / 1000).toFixed(v >= 1e5 ? 0 : 1)} k${unit}` : `${v} ${unit}`;
}

function renderSummaries(p, s, hits) {
  for (const det of document.querySelectorAll('#panel > details')) {
    const key = det.dataset.summary;
    const sum = det.querySelector('.sum');
    if (sum && SUMMARY[key]) {
      let text = '';
      try { text = SUMMARY[key](p, s); } catch { text = ''; }
      sum.textContent = text.includes('NaN') ? '' : text;   // a blank box shouldn't read "NaN"

    }
    // Badge the section holding each flagged field.
    det.querySelector('.badge')?.remove();
    const ids = [...det.querySelectorAll('input, select')].map((el) => el.id);
    const mine = hits.filter((h) => ids.includes(h.field));
    if (mine.length) {
      const level = hasError(mine) ? 'error' : 'warn';
      const b = document.createElement('span');
      b.className = `badge ${level}`;
      b.textContent = level === 'error' ? 'error' : `${mine.length}`;
      det.querySelector('summary').append(b);
    }
  }
}

function renderWarnings(hits) {
  for (const el of paramEls()) el.classList.remove('flag-warn', 'flag-error');
  for (const h of hits) $opt(h.field)?.classList.add(`flag-${h.level}`);

  const box = $('warnings');
  const bad = hasError(hits);
  box.classList.toggle('is-empty', hits.length === 0);
  box.classList.toggle('has-error', bad);
  box.innerHTML = hits.length === 0 ? '' :
    `<h4>${bad ? 'Invalid settings' : 'Worth checking'} (${hits.length})</h4>`
    + hits.map((h) => `<div class="msg ${h.level}">`
        + `<span class="tag">${h.level === 'error' ? 'invalid' : 'check'}</span>`
        + `<span class="body">${h.message}</span></div>`).join('');
}

function update() {
  const p = readParams();
  // A cleared or half-typed box yields NaN. Treat it like any other invalid
  // input: flag it, keep the last good render, and stop.
  const blank = Object.entries(p)
    .filter(([, v]) => typeof v === 'number' && !Number.isFinite(v))
    .map(([field]) => ({ id: `blank:${field}`, level: 'error', field,
                         message: 'This field is empty or not a number.' }));
  const s = scalars(p, overrides);
  const hits = [...blank, ...(blank.length ? [] : check(p, s))];

  writeDerived(s);
  $('sidelobe_window').disabled = p.overlap_mode !== 'sidelobe';
  markPresets(currentPresetId());
  renderSummaries(p, s, hits);
  renderWarnings(hits);
  $('results').classList.toggle('suppressed', hasError(hits));
  scheduleRail();          // the warning box just changed size; relayout either way
  if (hasError(hits)) return;

  renderBudget(p, s);

  const q = p.quantity;
  for (const sheet of SHEETS) {
    const c = state.data[sheet];
    basalSNR(s, p, c.thk, c.mu, state.snr[sheet]);
    if (q === 'p20') percentileSNR(state.snr[sheet], c.sd, state.meta.sd_scale, Z20, state.values[sheet]);
    else state.values[sheet].set(state.snr[sheet]);
  }

  const [vmin, vmax] = state.vlim = [0, 80];
  $('map-title').textContent = QUANTITY[q].title;
  for (const sheet of SHEETS) {
    drawMap($(`map-${sheet}`), state.meta.sheets[sheet], state.data[sheet],
            state.values[sheet], vmin, vmax, LUT, state.coast[sheet]);
  }
  drawColorbar(vmin, vmax, LUT);
  renderDistributions(p);
  renderCoverage(p);
  scheduleRail();
}

function renderBudget(p, s) {
  const row = (k, v, key) => `<div class="${key ? 'key' : ''}"><span>${k}</span><span>${v}</span></div>`;
  $('budget').innerHTML = [
    row('Pulse length', `${(s.pulse_length_s * 1e6).toFixed(1)} µs`),
    row('PRF', `${(s.prf).toFixed(0)} Hz`),
    row('Duty cycle', `${(s.duty_cycle * 100).toFixed(1)} %`),
    row('Peak Tx power', `${s.tx_power_W.toFixed(0)} W / ${s.tx_power_dBm.toFixed(1)} dBm`),
    row('Spreading to surface', `${s.spreading_surface_dB.toFixed(1)} dB`),
    row('Surface return', `${s.surface_return_dBm.toFixed(1)} dBm`),
    row('Antenna / system temp', `${s.noise_temp_K.toFixed(0)} / ${s.system_temp_K.toFixed(0)} K`),
    row('Noise power', `${s.noise_dBm.toFixed(1)} dBm`),
    row('Echo window', `${(s.echo_window_s * 1e6).toFixed(1)} µs`),
    row('Pulses in flight', `${s.pulses_in_flight}`),
    row('Pulse compression gain', `${s.pulse_compression_gain_dB.toFixed(1)} dB`
        + (s.weighting_loss_dB ? ` (−${s.weighting_loss_dB} weighting)` : '')),
    row('Coherent aperture', `${s.coherent_aperture_m.toFixed(0)} m`),
    row('Pulses integrated', `${s.pulses_integrated.toLocaleString()}`),
    row('Azimuth gain', `${s.azimuth_gain_dB.toFixed(1)} dB`),
    row('Distance per pulse', `${s.distance_per_pulse_m.toFixed(2)} m`),
    row('Surface SNR (pre-compression)', `${s.surface_snr_dB.toFixed(1)} dB`, true),
  ].join('');
}

function drawColorbar(vmin, vmax, lut) {
  const c = $('cbar'), dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth || 600;
  c.width = w * dpr; c.height = 14 * dpr;
  const ctx = c.getContext('2d');
  for (let i = 0; i < c.width; i++) {
    const t = Math.round((i / c.width) * 255) * 3;
    ctx.fillStyle = `rgb(${lut[t]},${lut[t + 1]},${lut[t + 2]})`;
    ctx.fillRect(i, 0, 1, c.height);
  }
  const ticks = [];
  for (let i = 0; i <= 4; i++) {
    const v = vmin + (i / 4) * (vmax - vmin);
    ticks.push(`<span>${v.toFixed(0)}${i === 4 ? ' dB' : ''}</span>`);
  }
  $('cbar-ticks').innerHTML = ticks.join('');
}

/** Tick positions at a round step inside [lo, hi] — chosen independently of the
    limits, so the axis can stay tight to the data and still label tidily. */
function niceTicks(lo, hi, count = 6) {
  const raw = Math.max((hi - lo) / count, 1e-9);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw) ?? 10 * mag;
  const out = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-6; t += step) {
    out.push({ v: t, label: step < 1 ? t.toFixed(1) : String(Math.round(t)) });
  }
  return out;
}

/**
 * X range for the distribution plots: the full spread of basal SNR across both
 * sheets, always including the target line. Fixed limits used to dump every
 * value beyond them into the end bin, which read as a tall spike that was an
 * artefact of the axis rather than anything in the data.
 */
function snrRange(target, predictive = false) {
  let lo = Infinity, hi = -Infinity, sigma = 0;
  for (const sheet of SHEETS) {
    const v = predictive ? state.snr[sheet] : summarised(sheet);
    for (let i = 0; i < v.length; i++) {
      if (v[i] < lo) lo = v[i];
      if (v[i] > hi) hi = v[i];
    }
    if (predictive) {
      const sd = state.data[sheet].sd;
      for (let i = 0; i < sd.length; i++) if (sd[i] > sigma) sigma = sd[i];
      sigma /= state.meta.sd_scale;
    }
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [-20, 100];
  // 3 sigma keeps the mass dropped off the ends below ~0.1%.
  lo -= 3 * sigma;
  hi += 3 * sigma;
  if (Number.isFinite(target)) { lo = Math.min(lo, target); hi = Math.max(hi, target); }
  const pad = Math.max(hi - lo, 1) * 0.03;
  return [lo - pad, hi + pad];
}

/** Gaussian kernel, `sigma` expressed in bins, normalised to unit mass. */
function gaussianKernel(sigma) {
  const half = Math.max(1, Math.ceil(4 * sigma));
  const k = new Float64Array(2 * half + 1);
  let sum = 0;
  for (let i = -half; i <= half; i++) {
    const w = Math.exp((-0.5 * i * i) / (sigma * sigma));
    k[i + half] = w;
    sum += w;
  }
  for (let i = 0; i < k.length; i++) k[i] /= sum;
  return { k, half };
}

/**
 * Posterior predictive histogram: the area-weighted mixture of every cell's
 * N(mu_i, sigma_i), rather than a histogram of the point estimates.
 *
 * A percentile layer translates the distribution; the predictive broadens it,
 * so the two are not interchangeable. Because `sd` is stored quantised, only a
 * handful of distinct sigmas occur — each group is histogrammed and convolved
 * with its own kernel, which makes this the exact mixture rather than an
 * approximation. Mass falling outside the axis is dropped rather than piled
 * into the end bins; snrRange() widens the axis so that loss is negligible.
 */
function predictiveHistogram(values, sd, sdScale, keep, lo, hi, nbins) {
  const width = (hi - lo) / nbins;
  const groups = new Map();
  let n = 0;
  for (let i = 0; i < values.length; i++) {
    if (keep && !keep(i)) continue;
    n++;
    let b = Math.floor((values[i] - lo) / width);
    b = Math.min(nbins - 1, Math.max(0, b));
    let g = groups.get(sd[i]);
    if (g === undefined) groups.set(sd[i], (g = new Float64Array(nbins)));
    g[b]++;
  }
  const h = new Float64Array(nbins);
  for (const [byte, g] of groups) {
    const { k, half } = gaussianKernel(byte / sdScale / width);
    for (let b = 0; b < nbins; b++) {
      const c = g[b];
      if (!c) continue;
      for (let j = -half; j <= half; j++) {
        const t = b + j;
        if (t >= 0 && t < nbins) h[t] += c * k[j + half];
      }
    }
  }
  return { h, n };
}

function histogram(values, keep, lo, hi, nbins) {
  const h = new Float64Array(nbins);
  let n = 0;
  for (let i = 0; i < values.length; i++) {
    if (keep && !keep(i)) continue;
    n++;
    let b = Math.floor(((values[i] - lo) / (hi - lo)) * nbins);
    b = Math.min(nbins - 1, Math.max(0, b));
    h[b]++;
  }
  return { h, n };
}

/**
 * Floating/grounded split, applied to Antarctica only: Greenland has ~100
 * floating cells at 5 km, far too few to say anything about.
 */
function splitGroups(sheet, p, c) {
  if (!p.split_floating || sheet !== 'antarctic') return [['all', null, []]];
  return [['grounded', (i) => c.mask[i] !== 3, []],
          ['floating', (i) => c.mask[i] === 3, [5, 3]]];
}

const distAxisLabel = (p) => (p.posterior_predictive
  ? 'Basal SNR [dB] — posterior predictive'
  : QUANTITY[p.quantity].axis);

/** The layer the distributions and coverage stats describe. */
const summarised = (sheet) => state.values[sheet];

function renderDistributions(p) {
  const nbins = 160;
  const pred = p.posterior_predictive;
  const [lo, hi] = state.snrRange = snrRange(p.target_snr_dB, pred);
  const css = getComputedStyle(document.body);
  const col = { antarctic: css.getPropertyValue('--ant').trim(), greenland: css.getPropertyValue('--grl').trim() };
  const target = {
    x: p.target_snr_dB,
    label: `target ${p.target_snr_dB} dB`,
    color: css.getPropertyValue('--accent').trim(),
  };
  const hSeries = [], cSeries = [], legend = [];
  let ymax = 0;

  for (const sheet of SHEETS) {
    const c = state.data[sheet];
    // The predictive always broadens the median layer: convolving the 20th
    // percentile instead would count the same uncertainty twice.
    const v = pred ? state.snr[sheet] : summarised(sheet);
    for (const [name, keep, dash] of splitGroups(sheet, p, c)) {
      const { h, n } = pred
        ? predictiveHistogram(v, c.sd, state.meta.sd_scale, keep, lo, hi, nbins)
        : histogram(v, keep, lo, hi, nbins);
      if (n < 50) continue;
      const x = [], y = [], cx = [], cy = [];
      let acc = 0;
      for (let b = 0; b < nbins; b++) {
        const xv = lo + ((b + 0.5) / nbins) * (hi - lo);
        x.push(xv); y.push((h[b] / n) * 100);
      }
      for (let b = nbins - 1; b >= 0; b--) {
        acc += h[b] / n;
        cx.unshift(lo + (b / nbins) * (hi - lo)); cy.unshift(acc * 100);
      }
      ymax = Math.max(ymax, ...y);
      hSeries.push({ x, y, color: col[sheet], dash });
      cSeries.push({ x: cx, y: cy, color: col[sheet], dash });
      legend.push(`<span><i style="border-top:2.5px ${dash.length ? 'dashed' : 'solid'} ${col[sheet]}"></i>${SHEET_LABEL[sheet]}${name === 'all' ? '' : ' — ' + name}</span>`);
    }
  }
  chart($('hist'), { xlim: [lo, hi], ylim: [0, Math.max(ymax * 1.1, 1e-3)],
                     xlabel: distAxisLabel(p), ylabel: '% of area per bin',
                     series: hSeries, yfmt: (v) => v.toFixed(1), vlines: [target] });
  chart($('cdf'), { xlim: [lo, hi], ylim: [0, 100], xlabel: distAxisLabel(p),
                    ylabel: '% of area above', series: cSeries,
                    yfmt: (v) => v.toFixed(0), vlines: [target] });
  $('legend').innerHTML = legend.join('');
}

function renderCoverage(p) {
  const t = p.target_snr_dB;
  const out = [];
  for (const sheet of SHEETS) {
    const c = state.data[sheet];
    const pred = p.posterior_predictive;
    const v = pred ? state.snr[sheet] : summarised(sheet);
    const groups = [['all', null], ...splitGroups(sheet, p, c).filter(([nm]) => nm !== 'all')
                                                              .map(([nm, k]) => [nm, k])];
    for (const [name, keep] of groups) {
      let n = 0, ok = 0;
      for (let i = 0; i < c.n; i++) {
        if (keep && !keep(i)) continue;
        n++;
        // Under the predictive the answer is the expected area fraction,
        // mean_i P(SNR_i >= target), not a count of point estimates.
        ok += pred ? normCdf((v[i] - t) / (c.sd[i] / state.meta.sd_scale)) : (v[i] >= t ? 1 : 0);
      }
      if (!n) continue;
      const cls = sheet === 'antarctic' ? 'ant' : 'grl';
      out.push(`<div class="stat ${cls}"><div class="big">${((ok / n) * 100).toFixed(1)}%</div>
        <div class="lbl">${SHEET_LABEL[sheet]}${name === 'all' ? '' : ' · ' + name} area with<br>${p.posterior_predictive ? 'basal SNR' : QUANTITY[p.quantity].short} ≥ ${t} dB
        <br><span style="opacity:.7">${(n * (state.meta.resolution_m / 1000) ** 2 / 1e6).toFixed(2)} M km² total</span></div></div>`);
    }
  }
  $('coverage').innerHTML = out.join('');
}

function hookHover(sheet) {
  const canvas = $(`map-${sheet}`);
  const lookup = new Map();
  canvas.addEventListener('mousemove', (e) => {
    const g = state.meta.sheets[sheet], c = state.data[sheet];
    if (!lookup.size) for (let i = 0; i < c.n; i++) lookup.set(c.idx[i], i);
    const r = canvas.getBoundingClientRect();
    const fx = Math.min(g.shape[1] - 1, Math.floor(((e.clientX - r.left) / r.width) * g.shape[1]));
    const fy = Math.min(g.shape[0] - 1, Math.floor(((e.clientY - r.top) / r.height) * g.shape[0]));
    const i = lookup.get(fy * g.shape[1] + fx);
    if (i === undefined) { $('hover').textContent = ''; return; }
    const p = readParams();
    const d = overlapDetail(scalars(p, overrides), p, c.thk[i]);
    const detail = d.mode === 'adaptive'
      ? `pulse ${(d.pulse_length_s * 1e6).toFixed(1)} µs`
      : (d.overlaps
          ? `sidelobe ${d.sidelobe_dBm.toFixed(0)} / noise ${d.noise_dBm.toFixed(0)} dBm`
          : 'bed clears the pulse');
    $('hover').textContent =
      `${(c.thk[i]).toFixed(0)} m ice · RSSNR ${(c.mu16[i] / state.meta.mu_scale).toFixed(0)}` +
      `±${(c.sd[i] / state.meta.sd_scale).toFixed(0)} dB · ` +
      `${detail} · SNR ${state.values[sheet][i].toFixed(1)} dB`;
  });
  canvas.addEventListener('mouseleave', () => ($('hover').textContent = ''));
}

// ─────────────────────────── URL state + exports ───────────────────────────

function applyURLState() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (![...h].length) return;
  for (const [k, v] of h) {
    const el = $opt(k);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = v === '1';
    else el.value = v;
    if (DERIVED.includes(k)) setOverride(el);
  }
}

function shareLink() {
  const h = new URLSearchParams();
  for (const el of paramEls()) {
    if (DERIVED.includes(el.id) && overrides[el.id] === undefined) continue;
    h.set(el.id, el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value);
  }
  const url = `${location.origin}${location.pathname}#${h}`;
  history.replaceState(null, '', '#' + h);
  navigator.clipboard?.writeText(url);
  $('share').textContent = 'Copied ✓';
  setTimeout(() => ($('share').textContent = 'Copy link'), 1500);
}

function download(name, blob) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** Device pixels per CSS pixel in the exported figure. 4x puts Antarctica at
    roughly twice its native 5 km raster, so the outlines stay crisp. */
const EXPORT_SCALE = 4;

function exportPNG() {
  const p = readParams();
  const [vmin, vmax] = state.vlim;
  // Re-render at export resolution instead of upscaling the screen canvases.
  const maps = SHEETS.map((sheet) => {
    const off = document.createElement('canvas');
    drawMap(off, state.meta.sheets[sheet], state.data[sheet], state.values[sheet],
            vmin, vmax, LUT, state.coast[sheet],
            { cssWidth: $(`map-${sheet}`).clientWidth || 400, scale: EXPORT_SCALE });
    return off;
  });

  const pad = 12 * EXPORT_SCALE;
  const head = 30 * EXPORT_SCALE;
  const barH = 13 * EXPORT_SCALE;
  const foot = barH + 26 * EXPORT_SCALE;
  const mapH = Math.max(...maps.map((m) => m.height));

  const c = document.createElement('canvas');
  c.width = maps.reduce((a, m) => a + m.width, 0) + pad * (maps.length + 1);
  c.height = head + pad + mapH + pad + foot;
  const ctx = c.getContext('2d');
  // No background fill: the figure is transparent apart from what is drawn, so
  // it drops onto a slide or page of any colour.
  const INK = '#16181d';

  ctx.fillStyle = INK;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
  ctx.font = `600 ${18 * EXPORT_SCALE}px system-ui, sans-serif`;
  ctx.fillText(`${$('map-title').textContent} — surface SNR `
    + `${scalars(p, overrides).surface_snr_dB.toFixed(1)} dB`, pad, head - 8 * EXPORT_SCALE);

  let x = pad;
  for (const m of maps) { ctx.drawImage(m, x, head + pad); x += m.width + pad; }

  // Colour bar, centred beneath the maps.
  const barW = Math.round(c.width * 0.45);
  const barX = Math.round((c.width - barW) / 2);
  const barY = head + pad + mapH + pad;
  for (let i = 0; i < barW; i++) {
    const t = Math.min(255, Math.round((i / (barW - 1)) * 255)) * 3;
    ctx.fillStyle = `rgb(${LUT[t]},${LUT[t + 1]},${LUT[t + 2]})`;
    ctx.fillRect(barX + i, barY, 1, barH);
  }
  ctx.strokeStyle = INK;
  ctx.lineWidth = Math.max(1, EXPORT_SCALE * 0.4);
  ctx.strokeRect(barX, barY, barW, barH);

  ctx.fillStyle = INK;
  ctx.textBaseline = 'top';
  ctx.font = `${11 * EXPORT_SCALE}px system-ui, sans-serif`;
  for (let i = 0; i <= 4; i++) {
    const v = vmin + (i / 4) * (vmax - vmin);
    ctx.textAlign = i === 0 ? 'left' : i === 4 ? 'right' : 'center';
    ctx.fillText(`${v.toFixed(0)}${i === 4 ? ' dB' : ''}`,
                 barX + (i / 4) * barW, barY + barH + 4 * EXPORT_SCALE);
  }
  c.toBlob((b) => download('basal_snr_map.png', b));
}


init().catch((e) => { document.body.innerHTML = `<div id="loading">Failed to load: ${e}</div>`; });
