"use strict";
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const P = new URLSearchParams(location.search);
const store = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
const load = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const POP = P.has('popup');                                                          // the compact card popup beside the orb (orb form): only cards, nothing else
const DOCK = P.has('dock') || POP;                                                          // the small always-on-top orb window (/dock)
if (DOCK && !POP) document.documentElement.classList.add('dock');
if (POP) { document.documentElement.classList.add('popup'); window.__popReady = false; fetch('/api/popup').then(r => r.json()).then(l => { if (l.length) popAdd(l[l.length - 1]); window.__popReady = true; }).catch(() => { window.__popReady = true; }); setInterval(() => { if (window.__popReady && !cards.length) window.close(); }, 4000); }
const HQ = new URLSearchParams(location.search).has('hq');
const TAU = Math.PI * 2, clamp = (v, a, b) => Math.min(b, Math.max(a, v)), D2R = Math.PI / 180;

const S = { base: 'jarvis', mode: 'loading', lvl: 0, tgt: 0, think: 0, spin: 0, spinV: .3, theme: 'jarvis', lite: false, muted: false,
            source: 'auto', frameEma: 16, slow: 0, viewAmt: 0, viewing: false };
const cv = $('#fx'), g = cv.getContext('2d'), bgc = $('#bgc'), bgx = bgc.getContext('2d');
let W = 0, H = 0, dpr = 1, cx = 0, cy = 0, R = 90, box = 400, ox = 0, oy = 0, ringImg = null, specks = [], ws = null;   // ox,oy = ring centre inside the small canvas
let ringTw = null; const ring = { x: 0, y: 0, s: 1 }, ringT = { x: 0, y: 0, s: 1 };       // current / target centre + scale (view mode shrinks it)

/* ---------------------------------------------------------------- theme / lite */
let PERS = {};                                                                       // persona id -> {name, base, accent, word}, from the server
const BASE_HUE = { jarvis: 217, rose: 346 };
const hexHue = h => { const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(h || ''); if (!m) return null; const [r, g, b] = [1, 2, 3].map(i => parseInt(m[i], 16) / 255); const mx = Math.max(r, g, b), d = mx - Math.min(r, g, b); if (!d) return 0; const x = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; return (x * 60 + 360) % 360; };
let roseWordFor = '';
const MOOD = { calm: [0, .9, .9], happy: [14, 1.05, 1.05], excited: [26, 1.3, 1.35], thinking: [-14, .95, 1.1], sorry: [-22, .8, .75], serious: [-6, .85, .85], playful: [34, 1.15, 1.2] };   // hue shift (warmer = +), pulse, spin
let moodT = [0, 1, 1];
function applyLook() {                                                               // a persona = one of the two drawn looks (HUD "jarvis" or brush "rose") + its own hue and wordmark; moods nudge the hue a little more
  const p = PERS[S.theme] || {}, base = p.base || (S.theme === 'rose' ? 'rose' : 'jarvis'), word = p.word || S.theme.toUpperCase();
  const changed = S.base !== base; S.base = base; document.documentElement.dataset.theme = base;
  const hh = hexHue(p.accent); let deg = hh == null ? 0 : hh - BASE_HUE[base]; if (deg > 180) deg -= 360; if (deg < -180) deg += 360; if (Math.abs(deg) < 6) deg = 0;      // Jarvis and Rose themselves need no filter
  S.personaHue = deg; const tot = Math.round(deg + (S.moodHue || 0)), f = tot ? `hue-rotate(${tot}deg)` : '';
  for (const id of ['fx', 'center']) { const e = $('#' + id); if (e && e.style.filter !== f) e.style.filter = f; }
  document.documentElement.style.setProperty('--persona-acc', p.accent || '');
  const jw = $('#jword'); if (jw && jw.textContent !== word) jw.textContent = word;
  if (base === 'rose' && roseWordFor !== word) { roseWordFor = word; roseWord = makeRoseWord(word); const rw = $('#rword'); rw.width = roseWord.width; rw.height = roseWord.height; const c = rw.getContext('2d'); c.clearRect(0, 0, rw.width, rw.height); c.drawImage(roseWord, 0, 0); rw.setAttribute('aria-label', word); }
  return changed;
}
function setTheme(t, save = true) {
  S.theme = t; if (save) store('jv.theme', t);
  applyLook();
  const mt = $('#m-theme'); if (mt) { mt.textContent = 'PERSONA'; mt.title = 'Persona: ' + ((PERS[t] || {}).name || t) + ' (Alt+T switches)'; }                // a fixed label: every persona lays the menu out identically
  build(); fitWords(); setRingTarget(false); renderPersonaList();
  if (save) send({ t: 'persona', v: t });                                              // theme = voice + name + persona
}
function renderPersonaList() {
  const box = $('#plist'); if (!box) return; box.textContent = '';
  for (const p of Object.values(PERS)) { const b = document.createElement('button'); b.className = 'box pbtn' + (p.id === S.theme ? ' on' : ''); b.textContent = p.name.toUpperCase(); b.style.setProperty('--pc', p.accent); b.onclick = () => setTheme(p.id); box.appendChild(b); }
}
function setLite(on, save = true, why) {
  S.lite = on; document.documentElement.classList.toggle('lite', on); const sl = $('#s-lite'); if (sl) sl.textContent = 'LITE MODE · ' + (on ? 'ON' : 'OFF');
  if (save) store('jv.lite', on ? '1' : '0');
  if (why) toast(why);
  resize();
}
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('on'); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('on'), 2400); }

/* ---------------------------------------------------------------- layout + static layers */
function resize() {
  W = innerWidth; H = innerHeight; dpr = S.lite ? 1 : Math.min(devicePixelRatio || 1, 1.5);
  cx = W / 2; cy = H / 2; R = Math.min(W, H) * .105;
  if (DOCK) { R = Math.min(W, H) * .165; cy = H * .42; }     // the orb is exactly centred, in every state
  box = Math.ceil(R * 5.4); ox = oy = box / 2;                                       // animated canvas = just the ring's neighbourhood
  cv.width = Math.round(box * dpr); cv.height = Math.round(box * dpr); cv.style.width = cv.style.height = box + 'px';
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  bgc.width = Math.round(W * dpr); bgc.height = Math.round(H * dpr); bgx.setTransform(dpr, 0, 0, dpr, 0, 0);
  setRingTarget(true);
  const root = document.documentElement.style;
  root.setProperty('--cw', clamp(W * .27, 210, 300) + 'px');
  fitWords();
  if (typeof cards !== 'undefined') cards.forEach((c, i) => c.moved || c.isFull || placeCard(c.el, i));   // re-flow cards after a resize / rotation
  specks = Array.from({ length: S.lite ? 8 : 18 }, () => ({ x: Math.random() * box, y: Math.random() * box, vx: (Math.random() - .5) * 8, vy: (Math.random() - .5) * 8, a: Math.random() * TAU, big: Math.random() < .2 }));
  build();
}
function fitWords() {
  const root = document.documentElement.style;
  root.setProperty('--wordsize', Math.max(13, R * .26) + 'px');
  root.setProperty('--rosewidth', R * 2.15 + 'px');
}
const anyFull = () => !DOCK && typeof cards !== 'undefined' && cards.some(c => c.isFull);
function setRingTarget(snap) {                                                       // target is always derived from state (never stored), so it cannot go stale
  const corner = !DOCK && !S.ghost && (S.viewing || anyFull());                               // ghost: always centred; look view / fullscreen card: small, bottom-right
  if (corner) { ringT.x = W * .87; ringT.y = H * .78; ringT.s = .4; } else { ringT.x = cx; ringT.y = cy; ringT.s = 1; }
  if (snap) { ringTw = null; Object.assign(ring, ringT); placeWord(); }
}
function placeWord() {                                                               // transform only (no layout): the orb glides on the compositor
  const c = $('#center').style; if (c.left !== '0px') { c.left = '0px'; c.top = '0px'; } c.transform = `translate(${ring.x.toFixed(1)}px,${ring.y.toFixed(1)}px) translate(-50%,-50%) scale(${ring.s.toFixed(3)})`;
  cv.style.transform = `translate(${(ring.x - box / 2).toFixed(1)}px,${(ring.y - box / 2).toFixed(1)}px)`;
  const hs = Math.max(72, R * 2.5 * ring.s), oh = $('#orbhit').style; oh.width = oh.height = hs + 'px'; if (oh.left !== '0px') { oh.left = '0px'; oh.top = '0px'; } oh.transform = `translate(${(ring.x - hs / 2).toFixed(1)}px,${(ring.y - hs / 2).toFixed(1)}px)`;
}
const RING_GLIDE_MS = 450, easeInOut = p => p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;      // the orb speeds up, then slows down (not a constant speed)
function ringStep(now) {                                                             // returns true while the glide is running
  const moved = Math.abs(ringT.x - ring.x) + Math.abs(ringT.y - ring.y) + Math.abs(ringT.s - ring.s) * 100;
  if (!ringTw && moved > .05) ringTw = { t0: now, from: { x: ring.x, y: ring.y, s: ring.s }, to: { x: ringT.x, y: ringT.y, s: ringT.s } };
  if (ringTw && (Math.abs(ringTw.to.x - ringT.x) + Math.abs(ringTw.to.y - ringT.y) + Math.abs(ringTw.to.s - ringT.s) * 100 > .5)) ringTw = { t0: now, from: { x: ring.x, y: ring.y, s: ring.s }, to: { x: ringT.x, y: ringT.y, s: ringT.s } };   // the target moved: glide on from here
  if (!ringTw) return false;
  const p = Math.min(1, (now - ringTw.t0) / RING_GLIDE_MS), e = easeInOut(p);
  ring.x = ringTw.from.x + (ringTw.to.x - ringTw.from.x) * e; ring.y = ringTw.from.y + (ringTw.to.y - ringTw.from.y) * e; ring.s = ringTw.from.s + (ringTw.to.s - ringTw.from.s) * e; placeWord();
  if (p >= 1) ringTw = null;
  return true;
}

/* ---- ROSE art, prerendered once per size: tick ring, dial band, grunge brush wordmark ---- */
let roseWord = null, dialImg = null;
const mkCanvas = n => { const c = document.createElement('canvas'); c.width = c.height = n; return [c, c.getContext('2d')]; };
const hash2 = (ix, iy) => { const v = Math.sin(ix * 127.1 + iy * 311.7) * 43758.5453; return v - Math.floor(v); };
function vnoise(x, y) { const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy, u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  return hash2(ix, iy) * (1 - u) * (1 - v) + hash2(ix + 1, iy) * u * (1 - v) + hash2(ix, iy + 1) * (1 - u) * v + hash2(ix + 1, iy + 1) * u * v; }
function makeRoseWord(word = 'ROSE') {                                                            // ragged dry-brush "ROSE": narrow slanted letters, trailing smear, eroded edges, scratches, ink splatter
  const w = 780, h = 330, c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d');
  let seed = 11; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const fs = Math.round(270 * Math.min(1, 4 / Math.max(4, word.length))), by = 165 + fs * .31;                      // longer names get a smaller brush so they fit the ring
  x.font = `italic bold ${fs}px "Nimbus Sans Narrow","Liberation Sans Narrow","Liberation Sans",sans-serif`; x.textAlign = 'center'; x.lineJoin = 'miter'; x.fillStyle = '#fff'; x.strokeStyle = '#fff';
  try { x.letterSpacing = '-2px'; } catch (e) {}
  x.setTransform(1, 0, -.16, 1, 40, 0);                                              // extra slant
  x.globalAlpha = .1; for (let k = 1; k <= 6; k++) { x.fillText(word, w / 2 - k * 5, by + (rnd() - .5) * 4); }       // dry-brush streaks trailing to the left
  x.globalAlpha = 1;
  for (let i = 0; i < 9; i++) { x.save(); x.translate(w / 2 + (rnd() - .5) * 10, by + (rnd() - .5) * 7); x.rotate((rnd() - .5) * .03); x.scale(1 + (rnd() - .5) * .05, 1 + (rnd() - .5) * .04); x.lineWidth = .4 + rnd() * 1.6; x.strokeText(word, 0, 0); x.fillText(word, 0, 0); x.restore(); }
  x.setTransform(1, 0, 0, 1, 0, 0);
  const img = x.getImageData(0, 0, w, h), d = img.data, A = new Float32Array(w * h), B = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) A[i] = d[i * 4 + 3] / 255;
  const RAD = 6;                                                                    // box blur of alpha = "how deep inside the letter" (separable)
  for (let y = 0; y < h; y++) { let sum = 0; for (let xx = -RAD; xx <= RAD; xx++) sum += A[y * w + Math.min(w - 1, Math.max(0, xx))]; for (let xx = 0; xx < w; xx++) { B[y * w + xx] = sum / (2 * RAD + 1); sum += A[y * w + Math.min(w - 1, xx + RAD + 1)] - A[y * w + Math.max(0, xx - RAD)]; } }
  for (let xx = 0; xx < w; xx++) { let sum = 0; for (let y = -RAD; y <= RAD; y++) sum += B[Math.max(0, y) * w + xx]; for (let y = 0; y < h; y++) { A[y * w + xx] = sum / (2 * RAD + 1); sum += B[Math.min(h - 1, y + RAD + 1) * w + xx] - B[Math.max(0, y - RAD) * w + xx]; } }
  for (let y = 0; y < h; y++) for (let xx = 0; xx < w; xx++) {                      // erode: low-depth pixels (edges, thin parts) are eaten by horizontally-stretched noise
    const i = y * w + xx; if (!d[i * 4 + 3]) continue;
    const n = vnoise(xx * .09, y * .3) * .5 + vnoise(xx * .3, y * .9) * .35 + vnoise(xx * 1, y * 2.2) * .15;
    if (n > A[i] * .86 + (d[i * 4 + 3] < 255 ? -.08 : 0)) d[i * 4 + 3] = 0;
  }
  for (let k = 0; k < 42; k++) { const y0 = 50 + rnd() * 230 | 0, x0 = rnd() * w | 0, len = 24 + rnd() * 120, th = 1 + (rnd() * 2 | 0);   // brush scratches
    for (let t = 0; t < len; t++) for (let q = 0; q < th; q++) { const xx = x0 + t, yy = y0 + q; if (xx < w && yy < h && rnd() < .88) d[(yy * w + xx) * 4 + 3] = 0; } }
  x.putImageData(img, 0, 0);
  x.strokeStyle = '#fff'; x.lineCap = 'round';                                      // brush bristles: thin spikes that stick out of the letter edges (mostly sideways)
  for (let k = 0, tries = 0; k < 170 && tries < 40000; tries++) { const px = rnd() * w | 0, py = 30 + rnd() * 270 | 0, i = py * w + px;
    if (d[i * 4 + 3] > 0 && A[i] > .3 && A[i] < .62) { const a = (rnd() < .5 ? 0 : Math.PI) + (rnd() - .5) * .8, L = 7 + rnd() * rnd() * 34; x.globalAlpha = .55 + rnd() * .4; x.lineWidth = .8 + rnd() * 1.6; x.beginPath(); x.moveTo(px, py); x.lineTo(px + Math.cos(a) * L, py + Math.sin(a) * L); x.stroke(); k++; } }
  for (let k = 0; k < 130; k++) { const top = rnd() < .45, px = w * .06 + rnd() * w * .88, py = top ? 20 + rnd() * 80 : 20 + rnd() * 280, sz = .6 + rnd() * rnd() * 5;   // ink splatter
    x.globalAlpha = .3 + rnd() * .65; x.fillStyle = rnd() < .1 ? '#ff7096' : '#fff'; if (rnd() < .35) x.fillRect(px, py, sz * 4, sz * .7); else { x.beginPath(); x.arc(px, py, sz, 0, TAU); x.fill(); } }
  x.globalAlpha = 1; return c;
}
function buildRose() {
  const Dm = Math.ceil(R * 4.4);
  { const [c, x] = mkCanvas(Dm); x.translate(Dm / 2, Dm / 2); const r0 = R * 1.62;       // slow outer ring: dotted ring, grey ticks, tiny numbers, red segments, markers
    x.strokeStyle = 'rgba(200,185,205,.3)'; x.lineWidth = 1; x.setLineDash([1, 5]); x.beginPath(); x.arc(0, 0, R * 1.92, 0, TAU); x.stroke(); x.setLineDash([]);
    x.strokeStyle = 'rgba(235,225,240,.6)'; x.beginPath(); x.arc(0, 0, r0, 0, TAU); x.stroke(); x.globalAlpha = .3; x.beginPath(); x.arc(0, 0, r0 - 13, 0, TAU); x.stroke(); x.globalAlpha = 1;
    x.fillStyle = 'rgba(235,225,240,.85)'; x.font = '700 6px monospace'; x.textAlign = 'center'; x.textBaseline = 'middle';
    for (let i = 0; i < 180; i++) { const a = i / 180 * TAU, L = i % 15 === 0 ? 9 : i % 5 === 0 ? 6 : 3;
      x.beginPath(); x.moveTo(Math.cos(a) * r0, Math.sin(a) * r0); x.lineTo(Math.cos(a) * (r0 - L), Math.sin(a) * (r0 - L)); x.stroke();
      if (i % 15 === 0) { x.save(); x.translate(Math.cos(a) * (r0 + 9), Math.sin(a) * (r0 + 9)); x.rotate(a + Math.PI / 2); x.fillText(String(i * 2).padStart(3, '0'), 0, 0); x.restore(); } }
    x.strokeStyle = '#ff2848'; for (const [a0, a1, lw] of [[96, 142, 4.5], [-103, -88, 3.5], [-34, -24, 3], [34, 42, 3]]) { x.lineWidth = lw; x.beginPath(); x.arc(0, 0, r0 - 6, a0 * D2R, a1 * D2R); x.stroke(); }
    x.lineWidth = 2; x.beginPath(); for (const a of [100, 160, -96, 30]) { const ca = Math.cos(a * D2R), sa = Math.sin(a * D2R); x.moveTo(ca * (r0 - 14), sa * (r0 - 14)); x.lineTo(ca * (r0 + 4), sa * (r0 + 4)); } x.stroke();
    ringImg = c; }
  { const [c, x] = mkCanvas(Dm); x.translate(Dm / 2, Dm / 2);                               // pink dial band of fine radial ticks
    x.strokeStyle = 'rgba(255,92,135,.55)'; x.lineWidth = 1;
    for (let i = 0; i < 260; i++) { const a = i / 260 * TAU, L = i % 5 === 0 ? 10 : 5; x.beginPath(); x.moveTo(Math.cos(a) * R * 1.14, Math.sin(a) * R * 1.14); x.lineTo(Math.cos(a) * (R * 1.14 + L), Math.sin(a) * (R * 1.14 + L)); x.stroke(); }
    x.strokeStyle = 'rgba(255,70,110,.55)'; x.beginPath(); x.arc(0, 0, R * 1.13, 0, TAU); x.stroke(); x.beginPath(); x.arc(0, 0, R * 1.31, 0, TAU); x.stroke();
    dialImg = c; }
  roseWordFor = (PERS[S.theme] || {}).word || 'ROSE'; roseWord = makeRoseWord(roseWordFor); const rw = $('#rword'); rw.width = roseWord.width; rw.height = roseWord.height; rw.getContext('2d').drawImage(roseWord, 0, 0);
}
function build() {
  if (!W) return;
  const x = bgx; x.clearRect(0, 0, W, H); ringImg = null;
  if (S.base === 'jarvis') {
    const gr = x.createRadialGradient(cx, cy, 0, cx, cy, Math.max(W, H) * .75);
    gr.addColorStop(0, '#141d48'); gr.addColorStop(.55, '#0b1030'); gr.addColorStop(1, '#05071a'); x.fillStyle = gr; x.fillRect(0, 0, W, H);
    x.strokeStyle = 'rgba(110,150,255,.09)'; x.lineWidth = 1; x.beginPath();               // faint grid
    for (let i = 0; i < W; i += 36) { x.moveTo(i + .5, 0); x.lineTo(i + .5, H); }
    for (let j = 0; j < H; j += 36) { x.moveTo(0, j + .5); x.lineTo(W, j + .5); }
    x.stroke();
    x.strokeStyle = 'rgba(190,210,255,.75)'; x.lineWidth = 1; x.beginPath();               // tick marks: top row + left column
    const ty = H * .085, tx = W * .035;
    for (let i = 24; i < W - 12; i += 28) { const L = (i / 28 | 0) % 5 ? 3 : 6; x.moveTo(i + .5, ty); x.lineTo(i + .5, ty + L); }
    for (let j = 40; j < H - 40; j += 28) { const L = (j / 28 | 0) % 5 ? 3 : 6; x.moveTo(tx, j + .5); x.lineTo(tx + L, j + .5); }
    x.stroke();
    x.strokeStyle = 'rgba(210,228,255,.9)'; x.lineWidth = 1.1; x.beginPath();               // short HUD bracket with a trapezoid dip, top centre
    [[.36, .055], [.405, .055], [.425, .08], [.575, .08], [.595, .055], [.64, .055]].forEach(([a, b], i) => i ? x.lineTo(a * W, b * H) : x.moveTo(a * W, b * H));
    x.stroke();
    x.beginPath(); x.moveTo(W - 28, 14); x.lineTo(W - 14, 14); x.lineTo(W - 14, 28); x.stroke();   // corner glyph
  } else {
    const gr = x.createRadialGradient(cx, cy, 0, cx, cy, Math.max(W, H) * .8);
    gr.addColorStop(0, '#0d0a1c'); gr.addColorStop(.5, '#07060f'); gr.addColorStop(1, '#030208'); x.fillStyle = gr; x.fillRect(0, 0, W, H);
    buildRose();
  }
  bgUrl = `url(${bgc.toDataURL('image/png')}) 0 0 / 100% 100% no-repeat, var(--bg)`; applyBackdrop();   // backdrop = root layer, no extra full-screen layer
}
let bgUrl = '';
function applyBackdrop() { document.documentElement.style.background = S.ghost ? 'var(--bg)' : bgUrl; }

/* ---------------------------------------------------------------- drawing helpers */
function ringPath(r, wob, k, ph) {
  g.beginPath();
  if (wob < .4) { g.arc(ox, oy, r, 0, TAU); return; }
  const n = S.lite ? 64 : 120;
  for (let i = 0; i <= n; i++) { const a = i / n * TAU, rr = r + wob * Math.sin(a * k + ph); i ? g.lineTo(ox + Math.cos(a) * rr, oy + Math.sin(a) * rr) : g.moveTo(ox + Math.cos(a) * rr, oy + Math.sin(a) * rr); }
  g.closePath();
}
function glow(color, width, fn) {                                                   // glow = a few wide, faint strokes under the real one (shadowBlur is very slow without GPU raster; ?hq=1 re-enables it)
  g.strokeStyle = color; g.lineJoin = 'round';
  const a = g.globalAlpha;
  if (HQ && !S.lite) { g.shadowColor = typeof color === 'string' ? color : '#ff2d55'; g.shadowBlur = 14; g.lineWidth = width; fn(); g.stroke(); g.shadowBlur = 0; return; }
  g.globalAlpha = a * .07; g.lineWidth = width * 6; fn(); g.stroke();
  if (!S.lite) { g.globalAlpha = a * .16; g.lineWidth = width * 3; fn(); g.stroke(); }
  g.globalAlpha = a * .95; g.lineWidth = width; fn(); g.stroke(); g.globalAlpha = a;
}
const arcAt = (r, a0, len) => () => { g.beginPath(); g.arc(ox, oy, r, a0, a0 + len); };
const wobble = () => S.mode === 'speaking' ? S.lvl * R * .08 * ring.s : 0;
const bloomSprites = {};
function bloom(r, rgb, a) {                                                         // soft halo: gradient rendered once per colour, then just scaled + blitted
  let sp = bloomSprites[rgb];
  if (!sp) {
    sp = bloomSprites[rgb] = document.createElement('canvas'); sp.width = sp.height = 192; const x = sp.getContext('2d');
    const b = x.createRadialGradient(96, 96, 96 * .6 / 2.6, 96, 96, 96);
    b.addColorStop(0, `rgba(${rgb},1)`); b.addColorStop(.35, `rgba(${rgb},.4)`); b.addColorStop(1, `rgba(${rgb},0)`); x.fillStyle = b; x.fillRect(0, 0, 192, 192);
  }
  g.globalAlpha = clamp(a, 0, 1); g.drawImage(sp, ox - r * 2.6, oy - r * 2.6, r * 5.2, r * 5.2); g.globalAlpha = 1;
}
function backdrop() { g.clearRect(0, 0, box, box); }                                // the page backdrop is static (#bgc); only the ring is redrawn
let lastView = -1;
function applyView() {                                                              // fade the picture + vignette in/out; keep them out of the layer tree when not shown
  const v = S.viewAmt; if (Math.abs(v - lastView) < .004) return; lastView = v;
  const on = v > .004, vw = $('#view').style, vg = $('#vig').style;
  vw.display = vg.display = on ? 'block' : 'none'; vw.opacity = (v * .78).toFixed(3); vg.opacity = v.toFixed(3);
}

/* ---------------------------------------------------------------- JARVIS */
function drawJarvis(t) {
  backdrop();
  const k = ring.s, listen = S.mode === 'listening' ? 1.5 : 1, r = R * k * (1 + .07 * S.lvl * listen), w = wobble();
  bloom(r, '40,110,255', clamp(.5 + .4 * S.lvl + .12 * S.think, 0, .95));              // soft blue bloom
  g.fillStyle = '#02040e'; g.beginPath(); g.arc(ox, oy, r * .97, 0, TAU); g.fill();
  glow('#4a96ff', 4 * k + 1 + S.lvl * 3, () => ringPath(r, w, 7, S.spin * 3));          // main blue ring
  g.strokeStyle = 'rgba(235,245,255,.95)'; g.lineWidth = 1.5 * k + .4; ringPath(r * .9, w * .3, 7, S.spin * 3); g.stroke();   // thin white inner ring
  g.strokeStyle = 'rgba(110,160,255,.5)'; g.lineWidth = 1.2; ringPath(r * 1.12, 0, 0, 0); g.stroke();                          // thin outer ring
  const a = S.spin * 1.4 * (S.mode === 'thinking' ? 1.6 : 1);                          // bright orbiting arcs
  g.globalAlpha = 1; glow('#f2f8ff', 3 * k + 1, arcAt(r * 1.2, a, .85));
  g.strokeStyle = 'rgba(150,190,255,.75)'; g.lineWidth = 1.4; arcAt(r * 1.3, -a * .6 + 2, 2.2)(); g.stroke();
  g.strokeStyle = 'rgba(150,190,255,.4)'; arcAt(r * 1.3, -a * .6 + 3.6, .5)(); g.stroke();
  g.strokeStyle = 'rgba(210,228,255,.85)'; g.lineWidth = 1.2; g.beginPath();           // N/S/E/W ticks
  
  // (the four detached N/S/E/W tick marks that used to be drawn here were the 'holes' to the right of and below the ring: removed)
  // (the four detached double-dash marks on the diagonals were lone decorative lines, not part of a bracket, grid or ring: removed)
  g.stroke();
  for (const s of specks) {                                                           // drifting dots: faded out well before the canvas edge, so a half-clipped one can never show as a stray dash on the edge
    const fade = clamp((box / 2 - 14 - Math.hypot(s.x - ox, s.y - oy)) / (box * .1), 0, 1); if (fade <= 0) continue;
    g.fillStyle = `rgba(190,215,255,${(.3 + .3 * Math.sin(t * .001 + s.a)) * fade})`; const z = s.big ? 3 : 1.6; g.fillRect(s.x, s.y, z, z); }
}

/* ---------------------------------------------------------------- ROSE */
function bracket(x, y, sx, sy, len) { g.moveTo(x, y + sy * 5); g.lineTo(x, y); g.lineTo(x + sx * len, y); }
function drawRose(t) {
  backdrop();
  const k = ring.s, th = S.think, calm = 1 - th, listen = S.mode === 'listening' ? 1.5 : 1;
  const r = R * k * (1 + .06 * S.lvl * listen), w = wobble();
  bloom(r, '255,30,90', clamp((.14 + .3 * S.lvl) * (1 - th * .8) + .06 * th, 0, .6));
  if (calm > .02) {
    g.globalAlpha = calm;
    g.save(); g.translate(ox, oy); g.rotate(S.spin * .06); g.scale(k, k); g.drawImage(ringImg, -ringImg.width / 2, -ringImg.height / 2); g.restore();        // grey tick ring + numbers + red segments
    g.save(); g.translate(ox, oy); g.rotate(-S.spin * .12); g.scale(k, k); g.drawImage(dialImg, -dialImg.width / 2, -dialImg.height / 2); g.restore();         // pink fine-tick dial band
    g.strokeStyle = 'rgba(255,255,255,.92)'; g.lineWidth = 3 * k + .5; g.lineCap = 'butt';                                                                         // thick white arc segments
    for (const [a0, a1] of [[196, 238], [262, 304], [322, 338], [16, 46]]) { g.beginPath(); g.arc(ox, oy, r * 1.4, a0 * D2R - S.spin * .2, a1 * D2R - S.spin * .2); g.stroke(); }
    g.fillStyle = '#030207'; g.beginPath(); g.arc(ox, oy, r * 1.01, 0, TAU); g.fill();                              // black interior (covers the bloom inside the ring)
    g.strokeStyle = 'rgba(255,70,105,.85)'; g.lineWidth = 1.4; ringPath(r * 1.08, 0, 0, 0); g.stroke();             // thin crimson ring just outside the neon
    const gr = g.createLinearGradient(ox - r, 0, ox + r, 0); gr.addColorStop(0, '#ff6a98'); gr.addColorStop(1, '#ff2455');
    glow(gr, 5.5 * k + S.lvl * 3, () => ringPath(r * .97, w, 9, S.spin * 3));                                       // neon ring
    g.strokeStyle = 'rgba(255,110,150,.35)'; g.lineWidth = 5 * k; ringPath(r * .88, 0, 0, 0); g.stroke();           // inner glow band
    g.strokeStyle = 'rgba(255,255,255,.92)'; g.lineWidth = 1.4; ringPath(r * .78, w * .3, 9, S.spin * 3); g.stroke(); // thin white inner ring
    g.strokeStyle = 'rgba(255,255,255,.3)'; g.lineWidth = 1; g.beginPath();                                          // crosshair through the middle
    g.moveTo(ox, oy - r * .95); g.lineTo(ox, oy + r * .95); g.moveTo(ox - r * .95, oy); g.lineTo(ox + r * .95, oy); g.stroke();
    g.strokeStyle = 'rgba(255,255,255,.85)'; g.lineWidth = 1.3; g.beginPath();                                       // bracket corners top and bottom + crosshair ticks
    for (const sy of [-1, 1]) { const yy = oy + sy * r * .66; bracket(ox - r * .34, yy, 1, -sy, 12 * k + 4); bracket(ox + r * .34, yy, -1, -sy, 12 * k + 4); }
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) { g.moveTo(ox + dx * r * .78, oy + dy * r * .78); g.lineTo(ox + dx * r * 1.0, oy + dy * r * 1.0); }
    g.stroke();
    g.globalAlpha = 1;
  }
  if (th > .02) {                                                                   // thinking: tilted, spinning 3D ellipses
    g.globalAlpha = th;
    const hz = g.createRadialGradient(ox, oy, 0, ox, oy, r * .95); hz.addColorStop(0, 'rgba(255,40,90,.26)'); hz.addColorStop(1, 'rgba(255,40,90,0)'); g.fillStyle = hz; g.beginPath(); g.arc(ox, oy, r * .95, 0, TAU); g.fill();   // red haze behind the thick ellipse
    const E = (rad, i, fn) => { const sy = 1 - th * (.22 + .72 * Math.abs(Math.sin(S.spin * .7 + i * 1.3))), rot = S.spin * (.5 + .3 * i) + i * 1.9;
      g.save(); g.translate(ox, oy); g.rotate(rot); g.scale(1, sy); g.beginPath(); g.arc(0, 0, r * rad, 0, TAU); fn(); g.restore(); };
    E(.76, 0, () => { g.strokeStyle = 'rgba(255,31,77,.2)'; g.lineWidth = 22; g.stroke(); g.strokeStyle = '#ff2448'; g.lineWidth = 6; g.stroke(); });      // thick red neon ellipse ...
    E(.82, 0, () => { g.strokeStyle = 'rgba(255,255,255,.9)'; g.lineWidth = 1; g.stroke(); }); E(.7, 0, () => { g.strokeStyle = 'rgba(255,255,255,.7)'; g.lineWidth = 1; g.stroke(); });   // ... with thin white edges
    E(1.15, 1, () => { g.strokeStyle = 'rgba(255,255,255,.92)'; g.lineWidth = 1.6; g.stroke(); });
    E(1.34, 2, () => { g.setLineDash([2, 3]); g.strokeStyle = 'rgba(255,255,255,.85)'; g.lineWidth = 1.4; g.stroke(); g.setLineDash([]); });             // segmented / ticked ellipse
    E(1.58, 3, () => { g.setLineDash([26, 15]); g.strokeStyle = 'rgba(255,255,255,.7)'; g.lineWidth = 1.6; g.stroke(); g.setLineDash([]); });
    const n = S.lite ? 18 : 34, sy2 = .5 + .3 * Math.sin(S.spin * .55), rt = S.spin * .35;                         // sparse red/white blocks on a big tilted ring
    for (let i = 0; i < n; i++) { const a = i / n * TAU + S.spin * .25, rr = r * 1.9, px = Math.cos(a) * rr, py = Math.sin(a) * rr * sy2;
      g.fillStyle = i % 3 ? 'rgba(255,255,255,.75)' : 'rgba(255,50,90,.85)'; g.fillRect(ox + px * Math.cos(rt) - py * Math.sin(rt), oy + px * Math.sin(rt) + py * Math.cos(rt), i % 5 ? 2 : 4, 3); }
    const ang = -.42 + .22 * Math.sin(S.spin * .5), lg = g.createLinearGradient(ox - r * 2.3, 0, ox + r * 2.3, 0);      // long red line through the middle
    lg.addColorStop(0, 'rgba(255,30,70,0)'); lg.addColorStop(.5, 'rgba(255,80,110,1)'); lg.addColorStop(1, 'rgba(255,30,70,0)');
    g.save(); g.translate(ox, oy); g.rotate(ang); g.translate(-ox, -oy);
    g.globalAlpha = th * (.8 + .2 * Math.sin(S.spin * 4)); glow(lg, 1.8, () => { g.beginPath(); g.moveTo(ox - r * 2.3, oy); g.lineTo(ox + r * 2.3, oy); }); g.restore();
    g.globalAlpha = 1;
  }
}

/* ---------------------------------------------------------------- frame loop */
let last = performance.now(), skip = false, wordT = 0;
let idleN = 0;
function frame(now) { requestAnimationFrame(frame); if (!S.paused) tick(now); }
function tick(now) {
  const idle = S.mode === 'idle' && S.viewAmt < .01 && S.think < .01 && Math.abs(ringT.s - ring.s) < .01;
  if ((document.documentElement.classList.contains('embed') || document.documentElement.classList.contains('idlescr')) && !document.documentElement.classList.contains('dock')) { last = now; return; }   // the orb is hidden behind embedded content: do no drawing at all
  if (idle) { if ((idleN = (idleN + 1) % 4) !== 0) return; }                         // idle: about 15 fps is plenty for a slow breathing orb (was 30)
  else if (S.lite && (skip = !skip)) return;                                         // lite = 30 fps
  const dt = Math.max(0, Math.min(.1, (now - last) / 1000)); last = now;
  if (!S.lite) {                                                                     // auto-lite when frames are slow
    S.frameEma += (dt * 1000 - S.frameEma) * .05;
    if (S.frameEma > 34 && !idle) { S.slow += dt; if (S.slow > 2.5 && !P.has('nolite')) { S.slow = 0; setLite(true, false, 'LITE MODE ON (slow frames)'); } }
    else if (S.frameEma <= 34) S.slow = 0;
  }
  S.dbg = (S.dbg || 33) + (dt * 1000 - (S.dbg || 33)) * .1;                          // debug: smoothed ms per drawn frame
  if (P.has('demo')) demoTick(now);
  if (S.conv) S.tgt = Math.max(S.tgt, .12 + .08 * Math.sin(now * .004));                 // conversation: the orb breathes gently = your turn
  { const f = Math.min(1, dt * 2.5); S.mh = (S.mh || 0) + (moodT[0] - (S.mh || 0)) * f; S.mAmp = (S.mAmp || 1) + (moodT[1] - (S.mAmp || 1)) * f; S.mSpin = (S.mSpin || 1) + (moodT[2] - (S.mSpin || 1)) * f;
    const mh = Math.round(S.mh); if (mh !== S.moodHue) { S.moodHue = mh; applyLook(); } }                  // the orb's hue, pulse and spin ease toward the mood of what is being said
  S.lvl += (S.tgt - S.lvl) * Math.min(1, dt * 18); S.tgt *= Math.pow(.02, dt);
  S.think += ((S.mode === 'thinking' ? 1 : 0) - S.think) * Math.min(1, dt * 4);
  S.viewAmt += ((S.viewing ? 1 : 0) - S.viewAmt) * Math.min(1, dt * 5);
  setRingTarget(false);                                                              // re-derive every frame
  const hf = anyFull(); if (hf !== S.hasfull) { S.hasfull = hf; document.documentElement.classList.toggle('hasfull', hf); }
  ringStep(performance.now());
  const want = { thinking: 2.8, listening: .5 + S.lvl, speaking: .9 + S.lvl * 2 }[S.mode] || .25;
  S.spinV += (want * (S.mSpin || 1) - S.spinV) * Math.min(1, dt * 3); S.spin += S.spinV * dt;
  for (const s of specks) { s.x = (s.x + s.vx * dt + box) % box; s.y = (s.y + s.vy * dt + box) % box; }
  applyView();
  if (S.viewing && !(typeof LK !== 'undefined' && LK.open) && S.mode === 'idle' && performance.now() - S.viewT > 15000) hideView();   // never leave the orb parked in the corner
  if (S.base === 'rose' && !S.lite) {                                               // brush wordmark tilts in 3D while thinking
    const ry = S.think * Math.sin(S.spin * .8) * 55, rw = $('#rword').style; rw.transform = `perspective(500px) rotateY(${ry.toFixed(1)}deg) rotateZ(${(-3 - S.think * 6).toFixed(1)}deg)`; rw.opacity = (1 - S.think * .98).toFixed(2);
  }
  const t1 = performance.now(); S.base === 'jarvis' ? drawJarvis(now) : drawRose(now); S.drawMs = (S.drawMs || 0) + (performance.now() - t1 - (S.drawMs || 0)) * .1;
  if (P.has('fps') && (S.fc = (S.fc || 0) + 1) % 5 === 0) $('#stat').textContent = 'FPS ' + (1000 / S.dbg).toFixed(0) + (S.lite ? ' LITE ' : ' FULL ') + S.dbg.toFixed(0) + 'ms  draw ' + S.drawMs.toFixed(1) + 'ms';
}

/* ---------------------------------------------------------------- state / text / meter */
const LABEL = { loading: 'LOADING MODELS', idle: 'READY', listening: 'LISTENING', thinking: 'THINKING', speaking: 'SPEAKING', error: 'ERROR', offline: 'OFFLINE' };
let startT = 0, startTick = 0;
function startupChip(on) {                                                          // "STARTING UP..." with a running count while the server is away or loading its speech models; the orb keeps animating
  const e = $('#startup'); if (!e) return;
  if (on && !startT) { startT = performance.now(); startTick = setInterval(() => { const n = Math.round((performance.now() - startT) / 1000); e.textContent = `STARTING UP · ${n} S · loading the speech models, about a minute`; }, 1000); e.style.display = 'block'; e.textContent = 'STARTING UP · loading the speech models, about a minute'; }
  else if (!on && startT) { clearInterval(startTick); startT = 0; e.style.display = 'none'; }
}
function setMode(m, detail) {
  startupChip(m === 'offline' || m === 'loading');
  if (m !== 'idle' && idleOn && performance.now() - idleSince > 2500) idleShow(false);                                          // a wake word, a voice or an answer dismisses the clock at once
  S.mode = m; S.detail = detail || ''; S.conv = m === 'listening' && S.detail === 'conversation'; if (S.viewing) S.viewT = performance.now(); const s = $('#stat'); s.textContent = (m === 'listening' && /^manual/.test(S.detail)) ? 'MANUAL PRESS · MUTED' : S.conv ? 'YOUR TURN' : (LABEL[m] || m.toUpperCase()); s.classList.toggle('on', m !== 'idle');
  document.documentElement.classList.toggle('idle', m === 'idle'); showMute();
  if (m === 'error' && detail) showCap(detail);
}
let capT = 0;
function showCap(text) {                                                            // only the current sentence or two, tucked bottom-left, fading out
  const c = $('#cap'); c.querySelector('.ai').textContent = text; c.classList.add('show'); clearTimeout(capT);
  capT = setTimeout(() => c.classList.remove('show'), 4500 + Math.min(9000, text.length * 55));
}
function addMsg(role, text) {
  const d = document.createElement('div'); d.className = role; d.textContent = (role === 'you' ? '› ' : '') + text;
  const h = $('#hist'); h.appendChild(d); h.scrollTop = h.scrollHeight; while (h.children.length > 60) h.firstChild.remove();
  if (role === 'you') { clearTimeout(capT); $('#cap').classList.remove('show'); }
  else if (role === 'jarvis') showCap(text);
  else if (/^Error|Didn/.test(text)) showCap(text);
}

/* ---------------------------------------------------------------- look view + cards */
function showView(src) { $('#view').src = src; S.viewing = true; S.viewT = performance.now(); $('#vclose').style.display = 'block'; }
function hideView() { S.viewing = false; $('#vclose').style.display = 'none'; }
const cards = [], SLOTS = [[.81, .33], [.19, .33], [.81, .70], [.19, .70]];
function slotPos(i, el) {
  const [fx, fy] = SLOTS[i % SLOTS.length], w = el.offsetWidth, h = el.offsetHeight, m = Math.min(W, H) * .05;
  return [clamp(fx * W - w / 2, m, W - w - m), clamp(fy * H - h / 2, 100, H - h - 110)];   // clear of the chips (top) and dock (bottom)
}
function saveWidget(c) { if (!c.wkey || c.isFull) return; try { localStorage.setItem(c.wkey, JSON.stringify({ x: c.el.offsetLeft, y: c.el.offsetTop, w: c.el.offsetWidth, h: c.el.offsetHeight })); } catch (_) {} }
function placeCard(el, i) { const [x, y] = slotPos(i, el); el.style.left = x + 'px'; el.style.top = y + 'px'; }
function expandCard(c) {                                                             // card goes full screen; the orb shrinks to the bottom-right corner (derived from state)
  cards.forEach(o => o !== c && o.isFull && collapseCard(o));
  c.isFull = true; c.el.classList.add('full'); c.el.style.transform = ''; if (c.ex) c.ex.setAttribute('aria-label', 'back to a small panel'); updatePager();
  setTimeout(() => { c.map && c.map.invalidateSize(); c.redraw && c.redraw(); }, 60);
}
function collapseCard(c) {
  c.isFull = false; c.el.classList.remove('full'); c.el.style.transform = ''; if (c.ex) c.ex.setAttribute('aria-label', 'full screen'); updatePager();
  setTimeout(() => { c.map && c.map.invalidateSize(); c.redraw && c.redraw(); }, 60);
}
let batch = [], lastCardT = 0;
function updatePager() {
  const live = batch.filter(c => cards.includes(c)), idx = live.findIndex(c => c.isFull);
  cards.forEach(c => { const on = c.isFull && live.length > 1; c.pg.classList.toggle('on', on); if (on) c.pg.querySelector('i').textContent = (idx + 1) + '/' + live.length; });
}
function flip(c, d) { const live = batch.filter(x => cards.includes(x)), i = live.indexOf(c); if (i < 0) return; expandCard(live[(i + d + live.length) % live.length]); }
function closeCard(c) { sfx('close'); if (c.live) c.live.src = ''; if (c.onClose) { try { c.onClose(); } catch (_) {} } c.isFull = false; c.el.classList.remove('in'); c.el.classList.add('out'); setTimeout(() => { c.el.remove(); c.map && c.map.remove(); }, 380); const i = cards.indexOf(c); if (i >= 0) cards.splice(i, 1); }
function clearView() { [...cards].forEach(closeCard); hideView(); updatePager(); }
function tidy() { cards.forEach((c, i) => { c.moved = false; placeCard(c.el, i); }); }

function drawChart(canvas, card) {
  const c = canvas, x = c.getContext('2d'), w = c.width = c.clientWidth * 2, h = c.height = c.clientHeight * 2, v = card.values || [];
  if (!v.length) return; x.scale(2, 2); const W2 = w / 2, H2 = h / 2, p = 22, lo = Math.min(0, ...v), hi = Math.max(...v), span = (hi - lo) || 1, col = '#e8f2ff';
  x.strokeStyle = 'rgba(255,255,255,.2)'; x.lineWidth = 1; x.beginPath(); x.moveTo(p, 6); x.lineTo(p, H2 - p); x.lineTo(W2 - 6, H2 - p); x.stroke();
  x.font = '700 8px monospace'; x.fillStyle = '#9aa6c0'; x.fillText(String(hi), 2, 12); x.fillText(String(lo), 2, H2 - p);
  const px = i => p + (v.length === 1 ? .5 : i / (v.length - 1)) * (W2 - p - 12), py = val => 6 + (1 - (val - lo) / span) * (H2 - p - 12);
  x.strokeStyle = col; x.fillStyle = col; x.lineWidth = 2;
  if (card.kind === 'bar') { const bw = (W2 - p - 12) / v.length * .7; v.forEach((val, i) => { const bx = p + 6 + i * (W2 - p - 12) / v.length; x.fillRect(bx, py(val), bw, H2 - p - py(val)); }); }
  else { x.beginPath(); v.forEach((val, i) => i ? x.lineTo(px(i), py(val)) : x.moveTo(px(i), py(val))); x.stroke(); v.forEach((val, i) => { x.beginPath(); x.arc(px(i), py(val), 2.5, 0, TAU); x.fill(); }); }
  x.fillStyle = '#9aa6c0'; const L = card.labels || [], step = Math.ceil(L.length / 6);
  L.forEach((l, i) => { if (i % step === 0) x.fillText(l, card.kind === 'bar' ? p + 6 + i * (W2 - p - 12) / v.length : px(i) - 6, H2 - 8); });
}
const popAdd = c => { while (cards.length) closeCard(cards[0]); addCard(c); };     // the popup shows one card at a time: the newest

/* ---- guide cards: big plain steps (pair the Mac, set up Spotify): a command to copy, a code, a box, buttons, one live status line ---- */
const GUIDES = {};
function guideBuild(c, body, card) {
  let cur = 0; const steps = card.steps || [], paged = !!card.paged;
  const stat = document.createElement('div'); stat.className = 'gstat'; stat.textContent = card.status || '';
  const setStatus = (text, state) => { stat.textContent = text; stat.dataset.state = state || ''; };
  GUIDES[card.gid] = { setStatus, next: () => { if (cur < steps.length - 1) { cur++; render(); } } };
  const act = (name, payload) => { send({ t: 'action', name, payload: payload || {} }); };
  function render() {
    body.textContent = '';
    steps.forEach((st, i) => {
      if (paged && i !== cur) return;
      const box = document.createElement('div'); box.className = 'gstep' + (st.optional ? ' opt' : '');
      const h = document.createElement('div'); h.className = 'gtxt'; h.textContent = (paged ? `STEP ${i + 1} OF ${steps.length}  ` : st.n + '.  ') + (st.optional ? '(optional) ' : '') + st.text; box.append(h);
      if (st.cmd) { const pre = document.createElement('pre'); pre.className = 'gcmd'; pre.textContent = st.cmd; const cp = document.createElement('button'); cp.className = 'tiny box gcopy'; cp.textContent = 'COPY';
        cp.onclick = () => { copyText(st.cmd).then(ok => { cp.textContent = ok ? 'COPIED' : 'SELECT + COPY'; }); }; box.append(pre, cp); }
      if (st.showCode && card.code) { const cd = document.createElement('div'); cd.className = 'gcode'; cd.textContent = card.code; box.append(cd); }
      if (st.copy) { const row = document.createElement('div'); row.className = 'gcmd'; row.textContent = st.copy.mask; const cb = document.createElement('button'); cb.className = 'tiny box gcopy'; cb.textContent = st.copy.label;
        cb.onclick = () => { send({ t: st.copy.action }); cb.textContent = 'COPIED'; }; box.append(row, cb); }
      if (st.hint) { const ht = document.createElement('div'); ht.className = 'ghint'; ht.textContent = st.hint; box.append(ht); }
      if (st.input) { const inp = document.createElement('input'); inp.className = 'gin'; inp.placeholder = st.input.placeholder || ''; inp.autocomplete = 'off'; inp.spellcheck = false;
        const sv = document.createElement('button'); sv.className = 'tiny box'; sv.textContent = st.input.label || 'SAVE';
        sv.onclick = () => { const v = inp.value.trim(); inp.value = ''; sug.textContent = ''; if (v) act(st.input.action, { value: v }); }; box.append(inp, sv);
        const sug = document.createElement('div'); sug.className = 'gsug'; box.append(sug); let sugT = 0, sugSeq = 0;
        if (st.input.suggest) inp.oninput = () => { clearTimeout(sugT); const q = inp.value.trim(); if (q.length < 4) { sug.textContent = ''; return; }
          sugT = setTimeout(async () => { const mine = ++sugSeq; let l = []; try { l = await fetch('/api/geosuggest?q=' + encodeURIComponent(q)).then(r => r.json()); } catch (_) {} if (mine !== sugSeq) return;
            sug.textContent = ''; l.forEach(x => { const b = document.createElement('button'); b.className = 'tiny box gsb'; b.textContent = x.label; b.onclick = () => { inp.value = ''; sug.textContent = ''; act(st.input.pick, x); }; sug.append(b); });
            if (!l.length) { const e = document.createElement('div'); e.className = 'ghint'; e.textContent = 'no suggestions yet: keep typing, or press FIND'; sug.append(e); } }, 350); }; }
      (st.buttons || []).forEach(b => { const bt = document.createElement('button'); bt.className = 'tiny box'; bt.textContent = b.label; bt.onclick = () => act(b.action, b.payload); box.append(bt); });
      body.append(box);
    });
    const row = document.createElement('div'); row.className = 'grow';
    if (paged) { if (cur > 0) { const b = document.createElement('button'); b.className = 'tiny box'; b.textContent = 'BACK'; b.onclick = () => { cur--; render(); }; row.append(b); }
      if (cur < steps.length - 1) { const b = document.createElement('button'); b.className = 'tiny box'; b.textContent = 'DONE, NEXT'; b.onclick = () => { cur++; render(); }; row.append(b); } }
    (card.buttons || []).forEach(b => { const bt = document.createElement('button'); bt.className = 'tiny box'; bt.textContent = b.label; bt.onclick = () => act(b.action, b.payload); row.append(bt); });
    body.append(row, stat);
  }
  render();
}

/* ---- preferences: the server owns them (settings.json); the page shows them and sends changes ---- */
const PREFS = { speed: 1, volume: 50, auto_visuals: true, stay_out: true, idle_minutes: 5, clock24: false, clock_seconds: true, home: '' };
const IDLE_CHOICES = [1, 2, 5, 10, 15, 30, 60, 0], idleLabel = m => m === 0 ? 'NEVER' : m === 60 ? '1 HOUR' : m + ' MIN';
const fmtX = v => v.toFixed(2).replace(/0$/, '') + '×';
function showPrefs() {
  const sp = $('#s-speed'); if (!sp) return;
  if (document.activeElement !== sp) sp.value = Math.round(PREFS.speed * 100); $('#s-speedl').textContent = fmtX(+sp.value / 100);
  const vl = $('#s-vol'); if (document.activeElement !== vl) vl.value = PREFS.volume; $('#s-voll').textContent = vl.value + '%';
  $('#s-heard').textContent = 'SHOW WHAT I HEARD · ' + (PREFS.show_heard === false ? 'OFF' : 'ON'); $('#s-large').textContent = 'LARGE TEXT · ' + (PREFS.large_text ? 'ON' : 'OFF'); $('#s-hc').textContent = 'HIGH CONTRAST · ' + (PREFS.high_contrast ? 'ON' : 'OFF');
  document.documentElement.classList.toggle('large', !!PREFS.large_text); document.documentElement.classList.toggle('hc', !!PREFS.high_contrast);
  $('#s-chime').textContent = 'SOUNDS · ' + (PREFS.chimes === false ? 'OFF' : 'ON'); $('#s-feel').textContent = 'FEELINGS · ' + (PREFS.feelings === false ? 'OFF' : 'ON'); { const cv = $('#s-cvol'); if (document.activeElement !== cv) cv.value = PREFS.chime_volume ?? 60; $('#s-cvoll').textContent = cv.value + '%'; }
  $('#s-autoprom').textContent = 'AUTO-SAVE · ' + (PREFS.auto_promises ? 'ON' : 'OFF');
  $('#s-tbox').textContent = { alt: 'ALT+SPACE', ctrl: 'CTRL+SHIFT+SPACE', both: 'BOTH SHORTCUTS', off: 'OFF' }[PREFS.typebox_key || 'alt'];
  $('#s-bootanim').textContent = 'START-UP ANIMATION · ' + (PREFS.boot_anim === false ? 'OFF' : 'ON');
  $('#s-patience').textContent = 'PATIENCE · ' + (PREFS.listen_patience || 'fast').toUpperCase();
  $('#s-orbavoid').textContent = (PREFS.orb_avoid || 'top-right').toUpperCase().replace('NONE', 'NO CORNER');
  $('#s-keepph').textContent = 'KEEP PHOTOS · ' + (PREFS.keep_phone_photos ? 'ON' : 'OFF');
  $('#s-auto3d').textContent = 'AUTO 3D · ' + (PREFS.auto_3d ? 'ON' : 'OFF');
  $('#s-mktdesk').textContent = 'MARKETS DESK · ' + (PREFS.markets_desk === false ? 'OFF' : 'ON');
  $('#s-camwarn').textContent = 'CAMERA WARNINGS · ' + (PREFS.camera_warnings ? 'ON' : 'OFF');
  $('#s-twake').textContent = 'TERMINAL WAKE · ' + (PREFS.term_wake ? 'ON' : 'OFF');
  $('#s-warn').textContent = 'MACHINE WARNINGS · ' + (PREFS.machine_warnings === false ? 'OFF' : 'ON');
  $('#s-auto').textContent = 'AUTO VISUALS · ' + (PREFS.auto_visuals ? 'ON' : 'OFF'); $('#s-stay').textContent = 'STAY OUT · ' + (PREFS.stay_out ? 'ON' : 'OFF');
  const id = $('#s-idle'); if (document.activeElement !== id) id.value = Math.max(0, IDLE_CHOICES.indexOf(PREFS.idle_minutes)); $('#s-idlel').textContent = idleLabel(IDLE_CHOICES[+id.value]);
  $('#s-c24').textContent = PREFS.clock24 ? '24 HOUR' : '12 HOUR'; $('#s-csec').textContent = 'SECONDS · ' + (PREFS.clock_seconds ? 'ON' : 'OFF');
  $('#s-lochint').textContent = PREFS.home ? 'Home: ' + PREFS.home : 'Type your address; it is shown on a map to confirm.';
  if (typeof clockPrefsChanged === 'function') clockPrefsChanged();
}
const setPref = (key, value) => { PREFS[key] = value; showPrefs(); send({ t: 'setting', key, value }); };
/* sliders: pointer-events on the control itself (the old one sat under a label's invisible touch-extender), a big thumb, − and + buttons, and a send only when you let go */
const slider = (id, minus, plus, step, fmt, commit) => { const el = $(id); el.addEventListener('pointerdown', e => e.stopPropagation()); el.addEventListener('input', () => fmt(+el.value)); el.addEventListener('change', () => commit(+el.value));
  const nudge = d => { el.value = Math.min(+el.max, Math.max(+el.min, +el.value + d * step)); fmt(+el.value); commit(+el.value); }; $(minus).onclick = () => nudge(-1); $(plus).onclick = () => nudge(1); };
slider('#s-speed', '#s-spm', '#s-spp', 5, v => { $('#s-speedl').textContent = fmtX(v / 100); }, v => { setPref('speed', v / 100); if (v > 180) toast('ABOVE 1.8× IT BARELY GETS FASTER AND CAN SOUND RUSHED: 1.6× IS THE SWEET SPOT'); });
slider('#s-cvol', '#s-cvm', '#s-cvp', 5, v => { $('#s-cvoll').textContent = v + '%'; }, v => setPref('chime_volume', v));
$('#s-rems').onclick = () => { closeSettings(); ask('what are my reminders'); }; $('#s-proms').onclick = () => { closeSettings(); ask('what did I promise'); }; $('#s-autoprom').onclick = () => setPref('auto_promises', !PREFS.auto_promises);
$('#s-tbox').onclick = () => { const o = ['alt', 'ctrl', 'both', 'off']; setPref('typebox_key', o[(o.indexOf(PREFS.typebox_key || 'alt') + 1) % 4]); }; $('#s-tboxgo').onclick = () => { closeSettings(); send({ t: 'action', name: 'typebox_open', payload: {} }); };
$('#s-howto').onclick = () => { closeSettings(); send({ t: 'action', name: 'howto_ask', payload: {} }); }; $('#s-circle').onclick = () => { closeSettings(); send({ t: 'action', name: 'circle_open', payload: {} }); };
$('#s-bootanim').onclick = () => setPref('boot_anim', PREFS.boot_anim === false);
$('#s-patience').onclick = () => { const o = ['fast', 'normal', 'patient']; setPref('listen_patience', o[(o.indexOf(PREFS.listen_patience || 'fast') + 1) % 3]); };
$('#s-orbreset').onclick = () => send({ t: 'action', name: 'orb_reset', payload: {} }); $('#s-orbavoid').onclick = () => { const o = ['top-right', 'top-left', 'bottom-left', 'bottom-right', 'none']; setPref('orb_avoid', o[(o.indexOf(PREFS.orb_avoid || 'top-right') + 1) % 5]); };
$('#s-keepph').onclick = () => setPref('keep_phone_photos', !PREFS.keep_phone_photos);
$('#s-auto3d').onclick = () => setPref('auto_3d', !PREFS.auto_3d); $('#s-sendexp').onclick = () => send({ t: 'action', name: 'holo_send_export', payload: {} }); $('#s-clearmodels').onclick = () => send({ t: 'action', name: 'models_clear', payload: {} });
$('#s-mktdesk').onclick = () => setPref('markets_desk', PREFS.markets_desk === false); $('#s-mktopen').onclick = () => { closeSettings(); send({ t: 'action', name: 'mkt_open', payload: {} }); };
$('#s-mktkeysave').onclick = () => { const i = $('#s-mktkey'); const v = i.value.trim(); i.value = ''; if (v) send({ t: 'action', name: 'mkt_key', payload: { value: v } }); toast(v ? 'KEY SENT' : 'PASTE A KEY FIRST'); };
$('#s-mktcontactsave').onclick = () => { const i = $('#s-mktcontact'); send({ t: 'action', name: 'mkt_contact', payload: { value: i.value } }); toast('SAVED'); };
$('#s-camwarn').onclick = () => setPref('camera_warnings', !PREFS.camera_warnings);
$('#s-chime').onclick = () => setPref('chimes', PREFS.chimes === false); $('#s-feel').onclick = () => setPref('feelings', PREFS.feelings === false);
slider('#s-vol', '#s-vm', '#s-vp', 5, v => { $('#s-voll').textContent = v + '%'; }, v => setPref('volume', v));
slider('#s-idle', '#s-idm', '#s-idp', 1, v => { $('#s-idlel').textContent = idleLabel(IDLE_CHOICES[v]); }, v => setPref('idle_minutes', IDLE_CHOICES[v]));
$('#s-heard').onclick = () => setPref('show_heard', PREFS.show_heard === false); $('#s-large').onclick = () => setPref('large_text', !PREFS.large_text); $('#s-hc').onclick = () => setPref('high_contrast', !PREFS.high_contrast);
$('#s-act').onclick = () => { closeSettings(); ask('what happened'); };
$('#s-warn').onclick = () => setPref('machine_warnings', PREFS.machine_warnings === false);
$('#s-auto').onclick = () => setPref('auto_visuals', !PREFS.auto_visuals); $('#s-stay').onclick = () => setPref('stay_out', !PREFS.stay_out);
$('#s-c24').onclick = () => setPref('clock24', !PREFS.clock24); $('#s-csec').onclick = () => setPref('clock_seconds', !PREFS.clock_seconds);
/* the panel: collapsible sections, search, reset-to-default per section, close */
const closeSettings = () => { $('#settings').classList.remove('on'); document.documentElement.classList.remove('sopen'); };
$('#sp-close').onclick = closeSettings;
document.querySelectorAll('#settings .sgh').forEach(h => h.onclick = () => { const sg = h.parentElement; sg.classList.toggle('open'); try { localStorage.setItem('sg-' + sg.dataset.sg, sg.classList.contains('open') ? '1' : '0'); } catch (_) {} });
document.querySelectorAll('#settings .sg').forEach(sg => { try { const v = localStorage.getItem('sg-' + sg.dataset.sg); if (v) sg.classList.toggle('open', v === '1'); } catch (_) {} });
$('#sp-search').oninput = () => { const q = $('#sp-search').value.trim().toLowerCase(); document.querySelectorAll('#settings .sg').forEach(sg => { let any = false;
  sg.querySelectorAll('.srow').forEach(r => { const hit = !q || r.textContent.toLowerCase().includes(q) || sg.dataset.sg.includes(q); r.classList.toggle('hide', !hit); any = any || hit; });
  sg.classList.toggle('hide', !!q && !any); if (q && any) sg.classList.add('open'); }); };
const RESETS = { voice: () => { setPref('speed', 1.0); followup = false; send({ t: 'followup', on: false }); showFollow(); }, display: () => { setPref('auto_visuals', true); setLite(false); },
  screensaver: () => { setPref('idle_minutes', 5); setPref('clock24', false); setPref('clock_seconds', true); }, advanced: () => { setPref('stay_out', true); setPref('machine_warnings', true); } };
document.querySelectorAll('#settings .sgreset').forEach(b => b.onclick = () => { (RESETS[b.dataset.reset] || (() => {}))(); b.textContent = 'RESET ✓'; setTimeout(() => { b.textContent = 'RESET THIS SECTION'; }, 1200); });
const ask = text => send({ t: 'ask', text });                                       // a few fixed requests from buttons (the server only accepts an allowlist)
$('#s-iphone').onclick = () => { closeSettings(); ask('set up the iphone voice'); };
$('#s-mac').onclick = () => { closeSettings(); ask('pair my Mac'); }; $('#s-spot').onclick = () => { closeSettings(); ask('set up Spotify'); }; $('#s-clk').onclick = () => { closeSettings(); ask('show the clock now'); };
const nextPersona = () => { const ids = Object.keys(PERS); if (ids.length < 2) return S.theme === 'jarvis' ? 'rose' : 'jarvis'; return ids[(ids.indexOf(S.theme) + 1) % ids.length]; };
$('#s-theme').onclick = () => setTheme(nextPersona());
$('#s-hf').onclick = () => $('#i-mute').click();
$('#s-src').onclick = () => setSource(SOURCES[(SOURCES.indexOf(S.source) + 1) % SOURCES.length]);
$('#s-loc').onclick = () => { closeSettings(); send({ t: 'locsetup' }); };
$('#s-pcam').onclick = () => { closeSettings(); send({ t: 'phonecam_setup' }); };
$('#s-snd').onclick = () => { send({ t: 'soundtest' }); };
$('#settings').addEventListener('pointerdown', e => e.stopPropagation());              // nothing underneath (press-to-talk, the menu) may take a press made inside the panel

/* ---- explain cards: a small drawing beside the answer (diagram, timeline, labelled picture, spinning 3D shape) with the key sentence underlined; plain text if drawing fails ---- */
const SVGNS = 'http://www.w3.org/2000/svg';
const svgEl = (n, a = {}, t) => { const e = document.createElementNS(SVGNS, n); for (const k in a) e.setAttribute(k, a[k]); if (t != null) e.textContent = t; return e; };
function explainDiagram(card) {
  const nodes = card.nodes || [], edges = card.edges || []; if (!nodes.length) throw new Error('no nodes');
  const level = {}; nodes.forEach(n => level[n.id] = 0);
  for (let i = 0; i < nodes.length; i++) edges.forEach(([a, b]) => { if (level[b] <= level[a] && level[a] + 1 < nodes.length) level[b] = level[a] + 1; });         // layers from the arrows
  const layers = []; nodes.forEach(n => (layers[level[n.id]] = layers[level[n.id]] || []).push(n));
  const W = 560, rowH = 74, H = Math.max(1, layers.filter(Boolean).length) * rowH + 10, svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'xsvg' }), pos = {};
  { const df = svgEl('defs'), mk = svgEl('marker', { id: 'ah', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto' }); mk.append(svgEl('path', { d: 'M0 0L10 5L0 10z', fill: 'currentColor' })); df.append(mk); svg.append(df); }
  layers.filter(Boolean).forEach((row, r) => row.forEach((n, i) => { const bw = Math.min(150, (W - 20) / row.length - 14), cx = (i + .5) * W / row.length; pos[n.id] = { x: cx, y: r * rowH + 36, w: bw }; }));
  edges.forEach(([a, b, lab]) => { const p = pos[a], q = pos[b]; if (!p || !q) return; const dy = q.y - p.y, x1 = p.x, y1 = p.y + 18, x2 = q.x, y2 = q.y - 20 + (dy ? 0 : 20);
    svg.append(svgEl('line', { x1, y1, x2, y2, class: 'xedge', 'marker-end': 'url(#ah)' })); if (lab) svg.append(svgEl('text', { x: (x1 + x2) / 2 + 4, y: (y1 + y2) / 2 + 3, class: 'xlab' }, lab)); });
  nodes.forEach(n => { const p = pos[n.id]; if (!p) return; svg.append(svgEl('rect', { x: p.x - p.w / 2, y: p.y - 18, width: p.w, height: 36, rx: 6, class: 'xnode' })); svg.append(svgEl('text', { x: p.x, y: p.y + 4, 'text-anchor': 'middle', class: 'xtxt' }, n.label.length > 22 ? n.label.slice(0, 21) + '…' : n.label)); });
  return svg;
}
function explainTimeline(card) {
  const ev = card.events || []; if (!ev.length) throw new Error('no events'); const W = 560, H = 150, svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'xsvg' });
  svg.append(svgEl('line', { x1: 20, y1: 70, x2: W - 20, y2: 70, class: 'xedge' }));
  ev.forEach((e, i) => { const x = 80 + i * (W - 160) / Math.max(1, ev.length - 1), up = i % 2 === 0; svg.append(svgEl('circle', { cx: x, cy: 70, r: 6, class: 'xdot' })); svg.append(svgEl('line', { x1: x, y1: 70, x2: x, y2: up ? 48 : 92, class: 'xedge' }));
    svg.append(svgEl('text', { x, y: up ? 40 : 108, 'text-anchor': 'middle', class: 'xlab xbold' }, e.when)); const words = e.text.split(' '), lines = []; let cur = ''; words.forEach(w => { if ((cur + ' ' + w).length > 15) { lines.push(cur); cur = w; } else cur = (cur + ' ' + w).trim(); }); lines.push(cur);
    lines.slice(0, 3).forEach((ln, k) => svg.append(svgEl('text', { x, y: (up ? 16 : 122) + k * 12 - (up ? (lines.length > 1 ? 4 : 0) : 0), 'text-anchor': 'middle', class: 'xtxt xsmall' }, ln))); });
  return svg;
}
function explainLabelled(card) {
  if (!card.src) throw new Error('no picture'); const box = document.createElement('div'); box.className = 'xpic'; const im = new Image(); im.referrerPolicy = 'no-referrer'; im.src = card.src; box.append(im);
  const leg = document.createElement('ol'); leg.className = 'xleg'; (card.labels || []).forEach((l, i) => { const pin = document.createElement('span'); pin.className = 'xpin'; pin.textContent = i + 1; pin.style.left = l.x * 100 + '%'; pin.style.top = l.y * 100 + '%'; box.append(pin); const li = document.createElement('li'); li.textContent = l.text; leg.append(li); });
  const wrap = document.createElement('div'); wrap.className = 'xwrap'; wrap.append(box, leg); return wrap;
}
function explainSpin(card, c) {                                                       // wireframe shape on one small canvas, 30 fps at most, stops when the card is closed or hidden
  const cv = document.createElement('canvas'); cv.className = 'xcv'; cv.width = 300; cv.height = 220; const g2 = cv.getContext('2d'); let a = 0, last = 0, alive = true;
  const V = { cube: [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], pyramid: [[-1, 1, -1], [1, 1, -1], [1, 1, 1], [-1, 1, 1], [0, -1.2, 0]] };
  const E = { cube: [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]], pyramid: [[0, 1], [1, 2], [2, 3], [3, 0], [0, 4], [1, 4], [2, 4], [3, 4]] };
  const shape = card.shape || 'cube', pts = [], segs = [];
  if (shape === 'sphere' || shape === 'ring') { const R2 = shape === 'ring' ? 2 : 1; for (let i = 0; i < (shape === 'ring' ? 2 : 3); i++) { const base = pts.length; for (let k = 0; k < 36; k++) { const t = k / 36 * Math.PI * 2, r = shape === 'ring' ? [1.3, .7][i] : 1.2; const p = [Math.cos(t) * r, Math.sin(t) * r, shape === 'ring' ? 0 : 0];
        pts.push(shape === 'sphere' ? (i === 0 ? [p[0], p[1], 0] : i === 1 ? [p[0], 0, p[1]] : [0, p[0], p[1]]) : [p[0], p[1] * .5, 0]); segs.push([base + k, base + (k + 1) % 36]); } } }
  else { V[shape].forEach(v => pts.push(v)); E[shape].forEach(e => segs.push(e)); }
  const col = () => getComputedStyle(document.documentElement).getPropertyValue('--card').trim() || '#9cc2ff';
  function frame(t) { if (!alive || !cv.isConnected) return; requestAnimationFrame(frame); if (t - last < 33 || document.hidden) return; last = t; a += .02;
    const ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(a * .6), sb = Math.sin(a * .6); g2.clearRect(0, 0, 300, 220); g2.strokeStyle = col(); g2.lineWidth = 1.6; g2.shadowColor = col(); g2.shadowBlur = 6;
    const P = pts.map(([x, y, z]) => { let x1 = x * ca + z * sa, z1 = -x * sa + z * ca, y1 = y * cb - z1 * sb, z2 = y * sb + z1 * cb, f = 140 / (4 + z2); return [150 + x1 * f * .9, 110 + y1 * f * .9]; });
    g2.beginPath(); segs.forEach(([i, j]) => { g2.moveTo(...P[i]); g2.lineTo(...P[j]); }); g2.stroke(); }
  requestAnimationFrame(frame); const prev = c.onClose; c.onClose = () => { alive = false; if (prev) prev(); };
  const w = document.createElement('div'); w.className = 'xwrap'; w.append(cv); if (card.caption) { const cp = document.createElement('div'); cp.className = 'xcap'; cp.textContent = card.caption; w.append(cp); } return w;
}
function explainBuild(c, body, card) {
  body.classList.add('xplain');
  try {
    const k = card.kind || 'diagram', fig = k === 'timeline' ? explainTimeline(card) : k === 'labelled' ? explainLabelled(card) : k === 'spin3d' ? explainSpin(card, c) : k === 'draw' ? explainDraw(card, c) : explainDiagram(card); body.append(fig);
  } catch (e) { const fb = document.createElement('div'); fb.className = 'xfb'; fb.textContent = card.text || card.key || card.title || ''; body.append(fb); }      // plain-text fallback
  if (card.key) { const key = document.createElement('div'); key.className = 'xkey'; const u = document.createElement('u'); u.textContent = card.key; key.append(u); body.append(key); }
  if (card.text && !body.querySelector('.xfb')) { const tx = document.createElement('div'); tx.className = 'xtext'; tx.textContent = card.text; body.append(tx); }
}
const MKT = { ctl: null, card: null, pending: [] };
function mktBuild(c, body, card) {                                                 // the Markets Desk card: read-only, loaded on demand like the 3D card
  body.classList.add('media'); body.textContent = 'Opening the desk…';
  import('/static/markets.js').then(m => { if (!c.el.isConnected) return; body.textContent = ''; const ctl = m.mount(body, card, { send, toast }); MKT.ctl = ctl; MKT.card = c; const prev = c.onClose;
    c.onClose = () => { try { ctl.dispose(); } catch (_) {} if (MKT.card === c) { MKT.ctl = null; MKT.card = null; send({ t: 'mkt_closed' }); } if (prev) prev(); };
    if (MKT.pending.length) { ctl.apply(MKT.pending); MKT.pending = []; } }).catch(() => { body.textContent = 'The desk could not start on this screen.'; });
}
const HOLO = { ctl: null, card: null };
function holoBuild(c, body, card) {                                                // the 3D card: the viewer module loads only now (three.js is not part of the page until a model opens)
  body.classList.add('media'); body.textContent = 'Building the model…';
  const api = { send, toast, look: () => ({ accent: (PERS[S.theme] || {}).accent || (S.base === 'rose' ? '#ff2d55' : '#3d8bff'), lite: S.lite, base: S.base }) };
  import('/static/holo.js').then(m => { if (!c.el.isConnected) return; body.textContent = ''; const ctl = m.mount(body, card, api); HOLO.ctl = ctl; HOLO.card = c; const prev = c.onClose; c.onClose = () => { try { ctl.dispose(); } catch (_) {} if (HOLO.card === c) { HOLO.ctl = null; HOLO.card = null; send({ t: 'holo_closed' }); } if (prev) prev(); }; send({ t: 'holo_open', title: card.title, parts: ctl.parts ? ctl.parts() : [] }); })
    .catch(() => { body.textContent = 'The 3D viewer could not start on this screen.'; });
}
const DRAWS = new Set();                                                           // open animated drawings: revealed one spoken sentence at a time (a slow timer is the fallback when nothing is spoken)
function explainDraw(card, c) {
  const els = card.elements || []; if (!els.length) throw new Error('empty'); const W = 560, H = 380, svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'xsvg xdraw' });
  const dm = svgEl('defs'), mk = svgEl('marker', { id: 'dah', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto' }); mk.append(svgEl('path', { d: 'M0 0L10 5L0 10z', fill: 'currentColor' })); dm.append(mk); svg.append(dm);
  const X = v => v * W, Y = v => v * H, nodes = [];
  const lab = (x, y, t, a = 'middle') => svgEl('text', { x, y, 'text-anchor': a, class: 'xtxt' }, t);
  els.forEach(e => { const g = svgEl('g', { class: 'xel' }); g.style.opacity = 0; g.dataset.s = e.s || 0;
    if (e.t === 'arrow') { g.append(svgEl('line', { x1: X(e.x1), y1: Y(e.y1), x2: X(e.x2), y2: Y(e.y2), class: 'xedge', 'marker-end': 'url(#dah)' })); if (e.label) g.append(lab((X(e.x1) + X(e.x2)) / 2, (Y(e.y1) + Y(e.y2)) / 2 - 6, e.label)); }
    else if (e.t === 'circle') { g.append(svgEl('circle', { cx: X(e.x), cy: Y(e.y), r: e.r * W, class: 'xnode' })); if (e.label) g.append(lab(X(e.x), Y(e.y) + 4, e.label)); }
    else if (e.t === 'box') { g.append(svgEl('rect', { x: X(e.x), y: Y(e.y), width: e.w * W, height: e.h * H, rx: 6, class: 'xnode' })); if (e.label) g.append(lab(X(e.x) + e.w * W / 2, Y(e.y) + e.h * H / 2 + 4, e.label)); }
    else if (e.t === 'point') { g.append(svgEl('circle', { cx: X(e.x), cy: Y(e.y), r: 5, class: 'xdot' })); if (e.label) g.append(lab(X(e.x) + 9, Y(e.y) + 4, e.label, 'start')); }
    else if (e.t === 'curve') { g.append(svgEl('path', { d: e.pts.map((p, i) => (i ? 'L' : 'M') + X(p[0]) + ' ' + Y(p[1])).join(' '), class: 'xedge', fill: 'none' })); if (e.label) g.append(lab(X(e.pts[0][0]), Y(e.pts[0][1]) - 8, e.label)); }
    else if (e.t === 'text') g.append(lab(X(e.x), Y(e.y), e.text));
    svg.append(g); nodes.push(g); });
  let shown = -1; const reveal = n => { if (n <= shown) return; shown = n; nodes.forEach(g => { if (+g.dataset.s <= n) { g.style.transition = 'opacity .5s'; g.style.opacity = 1; } }); };
  const maxS = Math.max(...els.map(e => e.s || 0)); let tick = 0; reveal(0);
  const timer = setInterval(() => { tick++; if (!svg.isConnected) { clearInterval(timer); DRAWS.delete(api); return; } if (tick % 3 === 0 && !DRAWS.spoken) reveal(Math.min(maxS, shown + 1)); }, 1000);
  const api = { reveal }; DRAWS.add(api); const prev = c.onClose; c.onClose = () => { clearInterval(timer); DRAWS.delete(api); if (prev) prev(); };
  return svg;
}
const MUSIC = {};                                                                    // the open music card (if any): updated by {t:'musicstate'}
/* ---- NEWS card: headlines with source, time and a picture when the feed has one; tap one to read it in the frame ---- */
function newsBuild(c, body, card) {
  body.classList.add('newsb'); const ul = document.createElement('div'); ul.className = 'nws';
  (card.items || []).forEach((it, i) => { const row = document.createElement('button'); row.className = 'nrow'; if (it.image) { const im = new Image(); im.loading = 'lazy'; im.referrerPolicy = 'no-referrer'; im.alt = ''; im.src = it.image; im.onerror = () => im.remove(); row.append(im); }
    const t = document.createElement('div'); t.className = 'nt'; const h = document.createElement('b'); h.textContent = it.title; const m = document.createElement('small'); m.textContent = (it.source + (it.ago ? ' · ' + it.ago : '')).toUpperCase(); t.append(h, m); row.append(t);
    row.onclick = () => send({ t: 'action', name: 'open_link', payload: { url: it.link } }); ul.append(row); });
  const up = document.createElement('div'); up.className = 'wxup'; up.textContent = 'updated ' + (card.updated || ''); body.append(ul, up);
}
/* ---- CHAT widget: terminal style transcript ("$" me, ">" Jarvis), an input line, an image button and send. Typed text goes through the same router as voice. ---- */
const CHAT = { log: [], views: new Set() };
function chatPush(who, text) { if (!text) return; CHAT.log.push({ who, text }); if (CHAT.log.length > 60) CHAT.log.shift(); CHAT.views.forEach(v => v.add(who, text)); }
function chatBuild(c, body, card) {
  body.classList.add('chatb'); const log = document.createElement('div'); log.className = 'clog'; const form = document.createElement('form'); form.className = 'cform';
  const inp = document.createElement('input'); inp.type = 'text'; inp.placeholder = 'type to Jarvis'; inp.autocomplete = 'off'; inp.spellcheck = false; inp.setAttribute('aria-label', 'message');
  const img = document.createElement('button'); img.type = 'button'; img.className = 'box'; img.textContent = '▣'; img.setAttribute('aria-label', 'attach an image'); const file = document.createElement('input'); file.type = 'file'; file.accept = 'image/*'; file.hidden = true;
  const go = document.createElement('button'); go.type = 'submit'; go.className = 'box'; go.textContent = '➤'; go.setAttribute('aria-label', 'send'); form.append(inp, img, go, file); body.append(log, form);
  const add = (who, text) => { const l = document.createElement('div'); l.className = 'cl ' + who; const p = document.createElement('span'); p.className = 'cp'; p.textContent = who === 'me' ? '$' : '>'; const t = document.createElement('span'); t.textContent = text; l.append(p, t); log.append(l); log.scrollTop = log.scrollHeight; };
  CHAT.log.forEach(m => add(m.who, m.text)); const view = { add }; CHAT.views.add(view); c.onClose = () => CHAT.views.delete(view);
  let imgData = null; img.onclick = () => file.click(); file.onchange = () => { const f = file.files[0]; if (!f || f.size > 6e6) { toast('IMAGE TOO BIG (6 MB)'); return; } const r = new FileReader(); r.onload = () => { imgData = String(r.result); img.classList.add('on'); }; r.readAsDataURL(f); };
  form.onsubmit = e => { e.preventDefault(); const t = inp.value.trim(); if (!t && !imgData) return; send({ t: 'typed', text: t || 'what is this?', image: imgData }); inp.value = ''; imgData = null; img.classList.remove('on'); };
  setTimeout(() => inp.focus(), 250);
}
/* ---- FLASHCARD: the question, tap to flip, "got it" / "missed" (missed ones come back later), next and back ---- */
function flashBuild(c, body, card) {
  body.classList.add('flashb'); const q = document.createElement('div'); q.className = 'fq'; q.textContent = card.q; const a = document.createElement('div'); a.className = 'fa'; a.textContent = card.a; a.hidden = true;
  const n = document.createElement('small'); n.className = 'fn'; n.textContent = `CARD ${card.i} OF ${card.n}` + (card.wrong ? ` · ${card.wrong} TO REVISIT` : ''); const row = document.createElement('div'); row.className = 'frow';
  const mk = (t, op, cls) => { const b = document.createElement('button'); b.className = 'box ' + (cls || ''); b.textContent = t; b.onclick = () => { send({ t: 'action', name: 'learn_ctl', payload: { op } }); if (op !== 'flip') closeCard(c); }; row.append(b); return b; };
  const flip = document.createElement('button'); flip.className = 'box'; flip.textContent = 'SHOW ANSWER'; flip.onclick = () => { a.hidden = !a.hidden; flip.textContent = a.hidden ? 'SHOW ANSWER' : 'HIDE ANSWER'; };
  body.append(n, q, a, flip); mk('BACK', 'prev'); mk('MISSED', 'wrong'); mk('GOT IT', 'right'); mk('NEXT', 'next'); body.append(row);
}
function choiceBuild(c, body, card) {                                              // a few big choices, each a button with a short line under it; one tap decides
  body.classList.add('choice'); const st = document.createElement('div'); st.className = 'chstat'; st.textContent = card.status || ''; body.append(st);
  (card.choices || []).slice(0, 4).forEach(ch => { const b = document.createElement('button'); b.className = 'box chbtn'; const t = document.createElement('b'), u = document.createElement('small'); t.textContent = ch.label; u.textContent = ch.sub || ''; b.append(t, u);
    b.onclick = () => { send({ t: 'action', name: ch.action, payload: ch.payload || {} }); closeCard(c); }; body.append(b); });
}
function musicBuild(c, body, card) {                                                // big cover art, title and artist small, a progress bar, the same controls as by voice
  body.classList.add('media', 'music');
  const art = new Image(); art.className = 'mart'; art.referrerPolicy = 'no-referrer'; art.alt = '';
  const meta = document.createElement('div'); meta.className = 'mmeta'; const nm = document.createElement('b'), ar = document.createElement('span'), dv = document.createElement('small'); meta.append(nm, ar, dv);
  const bar = document.createElement('div'); bar.className = 'mbar'; const fill = document.createElement('i'); bar.append(fill); const tm = document.createElement('div'); tm.className = 'mtime';
  const row = document.createElement('div'); row.className = 'mctl';
  const mk = (txt, label, fn) => { const b = document.createElement('button'); b.className = 'box mb'; b.textContent = txt; b.setAttribute('aria-label', label); b.onclick = fn; row.append(b); return b; };
  const ctl = op => send({ t: 'action', name: 'spotify_ctl', payload: { op } });
  mk('⏮', 'previous', () => ctl('previous')); const pp = mk('⏯', 'play or pause', () => ctl(st.playing ? 'pause' : 'resume')); mk('⏭', 'next', () => ctl('next'));
  mk('−', 'quieter', () => ctl('vol_down')); mk('+', 'louder', () => ctl('vol_up')); mk('DEVICES', 'devices', () => send({ t: 'action', name: 'spotify_devices', payload: {} }));
  let st = {}; let t0 = performance.now();
  const fmt = x => `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, '0')}`;
  function paint() { const pr = (st.progress || 0) + (st.playing ? (performance.now() - t0) / 1000 : 0), d = st.duration || 0; fill.style.width = d ? Math.min(100, pr / d * 100) + '%' : '0%'; tm.textContent = d ? `${fmt(Math.min(pr, d))} / ${fmt(d)}` : ''; pp.textContent = st.playing ? '⏸' : '▶'; }
  function set(m) { st = m; t0 = performance.now(); if (m.image && art.getAttribute('src') !== m.image) art.src = m.image; nm.textContent = m.name || 'Nothing playing'; ar.textContent = m.artist || ''; dv.textContent = (m.device ? 'on ' + m.device : '') + (m.volume != null ? '  ·  volume ' + m.volume + '%' : ''); paint(); }
  set(card); body.append(art, meta, bar, tm, row);
  const iv = setInterval(paint, 1000), poll = setInterval(() => send({ t: 'action', name: 'spotify_status', payload: {} }), 5000);       // polls only while the card is open
  MUSIC.set = set; MUSIC.card = c; c.onClose = () => { clearInterval(iv); clearInterval(poll); if (MUSIC.card === c) { MUSIC.set = null; MUSIC.card = null; } };
}
function galleryBuild(body, card) {                                                // several pictures: one large, a swipeable strip underneath, a small caption in the corner
  body.classList.add('media', 'gal'); const ims = card.images || []; let cur = 0;
  const gm = document.createElement('div'); gm.className = 'gmain'; const im = new Image(); im.referrerPolicy = 'no-referrer'; im.alt = card.title || '';
  const cap = document.createElement('div'); cap.className = 'gcap'; const cnt = document.createElement('div'); cnt.className = 'gcount';
  const l = document.createElement('button'), r = document.createElement('button'); l.className = 'gnav l'; r.className = 'gnav r'; l.textContent = '‹'; r.textContent = '›';
  const strip = document.createElement('div'); strip.className = 'gstrip'; const thumbs = ims.map((m, i) => { const t = new Image(); t.referrerPolicy = 'no-referrer'; t.src = m.src; t.alt = ''; t.onclick = () => show(i); strip.append(t); return t; });
  function show(i) { cur = (i + ims.length) % ims.length; im.src = ims[cur].src; cap.textContent = ims[cur].caption || card.title || ''; cap.style.display = cap.textContent ? '' : 'none'; cnt.textContent = (cur + 1) + ' / ' + ims.length;
    thumbs.forEach((t, k) => t.classList.toggle('on', k === cur)); const t = thumbs[cur]; t && t.scrollIntoView && t.scrollIntoView({ block: 'nearest', inline: 'center' }); }
  im.onerror = () => { if (ims.length > 1) { ims.splice(cur, 1); thumbs.splice(cur, 1)[0].remove(); show(cur); } };
  l.onclick = () => show(cur - 1); r.onclick = () => show(cur + 1); if (ims.length < 2) { l.style.display = r.style.display = 'none'; strip.style.display = 'none'; cnt.style.display = 'none'; }
  let x0 = null; gm.addEventListener('pointerdown', e => { x0 = e.clientX; }); gm.addEventListener('pointerup', e => { if (x0 !== null && Math.abs(e.clientX - x0) > 40) show(cur + (e.clientX < x0 ? 1 : -1)); x0 = null; });
  gm.append(im, cap, cnt, l, r); body.append(gm, strip); if (ims.length) show(0);
}

/* ---- window switcher: your own windows from hyprctl, one tap to bring one to the front; Jarvis keeps running behind ---- */
let wswT = null;
async function wswRefresh() {
  try { const l = await fetch('/api/windows').then(r => r.json()); const box = $('#wslist'); box.textContent = '';
    l.forEach(w => { const b = document.createElement('button'); b.className = 'tiny box w'; b.textContent = w.title || w.class; const sm = document.createElement('small'); sm.textContent = w.class.toUpperCase() + ' · WS ' + w.ws; b.append(sm);
      b.onclick = () => { send({ t: 'action', name: 'focus_other', payload: { address: w.address } }); wswOpen(false); }; box.append(b);
      const row = document.createElement('div'); row.className = 'wrow'; const here = document.createElement('button'), back = document.createElement('button'); here.className = back.className = 'tiny box'; here.textContent = 'SHOW ON JARVIS'; back.textContent = 'GIVE BACK';
      here.onclick = () => { send({ t: 'action', name: 'place_other', payload: { address: w.address } }); wswOpen(false); }; back.onclick = () => { send({ t: 'action', name: 'release_other', payload: { address: w.address } }); wswOpen(false); }; row.append(here, back); box.append(row); });
    if (!l.length) { const e = document.createElement('div'); e.className = 'ghint'; e.textContent = 'no other windows open'; box.append(e); } } catch (_) {}
}
function wswOpen(on) { $('#wsw').classList.toggle('on', on); clearInterval(wswT); if (on) { wswRefresh(); wswT = setInterval(wswRefresh, 3000); } }
$('#m-win').onclick = () => { openMenu(false); wswOpen(true); }; $('#wsj').onclick = () => wswOpen(false);

/* ---- LOOK view: the camera/screen picture fills the frame, labelled boxes (from the brain) animate in, tap a box to select it ---- */
const LK = { boxes: [], sel: null, open: false, live: false, stream: null, poll: 0, srcName: '' };
function lkLayout() {                                                              // put the box layer exactly over the displayed picture
  const im = $('#lkimg'), st = $('#lkstage'), b = $('#lkboxes'); if (!im.naturalWidth) return; const r = im.getBoundingClientRect(), s0 = st.getBoundingClientRect();
  const f = LK.zoom || 1; b.style.left = ((r.left - s0.left) / f) + 'px'; b.style.top = ((r.top - s0.top) / f) + 'px'; b.style.width = (r.width / f) + 'px'; b.style.height = (r.height / f) + 'px'; lkPlaceCard();
}
function lkDraw() {
  const box = $('#lkboxes'); box.textContent = ''; box.classList.toggle('on', LK.boxes.length > 0);
  LK.boxes.forEach((o, i) => { const d = document.createElement('div'); d.className = 'lkb' + (LK.sel === i ? ' sel' : ''); d.style.cssText = `left:${o.x * 100}%;top:${o.y * 100}%;width:${o.w * 100}%;height:${o.h * 100}%;--d:${(0.25 + i * 0.12).toFixed(2)}s`;
    const sp = document.createElement('span'); sp.textContent = (i + 1) + ' · ' + o.name; d.append(sp); ['tr', 'bl'].forEach(c => { const e = document.createElement('i'); e.className = c; d.append(e); });
    d.onclick = ev => { ev.stopPropagation(); lkSelect(i); }; box.append(d); });
  if (LK.boxes.length) { const sc = $('#lkscan'); sc.classList.remove('go'); void sc.offsetWidth; sc.classList.add('go'); }                       // the quick scan sweep
  lkLayout();
}
function lkSelect(i) { LK.sel = i; lkDraw(); lkCard(LK.boxes[i].name, 'looking…'); send({ t: 'look_select', i }); }
function lkCard(title, text) { const c = $('#lkcard'); c.textContent = ''; const b = document.createElement('b'); b.textContent = title; c.append(b, document.createTextNode(text || '')); c.style.display = 'block'; lkPlaceCard(); }
function lkPlaceCard() {                                                           // next to the selected box, on whichever side has room
  const c = $('#lkcard'); if (LK.sel === null || !LK.boxes[LK.sel] || c.style.display === 'none') return; const o = LK.boxes[LK.sel], br = $('#lkboxes').getBoundingClientRect(), W2 = innerWidth, H2 = innerHeight;
  let x = br.left + (o.x + o.w) * br.width + 10, y = br.top + o.y * br.height; if (x + 250 > W2) x = Math.max(8, br.left + o.x * br.width - 250); y = Math.min(Math.max(54, y), H2 - 150); c.style.left = x + 'px'; c.style.top = y + 'px';
}
function lkMode(live) { LK.live = live; $('#lkinfo').classList.toggle('live', live); $('#lksrc').textContent = (live ? 'LIVE · ' : '') + (LK.srcName || 'LOOK').toUpperCase(); }
function lkStopLive() { clearInterval(LK.poll); LK.poll = 0; }
function lkStale(on) { $('#lkboxes').classList.toggle('stale', on); $('#lkagain').classList.toggle('on', on); clearTimeout(LK.staleT); if (!on && LK.boxes.length) LK.staleT = setTimeout(() => lkStale(true), 9000); }      // boxes belong to the frame they were found on: after a few seconds they fade and offer HIGHLIGHT AGAIN
function lkLive(stream, keep) {
  lkStopLive(); if (!keep) { LK.boxes = []; LK.sel = null; lkDraw(); $('#lkcard').style.display = 'none'; lkStale(false); } lkMode(true);
  if (stream) $('#lkimg').src = stream;                                             // the phone's own stream, straight from the phone
  else { const tick = async () => { try { const j = await fetch('/api/look/frame').then(r => r.json()); $('#lkimg').src = j.src; } catch (_) {} }; tick(); LK.poll = setInterval(tick, 2500); }
}
function lkOpen(m) {
  LK.open = true; document.documentElement.classList.add('looking'); S.viewing = true; S.viewT = performance.now() + 3600000; LK.srcName = m.source || ''; lkStopLive(); LK.boxes = []; LK.sel = null; $('#lkcard').style.display = 'none';
  $('#lkimg').onload = lkLayout; $('#lkimg').src = m.src; lkMode(false); lkDraw();
}
function lkClose() { lkStopLive(); lkStale(false); LK.zoom = 1; $('#lkstage').className = ''; $('#lkopts').classList.remove('on'); $('#lk-zoom').textContent = 'ZOOM 1×'; $('#lkimg').src = ''; LK.open = false; document.documentElement.classList.remove('looking'); S.viewing = false; $('#lkcard').style.display = 'none'; }   // the stream stops, the orb returns
function lkEvent(m) {
  if (m.op === 'open') lkOpen(m);
  else if (m.op === 'boxes') { LK.boxes = m.boxes || []; LK.sel = null; $('#lkcard').style.display = 'none'; lkDraw(); lkStale(false); }
  else if (m.op === 'select') { LK.sel = m.i; lkDraw(); if (LK.boxes[m.i]) lkCard(LK.boxes[m.i].name, m.text || 'looking…'); }
  else if (m.op === 'freeze') { lkStopLive(); lkMode(false); }
  else if (m.op === 'live') lkLive(m.stream, m.keep);
  else if (m.op === 'frame') { if ($('#lkimg').getAttribute('src') !== m.src) { $('#lkimg').src = m.src; } LK.srcName = m.source || LK.srcName; lkMode(false); }
  else if (m.op === 'close') lkClose();
}
$('#lk-close').onclick = () => { lkClose(); send({ t: 'look_op', op: 'close' }); };
$('#lkdots').onclick = () => { const o = $('#lkopts'); o.classList.toggle('on'); $('#lkdots').setAttribute('aria-expanded', o.classList.contains('on')); };
$('#lk-phone').onclick = () => { send({ t: 'look_op', op: 'phone' }); toast('SENDING TO YOUR PHONE'); };
$('#lk-hi').onclick = () => { send({ t: 'look_op', op: 'highlight' }); $('#lkopts').classList.remove('on'); };
$('#lk-scan').onclick = () => { send({ t: 'look_op', op: 'scan' }); $('#lkopts').classList.remove('on'); };
$('#lkagain').onclick = () => { send({ t: 'look_op', op: 'highlight' }); lkStale(false); };
$('#lk-zoom').onclick = () => { const z = (LK.zoom || 1) % 3 + 1; LK.zoom = z; $('#lkstage').className = z > 1 ? 'z' + z : ''; $('#lk-zoom').textContent = 'ZOOM ' + z + '×'; lkLayout(); };
$('#lk-mac').onclick = () => { send({ t: 'look_mac' }); toast('SENDING TO YOUR MAC'); };
$('#look').addEventListener('pointerdown', e => e.stopPropagation());
addEventListener('resize', lkLayout); try { new ResizeObserver(lkLayout).observe($('#lkstage')); } catch (_) {}                     // the box layer follows the picture whenever its size changes: no layout jump

/* ---- TERMINAL and FILES windows: movable/resizable/minimisable like the others. Typing here is yours alone: nothing in the assistant can write to it. ---- */
let TERM = { token: null, claudeDir: '', waiters: [] };
function termToken() { return new Promise(res => { if (TERM.token) return res(TERM.token); TERM.waiters.push(res); send({ t: 'term_open' }); }); }
const copyText = t => { try { return navigator.clipboard.writeText(t).then(() => true).catch(() => { toast('COPY FAILED'); return false; }); } catch (_) { toast('COPY FAILED'); return Promise.resolve(false); } };
const typingIn = el => !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
function termMount(w, m) {
  const holder = document.createElement('div'); holder.className = 'term'; w.body.appendChild(holder);
  const claude = m.preset === 'claude', bar = document.createElement('div'); bar.className = 'termbar'; let inp = null, go = null;
  if (claude) { inp = document.createElement('input'); inp.placeholder = 'folder for Claude (inside your home folder)'; inp.value = TERM.claudeDir || '~'; inp.spellcheck = false; go = document.createElement('button'); go.className = 'box'; go.textContent = 'START IN THIS FOLDER'; bar.append(inp, go); holder.append(bar); }
  const host = document.createElement('div'); host.className = 'xt'; holder.append(host);
  const cs = getComputedStyle(document.documentElement);
  const xt = new Terminal({ fontFamily: '"Noto Sans Mono","DejaVu Sans Mono",monospace', fontSize: 13, cursorBlink: true, scrollback: 3000, allowProposedApi: false, theme: { background: '#02040c', foreground: (cs.getPropertyValue('--fg') || '#dce8ff').trim(), cursor: (cs.getPropertyValue('--acc') || '#4a96ff').trim() } });
  const fit = new FitAddon.FitAddon(); xt.loadAddon(fit); xt.open(host);
  let sock = null; const fitNow = () => { try { fit.fit(); } catch (_) {} };
  w.onresize = fitNow; w.xt = xt; setTimeout(fitNow, 60);
  async function start() {
    if (sock) { try { sock.close(); } catch (_) {} sock = null; } xt.reset(); const token = await termToken(); fitNow();
    if (claude && inp) { TERM.claudeDir = inp.value.trim(); send({ t: 'claude_dir', dir: TERM.claudeDir }); }
    sock = new WebSocket(`ws://${location.host}/term/ws`); sock.binaryType = 'arraybuffer';
    sock.onopen = () => sock.send('\x00' + JSON.stringify({ token, cols: xt.cols, rows: xt.rows, preset: claude ? 'claude' : 'shell', dir: claude && inp ? inp.value.trim() : '' }));
    sock.onmessage = e => { if (typeof e.data === 'string') { try { const c = JSON.parse(e.data.slice(1)); if (c.error) xt.write('\r\n[' + c.error + ']\r\n'); else if ('exit' in c) xt.write('\r\n[session ended]\r\n'); } catch (_) {} } else xt.write(new Uint8Array(e.data)); };
    sock.onclose = () => { sock = null; };
    xt.focus();
  }
  xt.onData(d => { if (sock && sock.readyState === 1) sock.send(d); });
  xt.onResize(s => { if (sock && sock.readyState === 1) sock.send('\x00' + JSON.stringify({ resize: [s.cols, s.rows] })); });
  host.addEventListener('focusin', () => send({ t: 'term_focus', on: true })); host.addEventListener('focusout', () => send({ t: 'term_focus', on: false }));
  host.addEventListener('pointerdown', e => e.stopPropagation());
  if (go) go.onclick = start;
  w.onclose = () => { try { if (sock) sock.close(); } catch (_) {} try { xt.dispose(); } catch (_) {} send({ t: 'term_focus', on: false }); };
  if (claude) { termToken().then(() => { if (TERM.claudeDir) { inp.value = TERM.claudeDir; start(); } }); } else start();
}
function filesMount(w) {
  const root = document.createElement('div'); root.className = 'files'; w.body.appendChild(root);
  const bar = document.createElement('div'); bar.className = 'fbar'; const up = document.createElement('button'), home = document.createElement('button'), hid = document.createElement('button'), pth = document.createElement('div');
  up.className = home.className = hid.className = 'box'; up.textContent = '↑ UP'; home.textContent = 'HOME'; hid.textContent = 'HIDDEN: OFF'; pth.className = 'fpath'; bar.append(up, home, hid, pth);
  const grid = document.createElement('div'); grid.className = 'fgrid'; root.append(bar, grid); let cur = '', showHidden = false, parent = null;
  async function load(path) {
    const token = await termToken();
    let j; try { j = await fetch('/api/files?path=' + encodeURIComponent(path || '') + (showHidden ? '&hidden=1' : ''), { headers: { 'X-Jarvis-Token': token } }).then(r => { if (!r.ok) throw 0; return r.json(); }); } catch (_) { grid.textContent = 'cannot open that folder'; return; }
    cur = j.path; parent = j.parent; pth.textContent = j.path; grid.textContent = '';
    j.entries.forEach(e => { const b = document.createElement('button'); b.className = 'fi'; const ic = document.createElement('div'); ic.className = 'ic'; ic.textContent = e.dir ? '▣' : '▤'; b.append(ic, document.createTextNode(e.name));
      const full = j.path.replace(/\/$/, '') + '/' + e.name;
      if (e.img) fetch('/api/files/thumb?path=' + encodeURIComponent(full), { headers: { 'X-Jarvis-Token': token } }).then(r => r.ok ? r.blob() : null).then(bl => { if (!bl) return; const im = new Image(); im.src = URL.createObjectURL(bl); ic.textContent = ''; ic.append(im); }).catch(() => {});
      b.onclick = () => e.dir ? load(full) : send({ t: 'action', name: 'files_open', payload: { path: full, token } }); grid.append(b); });
  }
  up.onclick = () => parent && load(parent); home.onclick = () => load(''); hid.onclick = () => { showHidden = !showHidden; hid.textContent = 'HIDDEN: ' + (showHidden ? 'ON' : 'OFF'); load(cur); };
  load('');
}

function launchMount(w) {
  const root = document.createElement('div'); root.className = 'files'; w.body.appendChild(root); const bar = document.createElement('div'); bar.className = 'fbar'; const inp = document.createElement('input'); inp.placeholder = 'type an app or a file name'; inp.className = 'gin'; inp.style.margin = '0'; bar.append(inp);
  const grid = document.createElement('div'); grid.className = 'fgrid'; root.append(bar, grid); let seq = 0, tm = 0; setTimeout(() => inp.focus(), 80);
  inp.addEventListener('pointerdown', e => e.stopPropagation()); inp.addEventListener('focusin', () => send({ t: 'term_focus', on: true })); inp.addEventListener('focusout', () => send({ t: 'term_focus', on: false }));
  inp.oninput = () => { clearTimeout(tm); tm = setTimeout(async () => { const q = inp.value.trim(); if (q.length < 2) { grid.textContent = ''; return; } const mine = ++seq; const token = await termToken();
    let l = []; try { l = await fetch('/api/launcher?q=' + encodeURIComponent(q), { headers: { 'X-Jarvis-Token': token } }).then(r => r.json()); } catch (_) {} if (mine !== seq) return; grid.textContent = '';
    l.forEach(x => { const b = document.createElement('button'); b.className = 'fi'; const ic = document.createElement('div'); ic.className = 'ic'; ic.textContent = x.kind === 'app' ? '◈' : x.kind === 'dir' ? '▣' : '▤'; b.append(ic, document.createTextNode(x.name));
      b.onclick = () => send({ t: 'action', name: 'launch_item', payload: { kind: x.kind, id: x.id, path: x.path, token } }); grid.append(b); }); if (!l.length) grid.textContent = 'nothing found'; }, 250); };
}
function scratchMount(w) {
  const ta = document.createElement('textarea'); ta.className = 'gin'; ta.style.cssText = 'width:100%;height:100%;margin:0;resize:none;border:0;background:#02040c;font-size:14px;line-height:1.5'; ta.placeholder = 'scratchpad: kept on this machine, saved as you type'; w.body.appendChild(ta);
  send({ t: 'scratch_get' }); w.onscratch = t => { if (document.activeElement !== ta) ta.value = t; }; let tm = 0; ta.addEventListener('pointerdown', e => e.stopPropagation()); ta.addEventListener('focusin', () => send({ t: 'term_focus', on: true })); ta.addEventListener('focusout', () => send({ t: 'term_focus', on: false }));
  ta.oninput = () => { clearTimeout(tm); tm = setTimeout(() => send({ t: 'scratch_save', text: ta.value }), 500); }; w.onclose = () => send({ t: 'scratch_save', text: ta.value });
}
function clipMount(w) {
  const root = document.createElement('div'); root.className = 'files'; w.body.appendChild(root); const grid = document.createElement('div'); grid.className = 'fgrid'; grid.style.gridTemplateColumns = '1fr'; root.append(grid);
  w.onclips = items => { grid.textContent = ''; items.forEach(t => { const b = document.createElement('button'); b.className = 'fi'; b.style.alignItems = 'flex-start'; b.style.textAlign = 'left'; b.textContent = t.slice(0, 160); b.onclick = () => { copyText(t).then(ok => ok && toast('COPIED')); }; grid.append(b); }); if (!items.length) grid.textContent = 'nothing copied yet (passwords and card numbers are never kept)'; };
  send({ t: 'clip_get' }); w.cliptimer = setInterval(() => send({ t: 'clip_get' }), 4000); const oc = w.onclose; w.onclose = () => { clearInterval(w.cliptimer); };
}
function openTools(list) { (list || []).forEach(t => { const tool = typeof t === 'string' ? { launcher: { kind: 'launch' }, scratchpad: { kind: 'scratch' }, clipboard: { kind: 'clip' } }[t] || { kind: t } : t; const w = openWin({ kind: tool.kind, preset: tool.preset, src: '', title: '' }); if (w && t.rect) { place(w, snapRect({ x: t.rect.x * innerWidth, y: t.rect.y * innerHeight, w: t.rect.w * innerWidth, h: t.rect.h * innerHeight })); } }); }
function saveLayout(name) { const wins = WM.wins.filter(w => ['term', 'claude', 'files', 'launch', 'scratch', 'clip'].includes(w.type)).map(w => ({ kind: w.type === 'claude' ? 'term' : w.type, preset: w.type === 'claude' ? 'claude' : undefined, rect: { x: w.rect.x / innerWidth, y: w.rect.y / innerHeight, w: w.rect.w / innerWidth, h: w.rect.h / innerHeight } })); send({ t: 'layout_save', name, wins }); }
$('#m-launch').onclick = () => { openMenu(false); openTools(['launcher', 'scratchpad', 'clipboard']); };       // one TOOLS button: launcher, scratchpad and clipboard together
const openTool = (kind, preset) => openWin({ kind, preset, src: '', title: '' });
$('#m-term').onclick = () => { openMenu(false); openTool('term'); }; $('#m-claude').onclick = () => { openMenu(false); openTool('term', 'claude'); }; $('#m-files').onclick = () => { openMenu(false); openTool('files'); };
$('#abort').onclick = () => send({ t: 'action', name: 'abort_desktop', payload: {} });
$('#abort').addEventListener('pointerdown', e => e.stopPropagation());
$('#s-twake').onclick = () => setPref('term_wake', !PREFS.term_wake);
let cardId = 0;
const sfx = ev => { if (ws && ws.readyState === 1 && !DOCK) send({ t: 'sfx', ev }); };
function addCard(card) {
  if (card.shot) { showView(card.src); return; }
  sfx('open');
  if (card.replace && card.gid) cards.filter(o => o.gid === card.gid).forEach(closeCard);                     // a live card (the guide) is replaced, not stacked                                    // "what I saw": fullscreen, ring shrinks aside
  while (cards.length >= SLOTS.length) closeCard(cards[0]);
  const el = document.createElement('div'); el.className = 'card';
  const bar = document.createElement('div'); bar.className = 'bar';
  bar.innerHTML = '<b></b><span class="pg"><button class="pp" aria-label="previous">‹</button><i></i><button class="pn" aria-label="next">›</button></span><span class="tl"><button class="mn tly" aria-label="minimise">–</button><button class="ex tlg" aria-label="full screen">⤢</button><button class="x tlr" aria-label="close">✕</button></span><button class="hdl" aria-label="window controls"></button>';
  const ctlT = () => { clearTimeout(el._ctlT); el._ctlT = setTimeout(() => el.classList.remove('ctl'), 5000); };            // the controls slide out of the corner and tuck away again by themselves
  bar.querySelector('.hdl').onclick = e => { e.stopPropagation(); el.classList.toggle('ctl'); ctlT(); };
  bar.querySelector('b').addEventListener('click', e => { if (!c.dragged) { el.classList.toggle('ctl'); ctlT(); } }); bar.querySelector('b').textContent = (card.title || card.type);
  const body = document.createElement('div'); body.className = 'body';
  const meta = document.createElement('div'); meta.className = 'meta'; meta.innerHTML = '<span></span><span></span>';
  meta.firstChild.textContent = card.type.toUpperCase() + ' // JARVIS'; meta.lastChild.textContent = 'ID ' + String(++cardId).padStart(3, '0');
  el.append(bar, body, meta); if (card.wide) { el.style.setProperty('--cw', Math.min(innerWidth - 40, card.wide) + 'px'); el.classList.add('widget'); }
  const c = { gid: card.gid, el, card, map: null, isFull: false, x: bar.querySelector('.x'), ex: bar.querySelector('.ex'), pg: bar.querySelector('.pg') }; let post = null;
  if (card.sources && card.sources.length) meta.firstChild.textContent = card.sources.slice(0, 3).map(u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (_) { return String(u); } }).join(' · ').toUpperCase();
  if (card.type === 'weather') weatherBuild(body, card);
  else if (card.type === 'text') body.textContent = card.text || '';
  else if (card.type === 'memory') {                                               // what he remembers: each fact has an x to forget it by touch
    const ul = document.createElement('ul'); ul.className = 'mem';
    (card.items || []).forEach(it => { const li = document.createElement('li'); const s = document.createElement('span'); s.textContent = it.text; const b = document.createElement('button'); b.textContent = '\u2715'; b.setAttribute('aria-label', 'forget this');
      b.onclick = () => { send({ t: 'action', name: 'memory_delete', payload: { id: it.id } }); li.remove(); }; li.append(s, b); ul.append(li); });
    if (!(card.items || []).length) ul.textContent = 'NOTHING REMEMBERED YET'; body.append(ul); }
  else if (card.type === 'report') {                                               // a research report: summary, numbered findings with [n] refs, uncertain ones marked, a numbered source list with links
    let r = document.createElement('div'); r.className = 'rep';
    const h = (t) => { const e = document.createElement('h4'); e.textContent = t; r.append(e); };
    h('SUMMARY'); const sm = document.createElement('p'); sm.textContent = card.summary || ''; r.append(sm);
    h('KEY POINTS'); const ul = document.createElement('ul'); (card.findings || []).forEach((f, fi) => { const li = document.createElement('li'); li.textContent = f.text + '  ' + (f.refs || []).map(n => '[' + n + ']').join(' ');
      if (f.uncertain) { const u = document.createElement('em'); u.textContent = '  UNCERTAIN: ' + f.why; li.append(u); } if (fi >= 3) li.className = 'rest'; ul.append(li); }); r.append(ul);
    const mb = document.createElement('button'); mb.className = 'tiny box morebtn'; mb.textContent = 'MORE'; mb.onclick = () => { r.classList.toggle('more'); mb.textContent = r.classList.contains('more') ? 'LESS' : 'MORE'; }; if ((card.findings || []).length > 3 || (card.sources || []).length) r.append(mb);
    const srcWrap = document.createElement('div'); srcWrap.className = 'rest'; r.append(srcWrap); const holder = r; r = srcWrap;
    h('SOURCES'); const ol = document.createElement('ol'); ol.style.listStyle = 'none'; ol.style.paddingLeft = '0'; (card.sources || []).forEach(s => { const li = document.createElement('li'); const lk = document.createElement('a'); lk.textContent = '[' + s.n + '] ' + (s.title || s.url) + (s.verified ? '' : '  (NOT VERIFIED)'); lk.href = '#'; lk.title = s.url;
      lk.onclick = e => { e.preventDefault(); send({ t: 'action', name: 'open_link', payload: { url: s.url } }); }; li.append(lk); ol.append(li); }); r.append(ol);
    const v = document.createElement('p'); v.className = 'ver'; v.textContent = card.verified || ''; r.append(v); body.append(holder); }
  else if (card.type === 'guide') guideBuild(c, body, card);
  else if (card.type === 'livecam') { body.classList.add('media', 'gal'); const gm = document.createElement('div'); gm.className = 'gmain'; const im = new Image(); im.alt = 'phone camera'; im.src = card.url; im.onerror = () => { gm.textContent = 'NO PICTURE FROM THE PHONE: check the app is running and on the same Wi-Fi'; gm.style.padding = '14px'; }; gm.append(im); body.append(gm); c.live = im; }
  else if (card.type === 'list') { const ul = document.createElement('ul'); (card.items || []).forEach(t => { const li = document.createElement('li'); li.textContent = t; ul.append(li); }); body.append(ul); }
  else if (card.type === 'image') { body.classList.add('media', 'gal'); const gm = document.createElement('div'); gm.className = 'gmain'; const im = new Image(); im.alt = card.title || ''; im.referrerPolicy = 'no-referrer';
    im.onerror = () => { body.classList.remove('media', 'gal'); body.textContent = 'IMAGE UNAVAILABLE'; }; im.src = card.src; gm.append(im);
    if (card.title) { const cp = document.createElement('div'); cp.className = 'gcap'; cp.textContent = card.title; gm.append(cp); } body.append(gm); }      // the picture is the card, the caption a small label in the corner
  else if (card.type === 'gallery') galleryBuild(body, card);
  else if (card.type === 'music') musicBuild(c, body, card);
  else if (card.type === 'choice') choiceBuild(c, body, card);
  else if (card.type === 'news') newsBuild(c, body, card);
  else if (card.type === 'flash') flashBuild(c, body, card);
  else if (card.type === 'chat') chatBuild(c, body, card);
  else if (card.type === 'explain') explainBuild(c, body, card);
  else if (card.type === 'scene3d') holoBuild(c, body, card);
  else if (card.type === 'markets') mktBuild(c, body, card);
  else if (card.type === 'chart') { body.classList.add('media'); const cn = document.createElement('canvas'); body.append(cn); post = c.redraw = () => drawChart(cn, card); }
  else if (card.type === 'map') { body.classList.add('media'); const fr = document.createElement('iframe'); fr.className = 'mapframe'; fr.title = 'map';       // the map page (vector, 2D/3D, me-dot; raster fallback by itself)
    fr.src = '/map?id=' + encodeURIComponent(card.mapid || 'me') + (card.lat != null && card.lon != null ? '&lat=' + card.lat + '&lon=' + card.lon + '&label=' + encodeURIComponent(card.title || '') : ''); body.append(fr); }
  cards.push(c); $('#cards').append(el);
  placeCard(el, cards.length - 1);
  requestAnimationFrame(() => { el.classList.add('in'); post && post(); });
  bar.querySelector('.x').onclick = () => closeCard(c);                                     // CLOSE always closes, in one press (it used to shrink a full-screen card to a small panel first)
  bar.querySelector('.ex').onclick = () => c.isFull ? collapseCard(c) : expandCard(c);
  bar.querySelector('.mn').onclick = () => { if (c.isFull) collapseCard(c); else el.classList.toggle('shaded'); };                  // yellow: back to small, or roll the card up to its title bar      // maximise / minimise is its own button with its own icon
  bar.querySelector('.pp').onclick = () => flip(c, -1); bar.querySelector('.pn').onclick = () => flip(c, 1);
  const now = performance.now(); if (now - lastCardT > 2500) batch = []; lastCardT = now; batch.push(c);   // cards of one reply form a batch you can page through
  if (S.ghost) setGhost(false);
  const MEDIA = ['image', 'gallery', 'map', 'chart', 'weather', 'music', 'explain', 'scene3d', 'markets'].includes(card.type);
  if (card.type === 'report' || card.type === 'text' || card.type === 'list' || card.type === 'memory') { el.classList.add('sidepanel'); }
  if (MEDIA && !card.small && (batch.length === 1 || !cards.some(o => o.isFull))) expandCard(c);                                              // the picture, map or chart gets the screen; text stays a small card (a research report is a side panel with a MORE button)
  else if (!MEDIA && card.full) expandCard(c);
  updatePager();                                                                      // 1/3 .. 3/3 once more cards of the reply arrive
  if (card.wide) {                                                                   // a widget: remembers its place and size, can be resized from its bottom-right corner, never rests on the orb
    const key = 'jw_' + (card.gid || card.type); let sv = null; try { sv = JSON.parse(localStorage.getItem(key) || 'null'); } catch (_) {} c.wkey = key;
    if (sv && !c.isFull) { const w = clamp(sv.w || el.offsetWidth, 240, W - 20), h = sv.h ? clamp(sv.h, 120, H - 20) : null; el.style.setProperty('--cw', w + 'px'); if (h) el.style.maxHeight = h + 'px'; if (h) el.style.height = h + 'px';
      const x = clamp(sv.x, 0, W - w), y = clamp(sv.y, 0, H - 60), ox2 = ring.x, oy2 = ring.y, rr = R * 1.3; const hits = x < ox2 + rr && x + w > ox2 - rr && y < oy2 + rr && y + (h || 200) > oy2 - rr;
      if (!hits) { el.style.left = x + 'px'; el.style.top = y + 'px'; c.moved = true; } }
    const rz = document.createElement('div'); rz.className = 'rsz'; rz.setAttribute('aria-label', 'resize'); el.append(rz); let rs = null;
    rz.addEventListener('pointerdown', e => { rs = { id: e.pointerId, x: e.clientX, y: e.clientY, w: el.offsetWidth, h: el.offsetHeight }; rz.setPointerCapture(e.pointerId); e.stopPropagation(); c.moved = true; });
    rz.addEventListener('pointermove', e => { if (!rs || e.pointerId !== rs.id) return; const w = clamp(rs.w + e.clientX - rs.x, 240, W - el.offsetLeft - 4), h = clamp(rs.h + e.clientY - rs.y, 120, H - el.offsetTop - 4); el.style.setProperty('--cw', w + 'px'); el.style.height = h + 'px'; el.style.maxHeight = h + 'px'; });
    rz.addEventListener('pointerup', () => { if (rs) { rs = null; saveWidget(c); } }); rz.addEventListener('pointercancel', () => { rs = null; });
  }
  let drag = null;                                                                   // drag by the title bar (touch friendly)
  bar.addEventListener('pointerdown', e => { if (e.target.closest('button')) return; if (c.isFull) { drag = { id: e.pointerId, y0: e.clientY, swipe: true }; try { bar.setPointerCapture(e.pointerId); } catch (_) {} e.stopPropagation(); return; } c.moved = true; drag = { id: e.pointerId, dx: e.clientX - el.offsetLeft, dy: e.clientY - el.offsetTop }; bar.setPointerCapture(e.pointerId); el.parentNode.append(el); e.stopPropagation(); });
  bar.addEventListener('pointermove', e => { if (!drag || e.pointerId !== drag.id) return; if (drag.swipe) { drag.dy = Math.max(0, e.clientY - drag.y0); el.style.transform = `translateY(${drag.dy}px)`; el.style.opacity = String(1 - drag.dy / 400); return; } el.style.left = clamp(e.clientX - drag.dx, 0, W - el.offsetWidth) + 'px'; el.style.top = clamp(e.clientY - drag.dy, 0, H - 40) + 'px'; });
  const end = e => { if (!drag || e.pointerId !== drag.id) return; const d = drag; drag = null;
    if (d.swipe) { el.style.opacity = ''; el.style.transform = ''; if ((d.dy || 0) > 70) collapseCard(c); } else saveWidget(c); };                         // swipe down = back down; a moved widget remembers where it is
  bar.addEventListener('pointerup', end); bar.addEventListener('pointercancel', end);
  return c;
}

/* ---- WEATHER card: big temperature, a thin-line condition icon, feels / wind / rain, sunrise and sunset, an hourly strip and a 5-day row, a few subtle animated lines ---- */
function wxIcon(kind, night, size) {
  const sv = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); sv.setAttribute('viewBox', '0 0 64 64'); sv.setAttribute('width', size || 64); sv.setAttribute('height', size || 64); sv.setAttribute('class', 'wxi k-' + kind);
  const P = (d, cls) => { const e = document.createElementNS('http://www.w3.org/2000/svg', 'path'); e.setAttribute('d', d); if (cls) e.setAttribute('class', cls); sv.appendChild(e); };
  const cloud = 'M18 44h28a9 9 0 0 0 0-18 12 12 0 0 0-23-3 10 10 0 0 0-5 21z';
  const sun = () => { P('M32 20a8 8 0 1 0 .01 0z', 'core'); for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4; P(`M${32 + Math.cos(a) * 13} ${32 + Math.sin(a) * 13}L${32 + Math.cos(a) * 18} ${32 + Math.sin(a) * 18}`, 'ray'); } };
  const moon = () => P('M40 14a18 18 0 1 0 12 28A15 15 0 0 1 40 14z', 'core');
  if (kind === 'sun') (night ? moon : sun)();
  else if (kind === 'partly') { if (night) moon(); else { P('M22 22a7 7 0 1 0 .01 0z', 'core'); for (let i = 0; i < 6; i++) { const a = i * Math.PI / 3 - 1; P(`M${22 + Math.cos(a) * 11} ${22 + Math.sin(a) * 11}L${22 + Math.cos(a) * 15} ${22 + Math.sin(a) * 15}`, 'ray'); } } P(cloud, 'cl'); }
  else if (kind === 'cloud') P(cloud, 'cl');
  else if (kind === 'fog') { P(cloud, 'cl'); P('M16 50h32', 'ln'); P('M20 56h24', 'ln l2'); }
  else if (kind === 'drizzle' || kind === 'rain') { P(cloud, 'cl'); [[24, 50], [33, 52], [42, 50]].forEach(([x, y], i) => P(`M${x} ${y}l-2 ${kind === 'rain' ? 8 : 5}`, 'dr d' + i)); }
  else if (kind === 'snow') { P(cloud, 'cl'); [[24, 52], [33, 54], [42, 52]].forEach(([x, y], i) => P(`M${x} ${y}h.1`, 'fl f' + i)); }
  else if (kind === 'storm') { P(cloud, 'cl'); P('M34 46l-6 9h6l-4 8', 'bolt'); }
  return sv;
}
function weatherBuild(body, card) {
  body.classList.add('wxb'); const w = document.createElement('div'); w.className = 'wx2';
  const top = document.createElement('div'); top.className = 'wxtop'; top.append(wxIcon(card.kind, card.night, 72));
  const big = document.createElement('div'); big.className = 'wxbig'; big.textContent = card.temp + '°'; const d = document.createElement('div'); d.className = 'wxd';
  const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']; const dir = dirs[Math.round((card.wind_dir || 0) / 45) % 8];
  const l1 = document.createElement('b'); l1.textContent = (card.desc || '').toUpperCase(); const l2 = document.createElement('span'); l2.textContent = `feels ${card.feels}° · wind ${card.wind} ${card.wind_unit} ${dir}`; const l3 = document.createElement('span'); l3.textContent = `rain ${card.pop || 0}% · humidity ${card.humidity}%`;
  const l4 = document.createElement('span'); l4.textContent = `↑ ${card.sunrise}   ↓ ${card.sunset}`; d.append(l1, l2, l3, l4); top.append(big, d); w.append(top);
  const hrs = document.createElement('div'); hrs.className = 'wxhrs'; (card.hours || []).forEach(h => { const e = document.createElement('div'); e.className = 'wxh'; const t = document.createElement('small'); t.textContent = h.t; const v = document.createElement('b'); v.textContent = h.temp + '°';
    const p = document.createElement('i'); p.textContent = h.pop >= 20 ? h.pop + '%' : ''; e.append(t, wxIcon(h.kind, false, 26), v, p); hrs.append(e); }); w.append(hrs);
  const dys = document.createElement('div'); dys.className = 'wxdays'; (card.days || []).forEach(x => { const e = document.createElement('div'); e.className = 'wxday'; const n = document.createElement('small'); n.textContent = x.name.toUpperCase(); const v = document.createElement('b'); v.textContent = x.hi + '° / ' + x.lo + '°';
    const p = document.createElement('i'); p.textContent = x.pop >= 30 ? x.pop + '%' : ''; e.append(n, wxIcon(x.kind, false, 30), v, p); dys.append(e); }); w.append(dys);
  const up = document.createElement('div'); up.className = 'wxup'; up.textContent = (card.place || '') + ' · updated ' + (card.updated || '') + (card.stale ? ' (old copy: no connection)' : ''); w.append(up); body.append(w);
}

/* ---------------------------------------------------------------- live status ("searching") */
function showStatus(lines, active) {
  const el = $('#srch'), ul = el.querySelector('ul'); ul.textContent = '';
  (lines || []).forEach(l => { const li = document.createElement('li'); li.textContent = l.replace(/ ✓$/, ''); li.className = / ✓$/.test(l) ? 'done' : ''; if (li.className) li.textContent = '✓ ' + li.textContent; ul.append(li); });
  el.classList.toggle('on', !!active && lines && lines.length > 0);
}

/* ---------------------------------------------------------------- input: hold to talk */
let holding = null;
function send(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }
function talk(down, look) {
  if (down) { if (holding !== null) return; holding = !!look; send({ t: 'talk', down: true, look: !!look }); if (P.has('demo')) setMode('listening'); }
  else if (holding !== null) { holding = null; send({ t: 'talk', down: false }); if (P.has('demo')) setMode('idle'); }
}
function holdable(el, look) {
  let id = null, t0 = 0, timer = 0, talking = false;
  el.addEventListener('pointerdown', e => { if (id !== null) return; id = e.pointerId; el.setPointerCapture(id); e.stopPropagation(); e.preventDefault(); t0 = performance.now(); talking = false;
    if (look) timer = setTimeout(() => { talking = true; talk(true, true); }, 380); else { talking = true; talk(true, false); } });                 // the camera button: a tap is NOT a recording
  const up = e => { if (e.pointerId !== id) return; id = null; clearTimeout(timer);
    if (talking) talk(false); else if (look && e.type === 'pointerup') send({ t: 'look_button' }); };                                                // tap = open / close the LOOK view
  el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up); el.addEventListener('lostpointercapture', up);
}
holdable($('#i-cam'), true);
let bgPtr = null, bgHeld = false, bgTimer = 0, lastTap = { t: 0, x: 0, y: 0 };      // press-and-hold on empty space talks; a double tap hides the UI
document.addEventListener('pointerdown', e => {
  if (e.target.closest('#menu, #settings, #menubtn')) return;
  if ($('#menu').classList.contains('on')) { openMenu(false); }                     // tapping elsewhere closes the menu
  if ($('#hist').classList.contains('open') && !e.target.closest('#hist, #s-hist')) toggleHist(false);
  if (bgPtr !== null || e.target.closest('button, .card, #hist, #dock, #srch, #vclose')) return;
  bgPtr = e.pointerId; bgHeld = false; clearTimeout(bgTimer); bgTimer = setTimeout(() => { bgHeld = true; talk(true, false); }, 220);   // brief taps must not start recording
});
const bgUp = e => {
  if (e.pointerId !== bgPtr) return; clearTimeout(bgTimer); const held = bgHeld; bgPtr = null; bgHeld = false;
  if (held) { talk(false); return; }
  const now = performance.now();
  if (now - lastTap.t < 350 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 50) { lastTap.t = 0; setGhost(!S.ghost); }   // double tap on empty space
  else lastTap = { t: now, x: e.clientX, y: e.clientY };
};
document.addEventListener('pointerup', bgUp); document.addEventListener('pointercancel', bgUp);
document.addEventListener('contextmenu', e => e.preventDefault());
function setGhost(on) { S.ghost = on; document.documentElement.classList.toggle('ghost', on); applyBackdrop(); if (on) openMenu(false); if (on) toast('TAP THE ORB TO BRING THE UI BACK'); }
let themeKey = 'Alt+T'; try { themeKey = localStorage.getItem('themeKey') || 'Alt+T'; } catch (_) {}
const typing = el => !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
const keyCombo = e => (e.ctrlKey ? 'Ctrl+' : '') + (e.altKey ? 'Alt+' : '') + (e.shiftKey ? 'Shift+' : '') + (e.metaKey ? 'Meta+' : '') + (e.key.length === 1 ? e.key.toUpperCase() : e.key);
let capturing = false; const showTKey = () => { $('#s-tkey').textContent = themeKey.toUpperCase() + ' · CHANGE'; }; showTKey();
$('#s-tkey').onclick = () => { capturing = true; $('#s-tkey').textContent = 'PRESS THE NEW KEYS (needs Alt, Ctrl or Meta)'; };
document.addEventListener('keydown', e => {
  if (capturing) { if (['Alt', 'Control', 'Shift', 'Meta'].includes(e.key)) return; e.preventDefault(); if (e.altKey || e.ctrlKey || e.metaKey) { themeKey = keyCombo(e); try { localStorage.setItem('themeKey', themeKey); } catch (_) {} } capturing = false; showTKey(); return; }
  if (typing(e.target)) return;                                                      // plain letters never trigger a shortcut, and nothing fires while a text box has focus
  if ((e.altKey || e.ctrlKey || e.metaKey) && keyCombo(e) === themeKey) { e.preventDefault(); setTheme(nextPersona()); return; }
  if (e.key === 'Escape' && document.documentElement.classList.contains('efull')) { setEfull(false); send({ t: 'action', name: 'embed_full', payload: { on: false } }); return; }
  if (e.key === 'Escape' && typingIn(e.target) && e.target.closest && e.target.closest('.term')) return;       // Escape belongs to the program in the terminal (vim, Claude Code)
  if (e.key === 'Escape' && $('#settings').classList.contains('on')) { closeSettings(); return; }
  if (e.key === 'Escape' && LK.open) { lkClose(); send({ t: 'look_op', op: 'close' }); return; }
  if (e.key === 'Escape') { const f = cards.find(c => c.isFull); if (S.ghost) setGhost(false); else if ($('#menu').classList.contains('on')) openMenu(false); else if (f) closeCard(f); else clearView(); } });
const orbEl = $('#orbhit'); let orbP = null;                                         // orb: hold = talk; quick tap = restore the UI (hidden) or toggle talk (otherwise)
orbEl.addEventListener('pointerdown', e => { if (orbP) return; orbP = { id: e.pointerId, held: false };
  orbP.timer = setTimeout(() => { if (orbP) { orbP.held = true; talk(true, false); } }, 320); try { orbEl.setPointerCapture(e.pointerId); } catch (_) {} e.stopPropagation(); e.preventDefault(); });
const orbUp = e => { if (!orbP || e.pointerId !== orbP.id) return; clearTimeout(orbP.timer); const held = orbP.held; orbP = null;
  if (held) { talk(false); return; }
  if (S.ghost) { setGhost(false); return; }
  if (DOCK) { send({ t: 'action', name: 'focus_jarvis', payload: {} }); return; }   // a tap on the dock orb brings the big Jarvis back
  holding !== null ? talk(false) : talk(true, false); };
orbEl.addEventListener('pointerup', orbUp); orbEl.addEventListener('pointercancel', orbUp);

/* ---- the one small menu (top-right) + settings row; closes by itself */
let menuT = 0;
function openMenu(on) { if (on !== $('#menu').classList.contains('on')) sfx('menu');
  $('#menu').classList.toggle('on', on);        // (it used to close the settings panel too: a stray tap, a menu action or the menu's own timer shut your settings. Now only CLOSE or Escape does.)
  clearTimeout(menuT); if (on) menuT = setTimeout(() => { if (!$('#settings').classList.contains('on')) openMenu(false); }, 6000);
}
const keepMenu = () => { if ($('#menu').classList.contains('on')) { clearTimeout(menuT); menuT = setTimeout(() => openMenu(false), 6000); } };
$('#menu').addEventListener('pointerdown', keepMenu); $('#settings').addEventListener('pointerdown', keepMenu);
$('#menubtn').onclick = () => openMenu(!$('#menu').classList.contains('on'));
function toggleHist(force) { const o = $('#hist').classList.toggle('open', typeof force === 'boolean' ? force : undefined); return o; }
$('#s-hist').onclick = () => { closeSettings(); toggleHist(); };       // History lives in Settings > Advanced now
$('#m-hide').onclick = () => setGhost(true);
$('#m-theme').onclick = () => setTheme(nextPersona());
$('#m-clear').onclick = () => { clearView(); openMenu(false); };
$('#m-set').onclick = () => { $('#menu').classList.remove('on'); $('#settings').classList.add('on'); document.documentElement.classList.add('sopen'); showPrefs(); };
$('#vclose').onclick = hideView;
const setSilent = on => { S.silent = on; send({ t: 'silent', on }); $('#s-voice').textContent = 'VOICE OUTPUT · ' + (on ? 'OFF' : 'ON'); };
$('#s-voice').onclick = () => setSilent(!S.silent);
$('#s-lite').onclick = () => setLite(!S.lite);
let followup = false;
const showFollow = () => { $('#s-follow').textContent = 'FOLLOW-UP · ' + (followup ? 'ON' : 'OFF'); };
$('#s-follow').onclick = () => { followup = !followup; send({ t: 'followup', on: followup }); showFollow(); };
$('#s-tidy').onclick = () => { tidy(); openMenu(false); };
let muted = false, handsfree = true;                                                // MUTE = deaf mode; the SERVER owns it (persisted) - voice can never change it
function showMute() {
  document.documentElement.classList.toggle('muted', muted); $('#i-mute').setAttribute('aria-pressed', muted); $('#mutel').textContent = muted ? 'MUTED' : 'MIC LIVE';
  $('#conn').textContent = S.mode === 'offline' ? 'OFFLINE' : muted ? 'MUTED · NOT LISTENING' : handsfree ? 'CONNECTED · HANDS-FREE' : 'CONNECTED';
}
$('#i-mute').onclick = () => { send({ t: 'muted', on: !muted }); toast(muted ? 'UNMUTING' : 'MUTING: NOTHING WILL LISTEN FOR WAKE PHRASES'); };
const SOURCES = ['auto', 'screen', 'window', 'camera', 'phone'];
const setSource = v => { S.source = v; send({ t: 'source', v }); $('#s-src').textContent = 'CAMERA SOURCE · ' + v.toUpperCase(); };
$('#m-orb').onclick = () => { openMenu(false); send({ t: 'action', name: 'to_orb', payload: {} }); };

$('#i-map').onclick = () => addCard({ type: 'map', title: 'Map' });
$('#i-music').onclick = () => send({ t: 'action', name: 'music_open', payload: {} });

/* ---- controls fade to low opacity when idle and brighten on touch */
function updateCalm() { const calm = performance.now() - S.touchT > 4000 && !$('#menu').classList.contains('on'); document.documentElement.classList.toggle('calm', calm); }
function poke() { S.touchT = performance.now(); document.documentElement.classList.remove('calm'); }
S.touchT = performance.now();
['pointerdown', 'pointermove', 'keydown', 'touchstart'].forEach(ev => addEventListener(ev, poke, { passive: true }));
setInterval(updateCalm, 500);

/* ---------------------------------------------------------------- embedded content: real windows inside Jarvis
   Windows (video, map, image, page) have a title bar (drag), a corner/edge resize, minimise (a small picture-in-picture tile), maximise (the Jarvis frame) and close.
   Moving and resizing only change CSS (transform while dragging, then left/top/width/height); the iframe is never removed or re-parented, so a playing video keeps playing.
   Several windows can be open; the one you touch comes to the front. Size and place are remembered per window type. A real window (a site or an app) still sits over #pframe. */
const WM = { wins: [], z: 20, front: null };
const FRAME = { x: .06, y: .12, w: .88, h: .74 };                                   // the Jarvis frame, as fractions of the screen (matches opener.PANEL)
const GAP = 12, CRACK = 190, ORBZ = 170, MINI = { w: 224, h: 126 };
const lsGet = k => { try { return JSON.parse(localStorage.getItem(k)); } catch (_) { return null; } }, lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} };
const frameRect = () => ({ x: FRAME.x * innerWidth, y: FRAME.y * innerHeight, w: FRAME.w * innerWidth, h: FRAME.h * innerHeight });
const winType = m => m.kind === 'launch' ? 'launch' : m.kind === 'scratch' ? 'scratch' : m.kind === 'clip' ? 'clip' : m.kind === 'term' ? (m.preset === 'claude' ? 'claude' : 'term') : m.kind === 'files' ? 'files' : /youtube\.com\/embed/.test(m.src) ? 'video' : /^\/map/.test(m.src) ? 'map' : m.kind === 'image' ? 'image' : 'page';
function snapRect(r) {                                                              // settle a dropped window: on screen, snapped to edges, out of the cracked corner and the orb
  const W_ = innerWidth, H_ = innerHeight; r.w = Math.min(r.w, W_ - 2 * GAP); r.h = Math.min(r.h, H_ - 2 * GAP);
  r.x = Math.min(Math.max(r.x, GAP), W_ - r.w - GAP); r.y = Math.min(Math.max(r.y, GAP), H_ - r.h - GAP);
  if (r.x < GAP + 18) r.x = GAP; if (r.y < GAP + 18) r.y = GAP; if (W_ - (r.x + r.w) < GAP + 18) r.x = W_ - r.w - GAP; if (H_ - (r.y + r.h) < GAP + 18) r.y = H_ - r.h - GAP;
  if (r.x + r.w > W_ - CRACK && r.y < CRACK) r.y = Math.min(CRACK, H_ - r.h - GAP);                                  // not in the top-right (cracked) corner
  if (r.x + r.w > W_ - ORBZ && r.y + r.h > H_ - ORBZ) { if (W_ - ORBZ - r.w >= GAP) r.x = W_ - ORBZ - r.w; else if (H_ - ORBZ - r.h >= GAP) r.y = H_ - ORBZ - r.h; }   // clear of the corner orb when that fits on screen (a big window may sit under it: the orb floats on top)
  return r;
}
function place(w, r) { const s = w.el.style; s.transform = ''; s.left = r.x + 'px'; s.top = r.y + 'px'; s.width = r.w + 'px'; s.height = r.h + 'px'; w.rect = { ...r }; if (w.onresize) setTimeout(w.onresize, 0); }
function remember(w) { if (w.state === 'normal') lsSet('jv.win.' + w.type, { x: w.rect.x / innerWidth, y: w.rect.y / innerHeight, w: w.rect.w / innerWidth, h: w.rect.h / innerHeight }); }
function toFront(w) { WM.front = w; w.el.style.zIndex = ++WM.z; WM.wins.forEach(o => o.el.classList.toggle('front', o === w)); }
function reportCount() { send({ t: 'action', name: 'embed_state', payload: { n: WM.wins.length } }); document.documentElement.classList.toggle('embed', WM.wins.length > 0 || document.documentElement.classList.contains('eframe')); if (!WM.wins.length) { document.documentElement.classList.remove('efull'); } updateMinis(); }
function updateMinis() { let k = 0; WM.wins.filter(w => w.state === 'mini').forEach(w => { place(w, { x: GAP, y: innerHeight - 74 - MINI.h - k * (MINI.h + 8), w: MINI.w, h: MINI.h }); k++; }); }
function setState(w, st) {
  if (st === 'max' && w.state === 'max') st = 'normal'; if (st === 'mini' && w.state === 'mini') st = 'normal';
  if (w.state === 'normal' && st !== 'normal') w.saved = { ...w.rect };
  w.state = st; w.el.classList.toggle('mini', st === 'mini'); w.el.classList.toggle('max', st === 'max');
  if (st === 'normal') place(w, w.saved || startRect(w.type)); else if (st === 'max') { const f = frameRect(); place(w, f); } else updateMinis();
  if (st !== 'mini') toFront(w); updateMinis();
}
function startRect(type) {
  const g = lsGet('jv.win.' + type), f = frameRect(); if (g && g.w > .15 && g.h > .15) return snapRect({ x: g.x * innerWidth, y: g.y * innerHeight, w: g.w * innerWidth, h: g.h * innerHeight });
  return { x: f.x, y: f.y, w: f.w, h: f.h };
}
function closeWin(w) { const i = WM.wins.indexOf(w); if (i < 0) return; if (w.onclose) { try { w.onclose(); } catch (_) {} } w.el.remove(); WM.wins.splice(i, 1); if (WM.front === w) WM.front = WM.wins[WM.wins.length - 1] || null; if (WM.front) toFront(WM.front); reportCount(); }
function ytCmd(w, fn, args) { try { w.body.querySelector('iframe').contentWindow.postMessage(JSON.stringify({ event: 'command', func: fn, args: args || [] }), 'https://www.youtube.com'); } catch (_) {} }
function drag(w, e, mode) {                                                          // mode: 'move' | 'se' | 'e' | 's'
  if (w.state === 'max' && mode === 'move') return; e.preventDefault(); e.stopPropagation(); toFront(w);
  const t = e.currentTarget, sx = e.clientX, sy = e.clientY, r0 = { ...w.rect }; t.setPointerCapture(e.pointerId); w.el.classList.add('dragging');
  const mv = ev => { const dx = ev.clientX - sx, dy = ev.clientY - sy;
    if (mode === 'move') w.el.style.transform = `translate(${dx}px,${dy}px)`;                                  // transform only: no layout, the video never notices
    else { const nr = { ...r0 }; if (mode !== 's') nr.w = Math.max(220, r0.w + dx); if (mode !== 'e') nr.h = Math.max(140, r0.h + dy); w.el.style.width = nr.w + 'px'; w.el.style.height = nr.h + 'px'; w.rect = nr; } };
  const up = ev => { t.releasePointerCapture(e.pointerId); t.removeEventListener('pointermove', mv); t.removeEventListener('pointerup', up); t.removeEventListener('pointercancel', up); w.el.classList.remove('dragging');
    const dx = ev.clientX - sx, dy = ev.clientY - sy; const nr = mode === 'move' ? { x: r0.x + dx, y: r0.y + dy, w: r0.w, h: r0.h } : { ...w.rect, x: r0.x, y: r0.y };
    place(w, snapRect(nr)); w.state = 'normal'; w.el.classList.remove('max'); remember(w); };
  t.addEventListener('pointermove', mv); t.addEventListener('pointerup', up); t.addEventListener('pointercancel', up);
}
function openWin(m) {
  const type = winType(m), html = document.documentElement; let w = WM.wins.find(o => o.type === type);
  if (w) { const old = w.body.firstChild; if (old && old.src !== undefined) { old.src = m.src; } if (w.state === 'mini') setState(w, 'normal'); toFront(w); html.classList.add('embed'); return w; }   // same type: reuse the window
  const el = document.createElement('div'); el.className = 'win';
  el.innerHTML = '<div class="wt"><span class="wtt"></span><span class="tl"><button class="wb wmin tly" aria-label="minimise">&#8211;</button><button class="wb wmax tlg" aria-label="full screen">&#10530;</button><button class="wb wx tlr" aria-label="close">&#10005;</button></span><button class="hdl" aria-label="window controls"></button></div>' +
    '<div class="wbody"></div><div class="wr wre"></div><div class="wr wrb"></div><div class="wr wrc"></div><div class="wtile"><button class="wb wpp" aria-label="play or pause">&#10074;&#10074;</button><button class="wb wex" aria-label="expand">&#10530;</button></div>';
  w = { type, el, body: el.querySelector('.wbody'), state: 'normal', rect: null, paused: false, saved: null }; w.titleEl = el.querySelector('.wtt');
  { const ctlT = () => { clearTimeout(el._ctlT); el._ctlT = setTimeout(() => el.classList.remove('ctl'), 5000); }; el.querySelector('.hdl').onclick = e => { e.stopPropagation(); el.classList.toggle('ctl'); ctlT(); }; }
  if (m.kind === 'iframe') { const f = document.createElement('iframe'); f.src = m.src; f.allow = 'autoplay; encrypted-media; picture-in-picture'; f.referrerPolicy = 'origin'; w.body.appendChild(f); }   // no 'fullscreen' permission: the only full screen is ours
  else if (m.kind === 'term') termMount(w, m);
  else if (m.kind === 'files') filesMount(w);
  else if (m.kind === 'launch') launchMount(w);
  else if (m.kind === 'scratch') scratchMount(w);
  else if (m.kind === 'clip') clipMount(w);
  else { const i = new Image(); i.src = m.src; i.referrerPolicy = 'no-referrer'; w.body.appendChild(i); }
  w.titleEl.textContent = ({ video: 'VIDEO', map: 'MAP', image: 'IMAGE', page: 'PAGE', term: 'TERMINAL', claude: 'CLAUDE', files: 'FILES', launch: 'LAUNCHER', scratch: 'SCRATCHPAD', clip: 'CLIPBOARD' })[type] + (m.title ? ' · ' + String(m.title).slice(0, 36).toUpperCase() : '');
  $('#panel').appendChild(el); WM.wins.push(w); place(w, startRect(type));
  el.addEventListener('pointerdown', () => toFront(w), true);
  el.querySelector('.wt').addEventListener('pointerdown', e => { if (!e.target.closest('.wb')) drag(w, e, 'move'); }); el.querySelector('.wt').addEventListener('dblclick', e => { if (!e.target.closest('.wb')) setState(w, 'max'); });
  el.querySelector('.wrc').addEventListener('pointerdown', e => drag(w, e, 'se')); el.querySelector('.wre').addEventListener('pointerdown', e => drag(w, e, 'e')); el.querySelector('.wrb').addEventListener('pointerdown', e => drag(w, e, 's'));
  el.querySelector('.wmin').onclick = () => setState(w, 'mini'); el.querySelector('.wmax').onclick = () => setState(w, 'max'); el.querySelector('.wx').onclick = () => { closeWin(w); send({ t: 'action', name: 'embed_closed', payload: {} }); };
  el.querySelector('.wex').onclick = () => setState(w, 'normal');
  el.querySelector('.wpp').onclick = () => { if (type !== 'video') return; w.paused = !w.paused; ytCmd(w, w.paused ? 'pauseVideo' : 'playVideo'); el.querySelector('.wpp').innerHTML = w.paused ? '&#9654;' : '&#10074;&#10074;'; };
  el.querySelector('.wtile').addEventListener('dblclick', () => setState(w, 'normal'));
  toFront(w); document.documentElement.classList.add('embed'); return w;
}
const WINCMD = { smaller: w => { const r = { ...w.rect }; r.w *= .75; r.h *= .75; setState(w, 'normal'); place(w, snapRect(r)); remember(w); },
  bigger: w => { const r = { ...w.rect }; r.w *= 1.25; r.h *= 1.25; setState(w, 'normal'); place(w, snapRect(r)); remember(w); },
  left: w => { setState(w, 'normal'); place(w, snapRect({ ...w.rect, x: 0 })); remember(w); }, right: w => { setState(w, 'normal'); place(w, snapRect({ ...w.rect, x: innerWidth })); remember(w); },
  top: w => { setState(w, 'normal'); place(w, snapRect({ ...w.rect, y: 0 })); remember(w); }, bottom: w => { setState(w, 'normal'); place(w, snapRect({ ...w.rect, y: innerHeight })); remember(w); },
  center: w => { setState(w, 'normal'); const f = frameRect(); place(w, snapRect({ ...w.rect, x: (innerWidth - w.rect.w) / 2, y: (innerHeight - w.rect.h) / 2 })); remember(w); },
  ypause: w => { w.paused = true; ytCmd(w, 'pauseVideo'); const b = w.el.querySelector('.wpp'); if (b) b.innerHTML = '&#9654;'; }, yplay: w => { w.paused = false; ytCmd(w, 'playVideo'); const b = w.el.querySelector('.wpp'); if (b) b.innerHTML = '&#10074;&#10074;'; },
  duck: w => ytCmd(w, 'setVolume', [20]), unduck: w => ytCmd(w, 'setVolume', [100]),
  min: w => setState(w, 'mini'), max: w => setState(w, 'max'), restore: w => setState(w, 'normal'), close: w => { closeWin(w); } };
function embed(m) {
  const html = document.documentElement, frame = $('#pframe');
  if (m.kind === 'close') {                                                       // close the front window (or all of them), or the real-window frame
    if (m.all) [...WM.wins].forEach(closeWin); else if (WM.front) closeWin(WM.front);
    if (!WM.wins.length) { html.classList.remove('eframe', 'efull'); frame.style.display = ''; } reportCount(); return; }
  if (m.kind === 'full') { setEfull(!!m.on); return; }
  if (m.kind === 'win') { const tgt = m.type ? WM.wins.find(o => o.type === m.type) : WM.front; if (tgt && WINCMD[m.cmd]) WINCMD[m.cmd](tgt); return; }
  if (m.kind === 'window') { html.classList.add('embed', 'eframe'); return; }  // a real window is placed over #pframe by the server
  html.classList.remove('eframe'); const w = openWin(m); if (m.full) setEfull(true); reportCount(); return w;
}
function setEfull(on) {                                                           // our own full screen: a CSS class, instant, the iframe stays where it is (real browser fullscreen is never used)
  const t0 = performance.now(), html = document.documentElement; html.classList.toggle('efull', on);
  $('#pfull').textContent = on ? 'EXIT FULL SCREEN' : 'FULL SCREEN';
  if (on) { try { window.focus(); if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); } catch (_) {} }
  requestAnimationFrame(() => requestAnimationFrame(() => { window.__efullMs = Math.round(performance.now() - t0); }));
}
document.addEventListener('fullscreenchange', () => { if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (_) {} setEfull(true); } });
$('#pclose').onclick = () => { if (WM.wins.length) { embed({ kind: 'close' }); send({ t: 'action', name: 'embed_closed', payload: {} }); } else send({ t: 'action', name: 'close_embed', payload: {} }); };
$('#pfull').onclick = () => { const on = !document.documentElement.classList.contains('efull'); setEfull(on); send({ t: 'action', name: 'embed_full', payload: { on } }); };
addEventListener('resize', () => { WM.wins.forEach(w => { if (w.state === 'max') place(w, frameRect()); else if (w.state === 'normal') place(w, snapRect({ ...w.rect })); }); updateMinis(); });

/* ---------------------------------------------------------------- idle screen */
const idleAfterMs = () => P.has('idleafter') ? (+P.get('idleafter') * 1000) : (PREFS.idle_minutes ? PREFS.idle_minutes * 60000 : Infinity);   // setting: 30 s ... 30 min, or never; default 5 min
var idleOn = false, idleSince = 0; let lastAct = performance.now(), idleTick = 0, secTimer = 0, shiftT = 0;
(() => { const g = $('#ictk'); if (!g) return; for (let i = 0; i < 60; i++) { const l = document.createElementNS('http://www.w3.org/2000/svg', 'line'); const five = i % 5 === 0, a = i * Math.PI / 30, r1 = five ? 150 : 156, r2 = 162;
  l.setAttribute('x1', Math.sin(a) * r1); l.setAttribute('y1', -Math.cos(a) * r1); l.setAttribute('x2', Math.sin(a) * r2); l.setAttribute('y2', -Math.cos(a) * r2); if (five) l.setAttribute('class', 'five'); g.append(l); } })();
function clockText() {
  const d = new Date(), p2 = n => String(n).padStart(2, '0'); let h = d.getHours(); const ap = h >= 12 ? 'PM' : 'AM'; if (!PREFS.clock24) h = h % 12 || 12;
  $('.ic-time .hm').textContent = (PREFS.clock24 ? p2(h) : String(h)) + ':' + p2(d.getMinutes()); $('.ic-time .ss').textContent = PREFS.clock_seconds ? ':' + p2(d.getSeconds()) : ''; $('.ic-time .ap').textContent = PREFS.clock24 ? '' : ap;
  $('.ic-date').textContent = d.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
  return d;
}
function clockSync() {                                                           // the sweep hand and the minute arc are CSS animations: start them at the real second so nothing runs per frame
  const d = clockText(), t = d.getSeconds() + d.getMilliseconds() / 1000; $('#idlescr').style.setProperty('--sec', (-t).toFixed(3) + 's');
  const sw = $('#icsweep'), ar = $('#icarc'); [sw, ar].forEach(e => { e.style.animation = 'none'; void e.getBoundingClientRect(); e.style.animation = ''; });
}
function clockPrefsChanged() { if (idleOn) clockText(); }
function burnShift() { const w = $('#icwrap'); w.style.transform = `translate(${Math.round((Math.random() - .5) * 16)}px, ${Math.round((Math.random() - .5) * 16)}px)`; }     // a few pixels every minute: no burn-in
async function idleLoad() { try { const j = await (await fetch('/api/idle')).json(); const w = j.weather;
  $('.ic-wx').textContent = w ? `${w.temp}${w.unit} · ${w.desc}${w.place ? ' · ' + w.place : ''}` : ''; $('.ic-next').textContent = j.next ? `NEXT: ${String(j.next.label).toUpperCase()} AT ${j.next.at}` : ''; } catch (_) {} }
function idleShow(on) {
  if (on === idleOn || DOCK) return; idleOn = on; document.documentElement.classList.toggle('idlescr', on); const sc = $('#idlescr');
  clearInterval(idleTick); clearInterval(shiftT); clearInterval(secTimer);
  if (on) { idleSince = performance.now(); sc.classList.remove('dim'); clockSync(); idleLoad();
    secTimer = setInterval(() => { const d = new Date(); clockText(); if (d.getSeconds() === 0) { clockSync(); idleLoad(); } }, 1000);        // text once a second; re-sync the animations every minute
    shiftT = setInterval(burnShift, 60000); idleTick = setInterval(() => { if (performance.now() - idleSince > 600000) sc.classList.add('dim'); }, 30000); }          // dims after 10 minutes
  else { lastAct = performance.now(); sc.classList.remove('dim'); $('#icwrap').style.transform = ''; if (window.playBoot) window.playBoot('wake'); }       // waking from the clock plays the start-up animation
}
const touchAct = () => { lastAct = performance.now(); if (idleOn) idleShow(false); };            // any touch, key, wake word or sound brings the normal view back at once (nothing restarts)
['pointerdown', 'pointermove', 'keydown', 'touchstart', 'wheel'].forEach(ev => addEventListener(ev, touchAct, { passive: true }));
setInterval(() => { const h = document.documentElement.classList;
  const busy = S.mode !== 'idle' || h.contains('embed') || h.contains('ghost') || S.conv || cards.length || $('#menu').classList.contains('on') || $('#settings').classList.contains('on') || $('#srch').classList.contains('on') || document.querySelector('#pframe.on');   // a video, music, a card, speech or a search is activity
  if (busy) lastAct = performance.now();
  else if (!idleOn && performance.now() - lastAct > idleAfterMs()) idleShow(true);
  if (idleOn && idleAfterMs() === Infinity) idleShow(false); }, 2000);
if (P.has('idle')) addEventListener('load', () => setTimeout(() => idleShow(true), 400));

/* ---------------------------------------------------------------- heartbeat (the watchdog restarts a page that goes silent) and the health line (hidden until something is wrong) */
setInterval(() => { if (!DOCK && ws && ws.readyState === 1) send({ t: 'beat' }); }, 3000);
/* ---- Claude Code / Codex status: a corner badge (also on the clock) and a coloured screen edge. Yellow blinks when it needs you, red when it failed, green flashes once and stays as a badge until tapped. ---- */
const CS = { state: 'idle', t: 0 };
function claudeStat(m) {
  const b = $('#cstat'), e = $('#cedge'), who = (m.who === 'codex' ? 'CODEX' : 'CLAUDE'); CS.state = m.state;
  e.className = ''; void e.offsetWidth;
  if (m.state === 'idle') { b.hidden = true; return; }
  b.hidden = false; b.dataset.s = m.state; b.textContent = who + ' · ' + ({ working: 'WORKING', needs: 'NEEDS ME', done: 'DONE', error: 'FAILED' }[m.state] || '') + (m.msg ? ' · ' + m.msg.slice(0, 48) : '');
  if (m.state === 'needs') e.className = 'cy'; else if (m.state === 'error') e.className = 'cr'; else if (m.state === 'done') e.className = 'cg';
  touchAct && m.state !== 'working' && (lastAct = performance.now());
}
$('#pdot').onclick = () => send({ t: 'presence', op: $('#pdot').classList.contains('paused') ? 'resume' : 'pause' });
$('#cstat').onclick = () => { $('#cstat').hidden = true; $('#cedge').className = ''; CS.state = 'idle'; };
function showHealth(msg) { const e = $('#hstat'); e.textContent = msg || ''; e.style.display = msg ? 'block' : 'none'; }

/* ---------------------------------------------------------------- websocket */
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { if (!DOCK) send({ t: 'persona', v: S.theme, announce: false }); if (!DOCK) send({ t: 'source', v: S.source }); if (S.silent && !DOCK) send({ t: 'silent', on: true }); };
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.t === 'heard') { if (PREFS.show_heard !== false) { const e = $('#heard'); e.textContent = 'heard: ' + m.text + (m.route ? '   →   ' + m.route : ''); e.style.display = 'block'; clearTimeout(e._t); e._t = setTimeout(() => { e.style.display = 'none'; }, 9000); } }
    else if (m.t === 'offline') { const e = $('#offl'); e.textContent = m.msg || ''; e.style.display = m.msg ? 'block' : 'none'; }
    else if (m.t === 'clip') { copyText(m.text).then(ok => ok && toast('COPIED')); }
    else if (m.t === 'phonereq') { const e = $('#preq'); e.textContent = 'PHONE: ' + m.text + '  →  ' + m.reply; e.style.display = 'block'; clearTimeout(e._t); e._t = setTimeout(() => { e.style.display = 'none'; }, 14000); }
    else if (m.t === 'termtoken') { TERM.token = m.token; TERM.claudeDir = m.claude_dir || ''; TERM.waiters.splice(0).forEach(f => f(m.token)); }
    else if (m.t === 'look') lkEvent(m);
    else if (m.t === 'prefs') { Object.assign(PREFS, m); showPrefs(); document.documentElement.classList.toggle('whisper', !!PREFS.whisper); }
    else if (m.t === 'hello') { if (m.personas) { PERS = Object.fromEntries(m.personas.map(p => [p.id, p])); renderPersonaList(); applyLook(); } if (m.prefs) { Object.assign(PREFS, m.prefs); showPrefs(); } if (m.persona && m.persona !== S.theme) setTheme(m.persona, false); followup = m.followup !== false; showFollow(); muted = !!m.muted; handsfree = !!m.handsfree; setMode(m.state); showMute(); $('#hist').textContent = ''; (m.history || []).forEach(h => addMsg(h.role, h.text)); }
    else if (m.t === 'state') { setMode(m.state, m.detail); if (m.state !== 'idle') touchAct(); }
    else if (m.t === 'level') {
      if (idleOn && m.src === 'mic' && m.v > .4) idleShow(false);                      // a sound from you
      if ((m.src === 'mic' && S.mode === 'listening') || (m.src === 'out' && S.mode === 'speaking')) S.tgt = Math.max(S.tgt * .5, m.v * (S.mAmp || 1));
    }
    else if (m.t === 'restarted') showHealth('restarted because: ' + m.what + ' (' + m.why + ')');
    else if (m.t === 'claudestat') claudeStat(m);
    else if (m.t === 'presence') { const d = $('#pdot'); d.hidden = !m.on; d.classList.toggle('paused', !!m.paused); d.querySelector('span').textContent = m.paused ? 'CAM PAUSED' : 'CAM ON'; document.documentElement.classList.toggle('away', m.present === false);
      if (m.present === true && idleOn) idleShow(false); }
    else if (m.t === 'whisper') { document.documentElement.classList.toggle('whisper', !!m.on); }
    else if (m.t === 'musicstate') { if (MUSIC.set) MUSIC.set(m); }
    else if (m.t === 'mkt') { if ((m.ops || []).some(o => o.op === 'close')) { if (MKT.card) closeCard(MKT.card); } else if (MKT.ctl) MKT.ctl.apply(m.ops); else MKT.pending.push(...(m.ops || [])); }
    else if (m.t === 'holo') { if (m.op === 'close_card') { if (HOLO.card) closeCard(HOLO.card); } else if (HOLO.ctl) HOLO.ctl.apply(m.op, m.id); }
    else if (m.t === 'draw_step') { DRAWS.spoken = true; DRAWS.forEach(d => d.reveal(m.n - 1)); }
    else if (m.t === 'holo_bullets') { if (HOLO.ctl && HOLO.ctl.setBullets) HOLO.ctl.setBullets(m.id, m.list || []); }
    else if (m.t === 'mood') { moodT = (PREFS.feelings === false ? null : MOOD[m.v]) || [0, 1, 1]; }
    else if (m.t === 'listen') { handsfree = m.on; showMute(); }
    else if (m.t === 'muted') { muted = m.on; showMute(); }
    else if (m.t === 'status') showStatus(m.lines, m.active);
    else if (m.t === 'ui') setGhost(!!m.hidden);
    else if (m.t === 'guide') { const g = GUIDES[m.gid]; if (g) g.setStatus(m.status, m.state); if (m.step === 'next' && g) g.next(); }
    else if (m.t === 'action_result') { if (!m.result.ok) toast(m.result.error.toUpperCase()); }
    else if (m.t === 'msg') { addMsg(m.role, m.text); touchAct(); if (m.role === 'you') chatPush('me', m.text); else if (m.role !== 'sys') chatPush('ai', m.text); }
    else if (m.t === 'card') { if (POP) { if (window.__popReady) popAdd(m.card); } else if (!DOCK) { addCard(m.card); if (m.card.full) { const c = cards[cards.length - 1]; c && expandCard(c); } if (m.card.secret) { const c = cards[cards.length - 1]; setTimeout(() => c && closeCard(c), m.card.type === 'guide' ? 600000 : 60000); } } }   // a secret card (the pairing code) removes itself after 60 s
    else if (m.t === 'embed') { if (!DOCK) embed(m); }
    else if (m.t === 'caption') { if (m.text) showCap(m.text); }
    else if (m.t === 'health') { showHealth(m.msg); }
    else if (m.t === 'idle') { idleShow(!!m.on); }
    else if (m.t === 'lite') { setLite(!!m.on, false, m.why); }
    else if (m.t === 'persona') { if (m.v && m.v !== S.theme) setTheme(m.v, false); }
  };
  ws.onclose = () => { setMode('offline'); setTimeout(connect, 1500); };
}

/* ---------------------------------------------------------------- demo mode (?demo=thinking&cards=1&theme=rose&view=/static/demo-view.jpg) */
function demoTick(t) {
  const m = P.get('demo');
  if (m === 'speaking' || m === 'listening') { S.tgt = Math.max(S.tgt, .45 + .4 * Math.sin(t * .011) * Math.sin(t * .0031)); }
}
function demo() {
  setMode(P.get('demo') || 'idle'); if (S.mode === 'thinking') { S.think = 1; S.spin = 2.2; S.spinV = 2.8; }   // settle immediately (screenshots under slow headless frames)
  showCap('The capital of Australia is Canberra.');
  if (P.has('view')) showView(P.get('view'));
  if (P.has('ghost')) setGhost(true);
  if (P.has('muted')) { muted = true; showMute(); }
  if (P.has('menu')) { openMenu(true); if (P.get('menu') === 'settings') $('#settings').classList.add('on'); clearTimeout(menuT); }
  if (P.has('weather')) addCard({ type: 'weather', title: 'Weather · Canberra', place: 'Canberra, Australian Capital Territory, Australia', unit: '°C', wind_unit: 'km/h', temp: 17, feels: 15, desc: 'mostly clear', wind: 12, humidity: 40, days: [{ name: 'Thu', hi: 26, lo: 12, desc: 'clear', pop: 5 }, { name: 'Fri', hi: 24, lo: 16, desc: 'thunderstorms', pop: 100 }, { name: 'Sat', hi: 25, lo: 16, desc: 'rain', pop: 97 }, { name: 'Sun', hi: 27, lo: 15, desc: 'showers', pop: 35 }] });
  if (P.has('search')) showStatus(['Searching: best christmas lights near Canberra ✓', 'Reading canberratimes.com.au', 'Searching: huddy street christmas lights opening times'], true);
  if (P.has('cards')) {
    addCard({ type: 'map', title: 'Canberra', lat: -35.2809, lon: 149.13 });
    addCard({ type: 'chart', title: 'Population (m)', kind: P.get('cards') === 'bar' ? 'bar' : 'line', labels: ['1990', '2000', '2010', '2020'], values: [17, 19, 22, 25.7] });
    addCard({ type: 'list', title: 'Facts', items: ['Founded 1913', 'Designed by Walter Burley Griffin', 'Not Sydney or Melbourne'] });
    addCard({ type: 'text', title: 'Note', text: 'Canberra was chosen as a compromise between the rival cities of Sydney and Melbourne.' });
    if (P.get('cards') === 'small') cards.forEach(c => c.isFull && collapseCard(c));
  }
}

if (P.has('hide')) document.head.insertAdjacentHTML('beforeend', '<style>' + P.get('hide') + '{display:none !important}</style>');   // debug: bisect cost
async function selftest() {
  S.paused = true; let simT = performance.now(); last = simT;
  const frames = n => { for (let i = 0; i < n * 2; i++) { simT += 16.7; tick(simT); } return Promise.resolve(); }, bad = [], log = [];
  const centre = () => { const r = $('#center').getBoundingClientRect(), c = cv.getBoundingClientRect();
    return { word: [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)], canvas: [Math.round(c.left + c.width / 2), Math.round(c.top + c.height / 2)], s: +ring.s.toFixed(2) }; };
  const check = (tag, expectCentre) => { const c = centre(), want = [Math.round(cx), Math.round(cy)];
    log.push(tag + ' ' + JSON.stringify(c));
    if (expectCentre && (Math.abs(c.word[0] - want[0]) > 2 || Math.abs(c.word[1] - want[1]) > 2 || Math.abs(c.canvas[0] - want[0]) > 2 || Math.abs(c.canvas[1] - want[1]) > 2 || Math.abs(c.s - 1) > .02)) bad.push(tag);
    if (!expectCentre && (Math.abs(c.s - .4) > .03 || c.word[0] < W * .7)) bad.push(tag); };
  const IDS = ['#menubtn', '#dock', '#status', '#cap', '#i-cam', '#i-map', '#i-music', '#i-mute', '#orbhit'];
  const sig = () => IDS.map(i => { const e = $(i), r = e.getBoundingClientRect(), vis = getComputedStyle(e).display !== 'none'; return [i, vis, Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; });
  const MENU = ['#menu', '#m-hide', '#m-theme', '#m-win', '#m-term', '#m-claude', '#m-files', '#m-launch', '#m-clear', '#m-set'], SETT = ['#settings', '#s-voice', '#s-speed', '#s-follow', '#s-snd'];
  setTheme('jarvis', false); setMode('idle'); openMenu(true); $('#settings').classList.add('on'); await frames(5);
  const sigM = () => [...IDS, ...MENU, ...SETT].map(i => { const e = $(i), r = e.getBoundingClientRect(), vis = getComputedStyle(e).display !== 'none'; return [i, vis, Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; });
  const a = sigM(); const cornerD = el => { const r = el.getBoundingClientRect(), pts = [[0, 0], [W, 0], [0, H], [W, H]];
    return Math.min(...pts.map(([x, y]) => Math.hypot(Math.max(r.left - x, 0, x - r.right), Math.max(r.top - y, 0, y - r.bottom)))); };
  const CTRL = ['#menubtn', '#i-cam', '#i-map', '#i-music', '#i-mute', '#status', ...MENU.slice(1), ...SETT.slice(1)]; let minD = 1e9, minWho = '';
  CTRL.forEach(i => { const e = $(i), d = cornerD(e), hit = (() => { const r = e.getBoundingClientRect(); return Math.min(r.width, r.height); })(); if (d < minD) { minD = d; minWho = i; } if (d < 60) bad.push(`corner: ${i} only ${d.toFixed(0)}px from a corner`); });
  log.push(`controls: nearest to any corner is ${minWho} at ${minD.toFixed(0)}px (need >= 60)`);
  openMenu(false); setTheme('rose', false); setMode('idle'); openMenu(true); $('#settings').classList.add('on'); await frames(5); const b = sigM(); openMenu(false);
  a.forEach((x, i) => { const y = b[i]; if (x[1] !== y[1] || x.slice(2).some((v, k) => Math.abs(v - y[k + 2]) > 3)) bad.push('layout ' + x[0] + ' jarvis=' + x.slice(1) + ' rose=' + y.slice(1)); });
  log.push('layout parity checked on ' + a.length + ' elements (incl. the open menu and settings row)');
  setTheme('jarvis', false); setMode('idle'); clearView(); await frames(150);
  const visBtns = [...document.querySelectorAll('button')].filter(e => getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width > 0);
  log.push('idle: visible controls = ' + visBtns.map(e => e.id).join(','));
  if (visBtns.length !== 7) bad.push('idle screen should show only the menu icon + 5 dock icons + the abort button, found ' + visBtns.length);
  ['#menu', '#settings', '#hist', '#cards .card', '#srch', '#vclose'].forEach(i => { const e = document.querySelector(i); if (e && getComputedStyle(e).display !== 'none' && i !== '#hist') bad.push('idle: ' + i + ' visible'); });
  S.touchT -= 5000; updateCalm(); if (!document.documentElement.classList.contains('calm')) bad.push('controls did not fade when idle');
  poke(); if (document.documentElement.classList.contains('calm')) bad.push('controls did not brighten on touch');
  openMenu(true); document.body.dispatchEvent(new PointerEvent('pointerdown', { pointerId: 3, bubbles: true })); document.body.dispatchEvent(new PointerEvent('pointerup', { pointerId: 3, bubbles: true }));
  if ($('#menu').classList.contains('on')) bad.push('menu did not close on an outside tap');
  openMenu(true); await new Promise(r => setTimeout(r, 6400)); if ($('#menu').classList.contains('on')) bad.push('menu did not close by itself');
  showCap('The capital of Australia is Canberra.'); { const r = $('#cap').getBoundingClientRect(), ox_ = cx, oy_ = cy, dx = Math.max(r.left - ox_, 0, ox_ - r.right), dy = Math.max(r.top - oy_, 0, oy_ - r.bottom), d = Math.hypot(dx, dy);
    log.push(`speech text: nearest edge ${d.toFixed(0)}px from the orb centre (orb ring radius ${(R * 1.92).toFixed(0)}px), at x=${r.left.toFixed(0)}..${r.right.toFixed(0)} y=${r.top.toFixed(0)}..${r.bottom.toFixed(0)}`);
    if (d < R * 1.92) bad.push('speech text overlaps the orb'); if (r.left < 60) bad.push('speech text too close to the left edge'); }
  { const tap = (id) => { document.body.dispatchEvent(new PointerEvent('pointerdown', { pointerId: id, clientX: 300, clientY: 400, bubbles: true })); document.body.dispatchEvent(new PointerEvent('pointerup', { pointerId: id, clientX: 300, clientY: 400, bubbles: true })); };
    setGhost(false); lastTap.t = 0; tap(21); if (S.ghost) bad.push('a single tap on empty space must not hide the UI'); tap(22); if (!S.ghost) bad.push('double tap on empty space did not hide the UI');
    orbEl.dispatchEvent(new PointerEvent('pointerdown', { pointerId: 23, bubbles: true })); orbEl.dispatchEvent(new PointerEvent('pointerup', { pointerId: 23, bubbles: true })); if (S.ghost) bad.push('tapping the orb did not bring the UI back');
    log.push('hide UI: double tap hides, orb tap restores'); }
  log.push(`orb centre: want (${W / 2}, ${H / 2}), have (${cx}, ${cy})`); if (cx !== W / 2 || cy !== H / 2) bad.push('orb is not exactly centred');
  for (let round = 0; round < 3; round++) for (const th of ['jarvis', 'rose']) {
    setTheme(th, false);
    for (const m of ['idle', 'listening', 'thinking', 'speaking', 'idle']) { setMode(m); S.tgt = .5; await frames(25); check(`${th}/${m}`, true); }
    showView('/static/demo-view.jpg'); await frames(90); check(`${th}/view-open`, false);
    hideView(); await frames(120); check(`${th}/view-closed`, true);
    showView('/static/demo-view.jpg'); setTheme(th === 'rose' ? 'jarvis' : 'rose', false); await frames(60); check(`${th}/theme-switch-in-view`, false); hideView(); await frames(120); check(`${th}/switch-then-close`, true);
    showView('/static/demo-view.jpg'); setMode('idle'); S.viewT -= 16000; await frames(130); check(`${th}/auto-return-after-15s-idle`, true);   // safety net
  }
  // ---- D: ghost mode + full-screen cards (both themes)
  const GUI = ['#menubtn', '#menu', '#settings', '#dock', '#status', '#cap', '#cards', '#hist'], SHOWN = ['#menubtn', '#dock', '#status'];
  const word = () => { const r = $('#center').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
  const centred = () => { const [x, y] = word(); return Math.abs(x - cx) < 2 && Math.abs(y - cy) < 2 && Math.abs(ring.s - 1) < .02; };
  const inCorner = () => { const [x, y] = word(); return x > W * .7 && y > H * .6 && Math.abs(ring.s - .4) < .04; };
  const ptr = (el, type, id, y = 0) => el.dispatchEvent(new PointerEvent(type, { pointerId: id, clientY: y, clientX: 50, bubbles: true }));
  const CARDS = { map: { type: 'map', title: 'Paris', lat: 48.85, lon: 2.35 }, chart: { type: 'chart', title: 'c', kind: 'bar', labels: ['a', 'b'], values: [1, 2] },
    list: { type: 'list', title: 'l', items: ['one', 'two'], sources: ['https://www.example.com/a'] }, weather: { type: 'weather', title: 'Weather', place: 'Canberra', unit: '°C', wind_unit: 'km/h', temp: 17, feels: 15, desc: 'mostly clear', wind: 12, humidity: 40, days: [{ name: 'Thu', hi: 26, lo: 12, desc: 'clear', pop: 5 }, { name: 'Fri', hi: 24, lo: 16, desc: 'rain', pop: 90 }] }, text: { type: 'text', title: 't', text: 'hello' }, image: { type: 'image', title: 'i', src: '/static/demo-view.jpg' } };
  let ok = 0; const need = (cond, msg) => { cond ? ok++ : bad.push(msg); };
  for (const th of ['jarvis', 'rose', 'jarvis']) {
    setTheme(th, false); setMode('idle'); clearView(); await frames(150);
    setGhost(true); await frames(40);                                                    // ghost: nothing but the orb
    GUI.forEach(i => need(getComputedStyle($(i)).display === 'none', `ghost ${th}: ${i} still visible`));
    need(centred(), `ghost ${th}: orb not centred`);
    ptr(orbEl, 'pointerdown', 7); ptr(orbEl, 'pointerup', 7); await frames(40);          // tap the orb -> GUI back
    need(!S.ghost, `ghost ${th}: tap did not restore`); SHOWN.forEach(i => need(getComputedStyle($(i)).display !== 'none', `ghost ${th}: ${i} not restored`));
    for (const [name, card] of Object.entries(CARDS)) {
      const tag = `${th}/${name}`; addCard({ ...card }); await frames(90);
      const f = cards.find(c => c.isFull);
      if (!['image', 'gallery', 'map', 'chart', 'weather'].includes(card.type)) { need(!f, `${tag}: a text card must stay small`); const sc = cards[cards.length - 1]; sc.x.click(); await frames(120); need(!cards.includes(sc), `${tag}: small card CLOSE`); continue; }
      need(!!f, `${tag}: did not go full screen`); if (!f) continue;
      const r = f.el.getBoundingClientRect(); need(r.width >= W * .8 && r.height >= H * .45, `${tag}: not big enough ${Math.round(r.width)}x${Math.round(r.height)}`);
      need(inCorner(), `${tag}: orb not in corner ${word().map(Math.round)} s=${ring.s.toFixed(2)}`);
      const sp0 = S.spin; S.mode = 'listening'; S.tgt = .8; await frames(10); need(S.spin > sp0, `${tag}: orb stopped animating`); need(S.lvl > .2, `${tag}: orb does not react to voice`);
      setMode('idle');
      f.ex.click(); await frames(150);                                                   // minimise button -> back down to the small panel, orb home
      need(!f.isFull && f.el.getBoundingClientRect().width < W * .4, `${tag}: minimise button did not collapse`); need(centred(), `${tag}: orb not home after minimise`);
      f.ex.click(); await frames(5); need(f.isFull, `${tag}: maximise button failed`);
      const bar = f.el.querySelector('.bar'); ptr(bar, 'pointerdown', 9, 100); ptr(bar, 'pointermove', 9, 120); ptr(bar, 'pointermove', 9, 260); ptr(bar, 'pointerup', 9, 260); await frames(150);   // swipe down
      need(!f.isFull, `${tag}: swipe did not collapse`); need(centred(), `${tag}: orb not home after swipe`);
      expandCard(f); await frames(60); need(inCorner(), `${tag}: re-expand orb`); closeCard(f); await frames(150); need(centred(), `${tag}: orb stuck after card removed while full`);
    }
    const EXTRA = { guide: { type: 'guide', gid: 'selftest', title: 'Guide', steps: [{ n: 1, text: 'Step one', cmd: 'echo hi' }], buttons: [] },
                    gallery: { type: 'gallery', title: 'Pictures', images: [{ src: '/static/demo-view.jpg', caption: 'one' }, { src: '/static/demo-view.jpg', caption: 'two' }] },
                    report: { type: 'report', title: 'Research', summary: 'sum', findings: [{ n: 1, text: 'f', refs: [1] }], sources: [{ n: 1, title: 't', url: 'https://example.org', verified: true }], verified: 'ok' } };
    for (const [name, card] of Object.entries({ ...CARDS, ...EXTRA })) {                  // CLOSE closes in ONE press, on every card type, and the layout returns to normal
      addCard({ ...card }); await frames(60); const f = cards[cards.length - 1]; need(!!f, `${th}/${name}: no card`); if (!f) continue;
      f.x.click(); await frames(150); need(!cards.includes(f) && f.el.classList.contains('out'), `${th}/${name}: CLOSE did not close in one press`); need(centred(), `${th}/${name}: orb not home after CLOSE`);
    }
    clearView(); await frames(30); lastCardT = 0; batch = [];                           // pager: three cards from one reply
    for (const k of ['chart', 'weather', 'map']) addCard({ ...CARDS[k] });
    await frames(60); const live = batch.filter(c => cards.includes(c));
    need(live.length === 3 && live[0].isFull && !live[1].isFull, `${th}: batch: first card should be full`); need(live[0].pg.classList.contains('on'), `${th}: pager not shown`);
    live[0].el.querySelector('.pn').click(); await frames(5); need(live[1].isFull && !live[0].isFull, `${th}: pager next failed`);
    clearView(); await frames(150); need(centred(), `${th}: orb not home after clear view`);
  }
  showStatus(['Searching: x', 'Reading y.com'], true); need($('#srch').classList.contains('on') && $('#srch').querySelectorAll('li').length === 2, 'status panel not shown');
  showStatus([], false); need(!$('#srch').classList.contains('on'), 'status panel not hidden');
  muted = true; showMute(); need(document.documentElement.classList.contains('muted') && getComputedStyle($('#i-mute .slash')).display !== 'none', 'mute icon not crossed out');
  need($('#conn').textContent.includes('MUTED'), 'status does not say muted'); muted = false; showMute(); need(!document.documentElement.classList.contains('muted'), 'unmute did not clear');
  log.push(`D/E: ${ok} ghost/fullscreen/status/mute assertions passed`);
  document.title = 'SELFTEST ' + (bad.length ? 'FAIL ' + JSON.stringify(bad) : 'PASS') + ' ' + log.length + ' checks | ' + log.filter(l => /^(controls|idle|orb centre|speech|layout|hide UI|D\/E)/.test(l)).join(' ; ');
  S.paused = false;
}
if (P.has('selftest')) addEventListener('load', () => setTimeout(() => selftest().catch(e => { document.title = 'SELFTEST ERROR ' + e; }), 300));
/* ---------------------------------------------------------------- boot */
addEventListener('resize', resize);
S.lite = P.has('lite') ? P.get('lite') !== '0' : load('jv.lite') === '1';
document.documentElement.classList.toggle('lite', S.lite); { const sl = $('#s-lite'); if (sl) sl.textContent = 'LITE MODE · ' + (S.lite ? 'ON' : 'OFF'); }
resize(); setTheme(P.get('theme') || load('jv.theme') || 'jarvis', !P.has('theme'));
requestAnimationFrame(frame);
if (P.has('demo')) demo(); else connect();
