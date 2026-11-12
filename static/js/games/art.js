import { h } from "../dom.js";

const TAU = Math.PI * 2;
const gcd = (a, b) => (b ? gcd(b, a % b) : a);

function rng(seed) {
  let s = (seed >>> 0) || 1;
  return () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
}

function norm(pts) {
  const f = pts.filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  const m = Math.max(...f.flat().map(Math.abs)) || 1;
  return f.map(([x, y]) => [x / m, y / m]);
}
function circle(r, n, ox = 0, oy = 0) {
  const p = [];
  for (let i = 0; i <= n; i++) { const a = (i / n) * TAU; p.push([ox + r * Math.cos(a), oy + r * Math.sin(a)]); }
  return p;
}
function ellipse(rx, ry, n) {
  const p = [];
  for (let i = 0; i <= n; i++) { const a = (i / n) * TAU; p.push([rx * Math.cos(a), ry * Math.sin(a)]); }
  return p;
}
function arc(r, a0, a1, n = 40, ox = 0, oy = 0) {
  const p = [];
  for (let i = 0; i <= n; i++) { const a = a0 + (a1 - a0) * (i / n); p.push([ox + r * Math.cos(a), oy + r * Math.sin(a)]); }
  return p;
}
function star(ro, ri, points = 5) {
  const p = [];
  for (let i = 0; i <= points * 2; i++) {
    const r = i % 2 ? ri : ro, a = (i / (points * 2)) * TAU - Math.PI / 2;
    p.push([r * Math.cos(a), r * Math.sin(a)]);
  }
  return p;
}
function wave() {
  const p = [];
  for (let i = 0; i <= 64; i++) { const x = (i / 64) * 2 - 1; p.push([x, 0.45 * Math.sin(x * TAU)]); }
  return p;
}
function spiral(rnd) {
  const turns = 2.5 + rnd() * 1.5, p = [];
  for (let i = 0; i <= 120; i++) { const a = (i / 120) * turns * TAU, rad = i / 120; p.push([rad * Math.cos(a), rad * Math.sin(a)]); }
  return p;
}
function blob(rnd) {
  const n = 7 + Math.floor(rnd() * 4), ph = rnd() * TAU, p = [];
  for (let i = 0; i <= 64; i++) { const a = (i / 64) * TAU; const r = 0.7 + 0.28 * Math.sin(n * a + ph); p.push([r * Math.cos(a), r * Math.sin(a)]); }
  return p;
}
function heart() {
  const p = [];
  for (let i = 0; i <= 100; i++) { const t = (i / 100) * TAU; p.push([0.8 * Math.pow(Math.sin(t), 3), -(0.6 * Math.cos(t) - 0.24 * Math.cos(2 * t) - 0.1 * Math.cos(3 * t) - 0.04 * Math.cos(4 * t))]); }
  return p;
}
function yinyangDivider() {
  return [...arc(0.5, -Math.PI / 2, Math.PI / 2, 24, 0, -0.5), ...arc(0.5, Math.PI / 2, 3 * Math.PI / 2, 24, 0, 0.5)];
}
function sunRays() {
  const rays = [];
  for (let i = 0; i < 12; i++) { const a = (i / 12) * TAU; rays.push([[0.75 * Math.cos(a), 0.75 * Math.sin(a)], [1 * Math.cos(a), 1 * Math.sin(a)]]); }
  return rays;
}
function skylineBars() {
  const hs = [0.5, 0.9, 0.7, 1, 0.6, 0.85, 0.55], w = 2 / hs.length, rects = [];
  hs.forEach((h, i) => { const x0 = -1 + i * w, y0 = 1 - h * 1.6; rects.push([[x0, 1], [x0, y0], [x0 + w * 0.8, y0], [x0 + w * 0.8, 1]]); });
  return rects;
}
function curvePoints(kind, rnd) {
  const out = [], N = 600;
  if (kind === "rose") { const a = 2 + Math.floor(rnd() * 5), b = 1 + Math.floor(rnd() * 3), k = a / b, span = TAU * b, ph = rnd() * 3.14;
    for (let i = 0; i <= N; i++) { const t = (i / N) * span, r = Math.cos(k * t + ph); out.push([r * Math.cos(t), r * Math.sin(t)]); } }
  else if (kind === "lissajous") { const a = 1 + Math.floor(rnd() * 4), b = 2 + Math.floor(rnd() * 4), ph = rnd() * 3.14;
    for (let i = 0; i <= N; i++) { const t = (i / N) * TAU; out.push([Math.sin(a * t + ph), Math.sin(b * t)]); } }
  else if (kind === "maurer") { const a = 3 + Math.floor(rnd() * 5), b = 2 + Math.floor(rnd() * 3), k = a / b, step = (2 + Math.floor(rnd() * 40)) * Math.PI / 180;
    for (let i = 0; i <= 720; i++) { const t = i * step, r = Math.cos(k * t); out.push([r * Math.cos(t), r * Math.sin(t)]); } }
  else if (kind === "super") { const m = 2 + Math.floor(rnd() * 10), n1 = 0.3 + rnd() * 4, n2 = 0.3 + rnd() * 4, n3 = 0.3 + rnd() * 4;
    for (let i = 0; i <= N; i++) { const th = (i / N) * TAU; const u = Math.pow(Math.pow(Math.abs(Math.cos(m * th / 4)), n2) + Math.pow(Math.abs(Math.sin(m * th / 4)), n3), -1 / n1); const r = Math.min(5, u); out.push([r * Math.cos(th), r * Math.sin(th)]); } }
  else if (kind === "harm") { const a = 2 + rnd() * 2, b = a + (rnd() - 0.5) * 0.05, c = 2 + rnd() * 2, d = c + (rnd() - 0.5) * 0.05, dd = 0.02 + rnd() * 0.08, M = 1200;
    for (let i = 0; i <= M; i++) { const t = (i / M) * 60, e1 = Math.exp(-dd * t), e2 = Math.exp(-dd * 1.1 * t); out.push([Math.sin(a * t) * e1 + Math.sin(b * t) * e2, Math.sin(c * t) * e1 + Math.sin(d * t) * e2]); } }
  else { const R = 5 + Math.floor(rnd() * 4), r = 2 + Math.floor(rnd() * 3), off = 1 + rnd() * 4, span = TAU * (r / gcd(R, r));
    for (let i = 0; i <= N * 2; i++) { const t = (i / (N * 2)) * span; out.push([(R - r) * Math.cos(t) + off * Math.cos(((R - r) / r) * t), (R - r) * Math.sin(t) - off * Math.sin(((R - r) / r) * t)]); } }
  return norm(out);
}

const GLYPH = {
  triangle: () => ({ paths: [[[0, -1], [0.9, 0.8], [-0.9, 0.8], [0, -1]]], dots: [] }),
  circle: () => ({ paths: [circle(1, 48)], dots: [] }),
  arc: () => ({ paths: [arc(1, Math.PI, 2 * Math.PI)], dots: [] }),
  line: () => ({ paths: [[[-1, 0], [1, 0]]], dots: [] }),
  star: () => ({ paths: [star(1, 0.45)], dots: [] }),
  spiral: (r) => ({ paths: [spiral(r)], dots: [] }),
  wave: () => ({ paths: [wave()], dots: [] }),
  blob: (r) => ({ paths: [blob(r)], dots: [] }),
  rect: () => ({ paths: [[[-0.8, -0.6], [0.8, -0.6], [0.8, 0.6], [-0.8, 0.6], [-0.8, -0.6]]], dots: [] }),
  ring: () => ({ paths: [circle(1, 48), circle(0.55, 48)], dots: [] }),
  dot: () => ({ paths: [], dots: [[0, 0, 0.5]] }),
  polygon: (r) => ({ paths: [circle(1, 5 + Math.floor(r() * 3))], dots: [] }),
  yinyang: () => ({ paths: [circle(1, 72), yinyangDivider()], dots: [[0, -0.5, 0.16], [0, 0.5, 0.16]] }),
  sun: () => ({ paths: [circle(0.6, 48), ...sunRays()], dots: [] }),
  mountain: () => ({ paths: [[[-1, 0.8], [-0.35, -0.9], [0.05, -0.1], [0.5, -0.85], [1, 0.8], [-1, 0.8]]], dots: [] }),
  skyline: () => ({ paths: skylineBars(), dots: [] }),
  tree: () => ({ paths: [[[-0.07, 1], [0.07, 1], [0.03, -0.1], [-0.03, -0.1], [-0.07, 1]], circle(0.5, 40, 0, -0.45)], dots: [] }),
  face: () => ({ paths: [circle(1, 64), arc(0.5, 0.15 * Math.PI, 0.85 * Math.PI, 24, 0, 0.35)], dots: [[-0.35, -0.15, 0.1], [0.35, -0.15, 0.1]] }),
  boat: () => ({ paths: [[[-0.9, 0.3], [0.9, 0.3], [0.5, 0.8], [-0.5, 0.8], [-0.9, 0.3]], [[0.05, -0.9], [0.05, 0.2], [0.75, 0.2], [0.05, -0.9]]], dots: [] }),
  bird: () => ({ paths: [arc(0.5, Math.PI, 2 * Math.PI, 20, -0.5, 0.2), arc(0.5, Math.PI, 2 * Math.PI, 20, 0.5, 0.2)], dots: [] }),
  fish: () => ({ paths: [ellipse(0.8, 0.4, 48), [[-0.7, 0], [-1.05, 0.35], [-1.05, -0.35], [-0.7, 0]]], dots: [[0.35, 0, 0.07]] }),
  heart: () => ({ paths: [heart()], dots: [] }),
  eye: () => ({ paths: [arc(1, 0.15 * Math.PI, 0.85 * Math.PI, 32), arc(1, 1.15 * Math.PI, 1.85 * Math.PI, 32)], dots: [[0, 0, 0.25]] }),
  arrow: () => ({ paths: [[[-1, 0], [1, 0]], [[0.4, -0.4], [1, 0], [0.4, 0.4]]], dots: [] }),
  rose: (r) => ({ paths: [curvePoints("rose", r)], dots: [] }),
  lissajous: (r) => ({ paths: [curvePoints("lissajous", r)], dots: [] }),
  spiro: (r) => ({ paths: [curvePoints("spiro", r)], dots: [] }),
  maurer: (r) => ({ paths: [curvePoints("maurer", r)], dots: [] }),
  super: (r) => ({ paths: [curvePoints("super", r)], dots: [] }),
  harm: (r) => ({ paths: [curvePoints("harm", r)], dots: [] }),
};

function layoutPositions(layout, count) {
  if (count <= 1 || layout === "center") {
    return Array.from({ length: count }, (_, i) => ({ x: 0, y: 0, s: count > 1 ? Math.max(0.3, 1 - i * 0.18) : 1 }));
  }
  const pos = [];
  for (let i = 0; i < count; i++) {
    const t = count > 1 ? i / (count - 1) : 0.5;
    if (layout === "stack") pos.push({ x: 0, y: (t * 2 - 1) * 0.7, s: 1 });
    else if (layout === "arc") { const a = Math.PI * (1 - t); pos.push({ x: Math.cos(a) * 0.8, y: -Math.sin(a) * 0.5 + 0.35, s: 1 }); }
    else if (layout === "radial") { const a = t * TAU; pos.push({ x: Math.cos(a) * 0.6, y: Math.sin(a) * 0.6, s: 1 }); }
    else if (layout === "grid") { const cols = Math.ceil(Math.sqrt(count)), rows = Math.ceil(count / cols); pos.push({ x: (cols > 1 ? (i % cols) / (cols - 1) : 0.5) * 1.6 - 0.8, y: (rows > 1 ? Math.floor(i / cols) / (rows - 1) : 0.5) * 1.6 - 0.8, s: 1 }); }
    else if (layout === "split") pos.push({ x: i % 2 ? 0.45 : -0.45, y: (t * 2 - 1) * 0.6, s: 1 });
    else pos.push({ x: (t * 2 - 1) * 0.8, y: 0, s: 1 }); // row (default)
  }
  return pos;
}

function drawScene(canvas, scene) {
  const size = canvas.clientWidth, dpr = window.devicePixelRatio || 1;
  canvas.width = canvas.height = size * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--text-main").trim(), accent = css.getPropertyValue("--accent").trim();
  g.clearRect(0, 0, size, size);
  const cx = size / 2, cy = size / 2, base = size * 0.42;
  const duotone = scene.palette === "duotone";
  (scene.layers || []).forEach((layer, li) => {
    const gen = GLYPH[layer.glyph] || GLYPH.circle;
    const shape = gen(rng((layer.seed || 1) + li * 7919));
    layoutPositions(scene.layout, layer.count).forEach((p, idx) => {
      const s = base * layer.size * layer.scale * p.s;
      if (s <= 0) return;
      g.save();
      g.translate(cx + p.x * base, cy + p.y * base);
      g.rotate(((layer.rotation + (scene.layout === "radial" ? (idx / layer.count) * 360 : 0)) * Math.PI) / 180);
      g.scale(s, s);
      const color = duotone && layer.role !== "anchor" ? accent : ink;
      g.strokeStyle = color; g.fillStyle = color;
      g.lineWidth = 1.6 / s;
      g.globalAlpha = layer.role === "echo" ? 0.5 : layer.role === "accent" ? 0.85 : 1;
      (shape.paths || []).forEach(path => { g.beginPath(); path.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke(); });
      (shape.dots || []).forEach(([x, y, rr]) => { g.beginPath(); g.arc(x, y, rr, 0, TAU); g.fill(); });
      g.restore();
    });
  });
}

// ---- legacy parametric-curve path (older stored pools) ----
function points(p) {
  const out = [], N = 900;
  if (p.kind === "rose") { const k = p.a / p.b, span = TAU * p.b; for (let i = 0; i <= N; i++) { const t = (i / N) * span, r = Math.cos(k * t + p.phase); out.push([r * Math.cos(t), r * Math.sin(t)]); } }
  else if (p.kind === "lissajous") { for (let i = 0; i <= N; i++) { const t = (i / N) * TAU; out.push([Math.sin(p.a * t + p.phase), Math.sin(p.b * t)]); } }
  else if (p.kind === "maurer") { const k = p.a / p.b, step = (Math.abs(p.c) || 1) * Math.PI / 180; for (let i = 0; i <= 720; i++) { const t = i * step, r = Math.cos(k * t); out.push([r * Math.cos(t), r * Math.sin(t)]); } }
  else if (p.kind === "super") { const m = Math.min(12, Math.max(2, Math.round(p.a))); const n1 = Math.min(10, Math.max(0.1, p.b)), n2 = Math.min(10, Math.max(0.1, p.c)), n3 = Math.min(10, Math.max(0.1, p.d)); for (let i = 0; i <= N; i++) { const th = (i / N) * TAU; const u = Math.pow(Math.pow(Math.abs(Math.cos(m * th / 4)), n2) + Math.pow(Math.abs(Math.sin(m * th / 4)), n3), -1 / n1); const r = Math.min(5, u); out.push([r * Math.cos(th), r * Math.sin(th)]); } }
  else if (p.kind === "harm") { const d = Math.min(0.5, Math.max(0.005, Math.abs(p.e) || 0.05)), M = 1500; for (let i = 0; i <= M; i++) { const t = (i / M) * 60, e1 = Math.exp(-d * t), e2 = Math.exp(-d * 1.1 * t); out.push([Math.sin(p.a * t + p.phase) * e1 + Math.sin(p.b * t) * e2, Math.sin(p.c * t + p.phase / 2) * e1 + Math.sin(p.d * t) * e2]); } }
  else { const R = Math.round(p.a) || 5, r = Math.round(p.b) || 2, d = p.c, span = TAU * (r / gcd(R, r)); for (let i = 0; i <= N * 2; i++) { const t = (i / (N * 2)) * span; out.push([(R - r) * Math.cos(t) + d * Math.cos(((R - r) / r) * t), (R - r) * Math.sin(t) - d * Math.sin(((R - r) / r) * t)]); } }
  return norm(out);
}
function drawLegacy(canvas, p) {
  const size = canvas.clientWidth, dpr = window.devicePixelRatio || 1;
  canvas.width = canvas.height = size * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--text-main").trim(), accent = css.getPropertyValue("--accent").trim();
  g.clearRect(0, 0, size, size);
  const pts = points(p);
  for (let L = 0; L < p.layers; L++) {
    g.save();
    g.translate(size / 2, size / 2);
    g.rotate(((p.rotate + L * (180 / (p.layers + 1))) * Math.PI) / 180);
    g.scale(size * 0.44, size * 0.44);
    g.beginPath();
    pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    g.lineWidth = 1.3 / (size * 0.44);
    g.strokeStyle = L === 0 ? ink : accent;
    g.globalAlpha = L === 0 ? 1 : 0.75;
    g.stroke();
    g.restore();
  }
}

export function mount(box, qs, { onDone }) {
  const answers = {};
  let i = 0;
  const canvas = h("canvas", { class: "art-canvas", role: "img", "aria-label": "A line drawing. Describe what you see." });
  const word = h("input", { class: "input", id: "word", maxlength: "60", autocomplete: "off", "aria-label": "One word or short phrase for what you see" });
  const count = h("p", { class: "mono muted" });
  const hintBtn = h("button", { class: "link-btn", type: "button" }, "Hint");
  const hintText = h("p", { class: "art-hint muted small" });
  hintBtn.addEventListener("click", () => { hintText.hidden = !hintText.hidden; });
  const next = () => { const w = word.value.trim(); if (w) answers[qs[i].id] = w; i++; word.value = ""; show(); };
  word.addEventListener("keydown", (e) => { if (e.key === "Enter") next(); });
  box.append(count, canvas, h("div", { class: "row-actions" }, hintBtn, hintText),
    h("div", { class: "row-actions" }, word,
      h("button", { class: "btn btn--primary", type: "button", onclick: next }, "Next"),
      h("button", { class: "link-btn", type: "button", onclick: () => { word.value = ""; next(); } }, "I see nothing")),
    h("p", { class: "muted small mono" }, "One word. First thing you saw. No wrong answers."));
  function show() {
    if (i >= qs.length) return onDone();
    const q = qs[i];
    count.textContent = `Image ${i + 1} of ${qs.length}`;
    if (q.type === "scene") drawScene(canvas, q); else drawLegacy(canvas, q.params);
    hintText.textContent = q.hint || "";
    hintText.hidden = true;
    hintBtn.style.display = q.hint ? "" : "none";
    word.focus();
  }
  requestAnimationFrame(show);
  return { collect: () => ({ answers }), destroy: () => box.replaceChildren() };
}
