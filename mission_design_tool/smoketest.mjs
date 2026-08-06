// Runs app.js against a minimal fake DOM, so a broken first paint shows up as a
// stack trace here instead of a blank page in a browser.
//
//   node mission_design_tool/smoketest.mjs
//
// The DOM is only as complete as app.js needs: the selectors it actually uses,
// a canvas that records nothing, and fetch backed by data/ on disk. It is not a
// browser — it catches missing elements, bad ordering, and exceptions, not
// layout or paint.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { DEFAULT_PRESET, PRESETS as PRESET_LIST, getPreset } from './presets.js';

const here = dirname(fileURLToPath(import.meta.url));
// Which build to exercise: the module page by default, or the bundled artifact.
const target = process.argv[2] || 'index.html';

// ── parse index.html with Python's stdlib parser (no npm dependency) ────────
const PY = `
import html.parser, json, sys
VOID = {'input','br','img','meta','link','hr','source'}
class P(html.parser.HTMLParser):
    def __init__(s):
        super().__init__(convert_charrefs=True)
        s.root = {'tag': '#root', 'attrs': {}, 'children': [], 'text': ''}
        s.stack = [s.root]
    def handle_starttag(s, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'children': [], 'text': ''}
        s.stack[-1]['children'].append(node)
        if tag not in VOID: s.stack.append(node)
    def handle_startendtag(s, tag, attrs):
        s.stack[-1]['children'].append({'tag': tag, 'attrs': dict(attrs), 'children': [], 'text': ''})
    def handle_endtag(s, tag):
        for i in range(len(s.stack) - 1, 0, -1):
            if s.stack[i]['tag'] == tag:
                del s.stack[i:]
                break
    def handle_data(s, d):
        if d.strip(): s.stack[-1]['text'] += d.strip()
p = P(); p.feed(sys.stdin.read())
json.dump(p.root, sys.stdout)
`;
const tree = JSON.parse(execFileSync('python3', ['-c', PY], {
  input: readFileSync(join(here, target), 'utf8'), maxBuffer: 256e6,
}));

// ── elements ───────────────────────────────────────────────────────────────
const byId = new Map();
let elementCount = 0;

function makeEl(node, parent) {
  const attrs = node.attrs || {};
  const el = {
    tagName: (node.tag || 'div').toUpperCase(),
    parentNode: parent,
    children: [],
    textContent: node.text || '',
    innerHTML: '',
    id: attrs.id || '',
    className: attrs.class || '',
    type: attrs.type || (node.tag === 'select' ? 'select-one' : 'text'),
    value: attrs.value ?? '',
    checked: 'checked' in attrs,
    disabled: false,
    style: {},
    width: 300, height: 150,
    clientWidth: 600, clientHeight: 300,
    // Fake geometry: each element claims a height, and a page position that the
    // test sets explicitly. Enough to exercise the margin-note layout.
    offsetHeight: 120, offsetTop: 0, _pageTop: 0,
    dataset: Object.fromEntries(Object.entries(attrs)
      .filter(([k]) => k.startsWith('data-'))
      .map(([k, v]) => [k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase()), v === '' ? '' : v])),
    _attrs: attrs,
    _listeners: {},
  };
  elementCount++;
  el.classList = {
    add: (...c) => { for (const x of c) if (!el.className.split(/\s+/).includes(x)) el.className += ` ${x}`; },
    remove: (...c) => { el.className = el.className.split(/\s+/).filter((x) => !c.includes(x)).join(' '); },
    toggle: (c, on) => (on ? el.classList.add(c) : el.classList.remove(c)),
    contains: (c) => el.className.split(/\s+/).includes(c),
  };
  el.setAttribute = (k, v) => { el._attrs[k] = String(v); };
  el.getAttribute = (k) => el._attrs[k] ?? null;
  el.addEventListener = (ev, fn) => { (el._listeners[ev] ||= []).push(fn); };
  el.dispatch = (ev, arg = {}) => (el._listeners[ev] || []).forEach((f) => f({ target: el, ...arg }));
  el.append = el.appendChild = (child) => { child.parentNode = el; el.children.push(child); return child; };
  el.remove = () => {
    const sibs = el.parentNode?.children;
    if (sibs) sibs.splice(sibs.indexOf(el), 1);
  };
  el.getBoundingClientRect = () => ({
    left: 0, top: el._pageTop, width: el.clientWidth, height: el.clientHeight,
  });
  el.getContext = () => ctx2d(el);
  el.toBlob = (cb) => cb({ size: 0, type: 'image/png' });
  el.closest = (sel) => {
    for (let n = el; n; n = n.parentNode) if (n.tagName && matches(n, sel)) return n;
    return null;
  };
  el.querySelector = (sel) => query(el, sel)[0] || null;
  el.querySelectorAll = (sel) => query(el, sel);
  el.showModal = () => {};
  el.click = () => el.dispatch('click');
  el.focus = () => { globalThis.document.activeElement = el; };
  el.blur = () => {
    if (globalThis.document.activeElement === el) globalThis.document.activeElement = null;
    el.dispatch('blur');
  };

  if (el.id) {
    if (byId.has(el.id)) throw new Error(`duplicate id in ${target}: #${el.id}`);
    byId.set(el.id, el);
  }
  for (const c of node.children || []) el.children.push(makeEl(c, el));
  // A <select> with no explicit value reports its first option's, as in a browser.
  if (el.tagName === 'SELECT' && !el.value) {
    el.value = el.children.find((c) => c.tagName === 'OPTION')?._attrs.value ?? '';
  }
  return el;
}

// ── a selector engine covering exactly what app.js uses ─────────────────────
function matches(el, sel) {
  return sel.split(',').some((part) => {
    const t = part.trim();
    if (!t) return false;
    const m = t.match(/^([a-zA-Z]+)?(#[\w-]+)?((?:\.[\w-]+)*)$/);
    if (!m) return false;
    const [, tag, id, cls] = m;
    if (tag && el.tagName !== tag.toUpperCase()) return false;
    if (id && el.id !== id.slice(1)) return false;
    for (const c of (cls || '').split('.').filter(Boolean)) if (!el.classList.contains(c)) return false;
    return true;
  });
}

function descendants(el, out = []) {
  for (const c of el.children) { out.push(c); descendants(c, out); }
  return out;
}

function query(root, selector) {
  const hits = [];
  for (const group of selector.split(',')) {
    const parts = group.trim().split(/\s+/);
    let scope = [root];
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (p === '>') {                       // child combinator
        const next = parts[++i];
        scope = scope.flatMap((s) => s.children.filter((c) => matches(c, next)));
      } else {
        scope = scope.flatMap((s) => descendants(s).filter((c) => matches(c, p)));
      }
    }
    for (const el of scope) if (!hits.includes(el)) hits.push(el);
  }
  return hits;
}

// ── canvas / window stubs ──────────────────────────────────────────────────
const ctx2d = (canvas) => new Proxy({
  canvas,
  createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h }),
  measureText: () => ({ width: 0 }),
  getPropertyValue: () => '#888888',
}, {
  get: (t, k) => (k in t ? t[k] : typeof k === 'string' ? () => {} : undefined),
  set: () => true,
});

const root = makeEl(tree, null);
const body = query(root, 'body')[0] || root;

globalThis.document = {
  body,
  activeElement: null,
  // Elements created at runtime set .id after construction, so fall back to a
  // tree search rather than only consulting the parse-time index.
  getElementById: (id) => byId.get(id)
    || descendants(root).find((e) => e.id === id) || null,
  querySelector: (s) => query(root, s)[0] || null,
  querySelectorAll: (s) => query(root, s),
  createElement: (tag) => makeEl({ tag, attrs: {}, children: [], text: '' }, null),
};
globalThis.window = {
  devicePixelRatio: 2,
  scrollY: 0,
  addEventListener: () => {},
  getComputedStyle: () => ({ getPropertyValue: () => '#888888' }),
  matchMedia: () => ({ matches: true, addEventListener: () => {} }),   // wide layout
  ResizeObserver: undefined,
};
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.getComputedStyle = window.getComputedStyle;
globalThis.location = { origin: 'http://localhost', pathname: '/', hash: '' };
globalThis.history = { replaceState: () => {} };
// node defines navigator as a getter-only global
Object.defineProperty(globalThis, 'navigator', {
  value: { clipboard: { writeText: () => {} } }, configurable: true,
});
globalThis.URL.createObjectURL = () => 'blob:stub';
globalThis.URL.revokeObjectURL = () => {};

// fetch backed by the data directory
globalThis.fetch = async (url) => {
  const path = join(here, String(url).replace(/^\.?\//, ''));
  const buf = readFileSync(path);
  return {
    ok: true,
    status: 200,
    json: async () => JSON.parse(buf.toString()),
    arrayBuffer: async () => buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength),
  };
};

// ── run ────────────────────────────────────────────────────────────────────
let failed = 0;
const ok = (cond, label) => {
  if (!cond) failed++;
  console.log(`${cond ? 'ok  ' : 'FAIL'} ${label}`);
};

console.log(`parsed ${target}: ${elementCount} elements, ${byId.size} with ids`);

// The module page loads app.js; the standalone bundle carries inline scripts,
// which run in shared global scope exactly as the browser would run them.
const inline = descendants(root)
  .filter((el) => el.tagName === 'SCRIPT' && !el._attrs.src)
  .map((el) => el.textContent);
const isBundle = inline.length > 0;
if (isBundle) {
  console.log(`running ${inline.length} inline script(s) from the bundle`);
  for (const src of inline) (0, eval)(src);
} else {
  await import('./app.js');
}
await new Promise((r) => setTimeout(r, 200));   // let init()'s awaits settle

ok(document.getElementById('budget').innerHTML.includes('Surface SNR'), 'link budget rendered');
ok(document.getElementById('budget').innerHTML.includes('Pulse compression gain'),
   'pulse compression gain shown in the link budget');
ok(document.getElementById('coverage').innerHTML.includes('%'), 'coverage rendered');
ok(document.getElementById('legend').innerHTML.includes('Antarctica'), 'legend rendered');
// These spans are optional — the prose around them is hand-edited.
for (const id of ['model-line', 'model-rmse', 'footer-rmse']) {
  const el = document.getElementById(id);
  if (el) ok(el.textContent.length > 0, `${id} filled`);
  else console.log(`     (no #${id} in this page — skipped)`);
}
{
  const links = (sel) => document.querySelectorAll(sel).map((a) => a._attrs.href).filter(Boolean);
  const attr = links('.attribution a');
  ok(attr.length === 5, `attribution carries ${attr.length} links`);
  for (const want of ['radar-return-statistics', 'openpolarradar.org',
                      'radar-return-statistics-postprocessing', 'englacial.org', 'astera.org']) {
    ok(attr.some((h) => h.includes(want)), `attribution links to ${want}`);
  }
  const foot = links('footer a');
  for (const want of ['openpolarradar.org', 'englacial/radar-return-statistics',
                      'radar-return-statistics-postprocessing', 'data_sources.md',
                      'doi.org/10.1109/IGARSS47720.2021.9553237']) {
    ok(foot.some((h) => h.includes(want)), `citation links to ${want}`);
  }
  // The page build points at logos/; the bundle must inline them, or a
  // double-clicked single file would show broken images.
  const srcs = document.querySelectorAll('.attribution img').map((i) => i._attrs.src);
  ok(srcs.length === 2, `${srcs.length} attribution logos`);
  ok(srcs.every((v) => (isBundle ? v.startsWith('data:image/png;base64,') : v.startsWith('logos/'))),
     isBundle ? 'bundle inlines the logos' : 'page build references logos/');
}
ok(document.getElementById('map-title').textContent.length > 0, 'map title set');
const coast = globalThis.__APP_STATE__.coast;
ok(coast.antarctic?.coast?.length > 0 && coast.greenland?.coast?.length > 0,
   'coastlines loaded for both sheets');
ok(coast.antarctic?.grounding?.length > 0, 'grounding line loaded');
for (const [sheet, kinds] of Object.entries(coast)) {
  const g = globalThis.__APP_STATE__.meta.sheets[sheet];
  for (const [kind, segs] of Object.entries(kinds)) {
    ok(segs.every((s2) => s2.length >= 12 && s2.length % 2 === 0),
       `${sheet} ${kind}: well-formed coordinate pairs`);
    const flat = segs.flat();
    const xs = flat.filter((_, i) => i % 2 === 0), ys = flat.filter((_, i) => i % 2 === 1);
    ok(Math.min(...xs) >= 0 && Math.max(...xs) <= g.shape[1]
       && Math.min(...ys) >= 0 && Math.max(...ys) <= g.shape[0],
       `${sheet} ${kind}: lies inside the raster it is drawn on`);
  }
}
ok(document.getElementById('scale-antarctic') === null
   && document.getElementById('scale-greenland') === null,
   'textual map extents removed (scale bars are drawn on the canvas)');
ok(!document.getElementById('results').classList.contains('suppressed'),
   'defaults produce no blocking error');
ok(!document.getElementById('results').classList.contains('suppressed'),
   'defaults raise no blocking errors');
{
  // Switching to the adaptive branch removes the overlap warning by construction.
  const mode = document.getElementById('overlap_mode');
  const was = mode.value;
  mode.value = 'adaptive';
  mode.dispatch('input');
  ok(document.getElementById('warnings').classList.contains('is-empty'),
     'adaptive branch is clean and hides the warning box');
  ok(document.getElementById('sidelobe_window').disabled,
     'receive weighting is disabled when it has no effect');
  mode.value = 'sidelobe';
  mode.dispatch('input');
  ok(!document.getElementById('sidelobe_window').disabled, 'and re-enabled for the sidelobe branch');
  mode.value = was;                     // leave the preset's own choice intact
  mode.dispatch('input');
}
for (const det of document.querySelectorAll('#panel > details')) {
  const sum = det.querySelector('.sum');
  ok(sum && sum.textContent.length > 0, `summary filled: ${det.dataset.summary}`);
}
// auto fields come from the markup, so removing one doesn't break this test
const autoIds = [...document.querySelectorAll('#panel input')]
  .filter((el) => 'derived' in el.dataset).map((el) => el.id);
ok(autoIds.length > 0, `${autoIds.length} auto fields on the page`);
for (const id of autoIds) {
  const el = document.getElementById(id);
  ok(el.value !== '' && Number.isFinite(+el.value), `auto value shown: ${id}`);
  ok(!!el.closest('label').querySelector('.revert'), `revert control added: ${id}`);
}

ok(document.getElementById('info-box').innerHTML.includes('Run'), 'info box rendered');
const rail = document.getElementById('rail');
const info = document.getElementById('info-box');
const warn = document.getElementById('warnings');
ok(info.closest('.rail') === rail && warn.closest('.rail') === rail, 'both notes live in the rail');
const view = document.getElementById('view-box');
ok(info.dataset.anchor === 'sec-intro' && warn.dataset.anchor === 'sec-params'
   && view.dataset.anchor === 'map-card', 'each note names the section it tracks');
ok(view.closest('.rail') === rail, 'the view controls sit in the rail');
const dist = document.getElementById('dist-box');
ok(dist.dataset.anchor === 'dist-card' && dist.closest('.rail') === rail,
   'the distribution controls track the distributions card');
ok(document.getElementById('dist-card').querySelector('#hist') !== null,
   'that card is the one holding the histograms');
for (const [id, box] of [['quantity', view], ['target_snr_dB', dist],
                         ['posterior_predictive', dist], ['split_floating', dist]]) {
  const el = document.getElementById(id);
  ok(el.closest('#view-box, #dist-box') === box, `${id} sits in the right note`);
  ok(el.closest('#panel') === null, `${id} is no longer in the parameter panel`);
}
// ...and are still read as parameters from their new home
{
  // Push-down assumes rail order matches anchor order down the page. Checked
  // positionally rather than against a fixed list, so adding a note is fine.
  const order = rail.children.map((c) => c.dataset.anchor);
  const docIndex = (id) => descendants(root).indexOf(document.getElementById(id));
  ok(order.every((a, i) => i === 0 || docIndex(order[i - 1]) <= docIndex(a)),
     `rail notes follow anchor order: ${order.join(' → ')}`);
  const clutter = document.getElementById('clutter-box');
  ok(clutter && clutter.closest('.rail') === rail, 'clutter note is in the rail');
  ok(rail.children.indexOf(clutter) === rail.children.indexOf(view) + 1,
     'clutter note sits directly under the map note');
}
ok(document.getElementById('map-card').querySelector('canvas') !== null,
   'the view box tracks the card that holds the maps');

// ── presets ────────────────────────────────────────────────────────────────
// Ids come from presets.js and are meant to be edited, so nothing here names one.
const presets = document.querySelectorAll('.preset');
const designs = presets.filter((b) => b.dataset.preset !== 'custom');
const selected = () => [...document.querySelectorAll('.preset')]
  .find((b) => b.getAttribute('aria-pressed') === 'true')?.dataset.preset;
const presetIdOf = (btn) => btn.dataset.preset;

ok(presets.length >= 2 && designs.length === presets.length - 1,
   `${designs.length} design presets plus custom`);
ok(getPreset(DEFAULT_PRESET)?.values, `DEFAULT_PRESET "${DEFAULT_PRESET}" names a real preset`);
ok(selected() === DEFAULT_PRESET,
   `page opens on the default preset (${selected()} === ${DEFAULT_PRESET})`);
ok(presets.every((b) => b.innerHTML.includes('<svg')), 'every preset carries an icon');
{
  const custom = presets.find((b) => b.dataset.preset === 'custom');
  ok(custom.tagName === 'DIV', 'the custom card is a marker, not a control');
  ok(document.getElementById('share').closest('.preset') === custom,
     'the share button lives in the custom card');
  ok(designs.every((b) => b.tagName === 'BUTTON'), 'the design presets are still buttons');
}
// a preset may set non-numeric or auto fields; those must survive apply -> match
{
  const nonNumeric = PRESET_LIST.filter((x) => x.values)
    .filter((x) => Object.values(x.values).some((v) => typeof v !== 'number'));
  ok(nonNumeric.length > 0, `${nonNumeric.length} preset(s) set a non-numeric field`);

  // An auto field named by a preset is pinned, shows as overridden, and still
  // matches — the box would otherwise silently ignore the preset's value.
  const AUTO_IDS_ON_PAGE = [...document.querySelectorAll('#panel input')]
    .filter((el) => 'derived' in el.dataset).map((el) => el.id);
  const pinning = PRESET_LIST.filter((x) => x.values)
    .filter((x) => AUTO_IDS_ON_PAGE.some((id) => x.values[id] !== undefined));
  ok(pinning.length > 0, `${pinning.length} preset(s) pin an auto field`);
  for (const pr of pinning) {
    const card = presets.find((b) => b.dataset.preset === pr.id);
    card.dispatch('click');
    ok(selected() === pr.id, `${pr.id}: still matches with a pinned auto field`);
    for (const id of AUTO_IDS_ON_PAGE.filter((k) => pr.values[k] !== undefined)) {
      const el = document.getElementById(id);
      const unit = el.dataset.unit ? parseFloat(el.dataset.unit) : 1;
      ok(Math.abs(parseFloat(el.value) * unit - pr.values[id]) < 1e-9,
         `${pr.id}: ${id} shows the pinned value (${el.value})`);
      ok(el.closest('label').classList.contains('is-overridden'),
         `${pr.id}: ${id} is marked as overridden`);
    }
  }
}
for (const btn of designs) {
  btn.dispatch('click');
  ok(document.getElementById('budget').innerHTML.includes('dBm'),
     `preset survives click: ${presetIdOf(btn)}`);
  ok(selected() === presetIdOf(btn), `preset selects itself: ${presetIdOf(btn)}`);
}

// a preset that sets a field a later preset omits must not leak across
const [first, second] = designs;
second.dispatch('click');
const secondFreq = document.getElementById('frequency_Hz').value;
first.dispatch('click');
const home = presetIdOf(first);
ok(document.getElementById('frequency_Hz').value !== secondFreq
   || presetIdOf(first) === presetIdOf(second),
   'switching presets resets fields the new one does not set');
ok(selected() === home, `back on ${home}`);

// touching any parameter drops to custom, and restoring it comes back
const freq = document.getElementById('frequency_Hz');
const wasFreq = freq.value;
freq.value = String(parseFloat(wasFreq) + 17);
freq.dispatch('input');
ok(selected() === 'custom', 'changing a parameter selects custom');
freq.value = wasFreq;
freq.dispatch('input');
ok(selected() === home, 'restoring the value re-selects the preset');

// overriding an auto field the preset does not pin is also custom
const pl = document.getElementById('pulse_length_s');
const wasPulse = pl.value;
pl.value = String(parseFloat(wasPulse) + 5);
pl.dispatch('input');
ok(selected() === 'custom', 'overriding an auto field selects custom');
pl.closest('label').querySelector('.revert').dispatch('click');
ok(selected() === home, 'reverting the override restores the preset');

// Notes track their anchors, and stack rather than overlap when anchors are
// close. Written against whatever notes the page defines, since the rail is
// hand-edited: the top note sits at its anchor, and each one below clears it.
const place = (intro, params, results = params + 800) => {
  rail._pageTop = 0;
  document.getElementById('sec-intro')._pageTop = intro;
  document.getElementById('sec-params')._pageTop = params;
  document.getElementById('map-card')._pageTop = results;
  document.getElementById('dist-card')._pageTop = results + 400;
  document.getElementById('altitude_m').dispatch('input');   // forces a relayout
};
const topOf = (el) => parseFloat(el.style.top);
const shown = () => rail.children.filter((n) => !n.classList.contains('is-empty'));

document.getElementById('pa_efficiency').value = '1.5';      // make the warn box visible
document.getElementById('pa_efficiency').dispatch('input');

for (const [why, args] of [['well separated', [40, 900]], ['anchors close together', [40, 60, 80]]]) {
  place(...args);
  const notes = shown();
  ok(notes.length >= 3, `${why}: ${notes.length} notes laid out`);
  ok(topOf(notes[0]) === parseFloat(document.getElementById(notes[0].dataset.anchor)._pageTop),
     `${why}: the top note sits on its anchor`);
  let bad = 0;
  for (let i = 1; i < notes.length; i++) {
    const prevBottom = topOf(notes[i - 1]) + notes[i - 1].offsetHeight;
    const anchor = parseFloat(document.getElementById(notes[i].dataset.anchor)._pageTop);
    if (topOf(notes[i]) < prevBottom) bad++;                  // must never overlap
    if (topOf(notes[i]) < anchor - 1e-6) bad++;               // never above its anchor
  }
  ok(bad === 0, `${why}: every note clears the one above and its own anchor`);
}
place(40, 60, 80);
ok(shown().some((n, i, a) => i > 0 && n.dataset.anchor === a[i - 1].dataset.anchor
   && topOf(n) > topOf(a[i - 1])), 'notes sharing an anchor stack rather than coincide');

document.getElementById('pa_efficiency').value = '0.5';
document.getElementById('pa_efficiency').dispatch('input');

// view options are not part of the design a preset describes
document.getElementById('target_snr_dB').value = '20';
document.getElementById('target_snr_dB').dispatch('input');
ok(selected() === home, 'view options do not switch to custom');
document.getElementById('target_snr_dB').value = '10';
document.getElementById('target_snr_dB').dispatch('input');

const alt = document.getElementById('altitude_m');
alt.value = '';
alt.dispatch('input');
ok(document.getElementById('results').classList.contains('suppressed'), 'blank field suppresses results');
ok(document.getElementById('warnings').innerHTML.includes('empty'), 'blank field explained');
ok(!document.getElementById('warnings').classList.contains('is-empty'), 'warning box shown');
alt.value = '12500';
alt.dispatch('input');
ok(!document.getElementById('results').classList.contains('suppressed'), 'recovers when refilled');

const eff = document.getElementById('pa_efficiency');
eff.value = '1.5';
eff.dispatch('input');
ok(document.getElementById('warnings').innerHTML.includes('between 0 and 1'), 'invalid efficiency reported');
ok(eff.classList.contains('flag-error'), 'invalid field highlighted');
eff.value = '0.5';
eff.dispatch('input');

const q = document.getElementById('quantity');
ok([...document.querySelectorAll('#view-box option')].map((o) => o._attrs.value).join() === 'snr,p20',
   'only the two dB quantities remain');
q.value = 'p20';
q.dispatch('input');
ok(document.getElementById('map-title').textContent.includes('20th'), 'percentile view switches');
{
  const S2 = globalThis.__APP_STATE__;
  let shifted = 0, worse = 0;
  for (let i = 0; i < S2.data.antarctic.n; i += 997) {
    if (S2.values.antarctic[i] < S2.snr.antarctic[i]) worse++;
    shifted++;
  }
  ok(worse === shifted, 'the 20th percentile is below the mean everywhere');
  const pp = document.getElementById('posterior_predictive');
  pp.checked = false;
  pp.dispatch('input');
  ok(document.getElementById('coverage').innerHTML.includes('20th-pct'),
     'coverage stats name the percentile layer when not predictive');
  pp.checked = true;
  pp.dispatch('input');
  ok(document.getElementById('coverage').innerHTML.includes('basal SNR ≥'),
     'coverage stats describe basal SNR under the predictive');
}
q.value = 'snr';
q.dispatch('input');

document.getElementById('split_floating').checked = true;
document.getElementById('split_floating').dispatch('input');
ok(document.getElementById('legend').innerHTML.includes('floating'), 'floating split adds a series');
ok(!/Greenland — (floating|grounded)/.test(document.getElementById('legend').innerHTML),
   'Greenland is never split (too few floating cells to say anything)');
ok(/Antarctica — floating/.test(document.getElementById('legend').innerHTML),
   'Antarctica is split');
document.getElementById('split_floating').checked = false;
document.getElementById('split_floating').dispatch('input');

// The distribution axis must contain every value, or the end bins collect a
// spike that is an artefact of the axis rather than anything in the data.
const S = globalThis.__APP_STATE__;
const spanOf = () => {
  let lo = Infinity, hi = -Infinity;
  for (const sheet of ['antarctic', 'greenland']) {
    for (const v of S.snr[sheet]) { if (v < lo) lo = v; if (v > hi) hi = v; }
  }
  return [lo, hi];
};
const covers = (why) => {
  const [lo, hi] = spanOf();
  const [alo, ahi] = S.snrRange;
  ok(alo <= lo && ahi >= hi,
     `${why}: axis [${alo.toFixed(0)}, ${ahi.toFixed(0)}] covers data [${lo.toFixed(0)}, ${hi.toFixed(0)}]`);
};
covers('default');
// Power only moves the result where the bed clears the transmitted pulse — in
// sidelobe mode the pedestal scales with the surface and cancels it exactly, so
// this check runs in the adaptive branch where more power really does help.
const mode = document.getElementById('overlap_mode');
const wasMode = mode.value;
mode.value = 'adaptive';
mode.dispatch('input');
const before = S.snrRange.slice();
// Drive this with transmit power itself: payload power would do nothing when a
// preset pins tx_power_W, which several do.
const power = document.getElementById('tx_power_W');
const wasPower = power.value;
power.value = String(parseFloat(power.value) * 400);   // push everything far right
power.dispatch('input');
covers('after a large power increase');
ok(S.snrRange[1] > before[1],
   `axis follows the data upward (${before[1].toFixed(0)} -> ${S.snrRange[1].toFixed(0)} dB)`);
power.closest('label').querySelector('.revert').dispatch('click');
mode.value = wasMode;
mode.dispatch('input');
presets.find((b) => b.dataset.preset !== 'custom').dispatch('click');
covers('back on a preset');

// The target line has to be on the axis to be drawn at all.
const tgt = document.getElementById('target_snr_dB');
for (const t of ['10', '-40', '250']) {
  tgt.value = t;
  tgt.dispatch('input');
  ok(S.snrRange[0] <= +t && S.snrRange[1] >= +t, `axis includes the target at ${t} dB`);
}
tgt.value = '10';
tgt.dispatch('input');

ok(document.getElementById('export-csv') === null, 'CSV export removed');
document.getElementById('export-png').dispatch('click');
document.getElementById('share').dispatch('click');
ok(true, 'PNG export and share run without throwing');

// the noise floor must respond to the noise figure, not just be annotated by it
{
  const nf = document.getElementById('noise_figure_dB');
  const readNoise = () => parseFloat(
    /Noise power<\/span><span>(-?[\d.]+)/.exec(document.getElementById('budget').innerHTML)[1]);
  const was = nf.value;
  const before = readNoise();
  nf.value = String(parseFloat(was) + 6);
  nf.dispatch('input');
  ok(readNoise() > before, 'a higher noise figure raises the noise floor');
  nf.value = was;
  nf.dispatch('input');
  ok(Math.abs(readNoise() - before) < 1e-6, 'and returns when it is put back');
}

// the hover readout has to describe whichever overlap treatment is active
{
  const hoverAt = () => {
    document.getElementById('map-antarctic').dispatch('mousemove', { clientX: 300, clientY: 150 });
    return document.getElementById('hover').textContent;
  };
  const mode = document.getElementById('overlap_mode');
  const text = hoverAt();
  ok(/m ice/.test(text) && /RSSNR/.test(text) && /SNR/.test(text), 'hover reports the cell');
  ok(/sidelobe .*dBm|clears the pulse/.test(text), `sidelobe mode reports levels: ${text}`);
  mode.value = 'adaptive';
  mode.dispatch('input');
  const adaptiveText = hoverAt();
  ok(/pulse [\d.]+ µs/.test(adaptiveText), `adaptive mode reports pulse length: ${adaptiveText}`);
  ok(!/sidelobe/.test(adaptiveText), 'and does not mention sidelobes');
  mode.value = 'sidelobe';
  mode.dispatch('input');
}

console.log(failed ? `\n${failed} FAILURES` : '\nsmoke test passed');
process.exit(failed ? 1 : 0);
