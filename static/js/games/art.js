import { h } from "../dom.js";

const TAU = Math.PI * 2;
const gcd = (a, b) => (b ? gcd(b, a % b) : a);

function points(p) {
  const out = [];
  const N = 900;
  if (p.kind === "rose") {
    const k = p.a / p.b, span = TAU * p.b;
    for (let i = 0; i <= N; i++) { const t = (i / N) * span; const r = Math.cos(k * t + p.phase); out.push([r * Math.cos(t), r * Math.sin(t)]); }
  } else if (p.kind === "lissajous") {
    for (let i = 0; i <= N; i++) { const t = (i / N) * TAU; out.push([Math.sin(p.a * t + p.phase), Math.sin(p.b * t)]); }
  } else if (p.kind === "maurer") {
    const k = p.a / p.b, step = (Math.abs(p.c) || 1) * Math.PI / 180;
    for (let i = 0; i <= 720; i++) { const t = i * step; const r = Math.cos(k * t); out.push([r * Math.cos(t), r * Math.sin(t)]); }
  } else if (p.kind === "super") {
    const m = Math.min(12, Math.max(2, Math.round(p.a)));
    const n1 = Math.min(10, Math.max(0.1, p.b)), n2 = Math.min(10, Math.max(0.1, p.c)), n3 = Math.min(10, Math.max(0.1, p.d));
    for (let i = 0; i <= N; i++) {
      const th = (i / N) * TAU;
      const u = Math.pow(Math.pow(Math.abs(Math.cos(m * th / 4)), n2) + Math.pow(Math.abs(Math.sin(m * th / 4)), n3), -1 / n1);
      const r = Math.min(5, u);
      out.push([r * Math.cos(th), r * Math.sin(th)]);
    }
  } else if (p.kind === "harm") {
    const d = Math.min(0.5, Math.max(0.005, Math.abs(p.e) || 0.05)), M = 1500;
    for (let i = 0; i <= M; i++) {
      const t = (i / M) * 60, e1 = Math.exp(-d * t), e2 = Math.exp(-d * 1.1 * t);
      out.push([Math.sin(p.a * t + p.phase) * e1 + Math.sin(p.b * t) * e2,
        Math.sin(p.c * t + p.phase / 2) * e1 + Math.sin(p.d * t) * e2]);
    }
  } else {
    const R = Math.round(p.a) || 5, r = Math.round(p.b) || 2, d = p.c, span = TAU * (r / gcd(R, r));
    for (let i = 0; i <= N * 2; i++) { const t = (i / (N * 2)) * span; out.push([(R - r) * Math.cos(t) + d * Math.cos(((R - r) / r) * t), (R - r) * Math.sin(t) - d * Math.sin(((R - r) / r) * t)]); }
  }
  const finite = out.filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  const m = Math.max(...finite.flat().map(Math.abs)) || 1;
  return finite.map(([x, y]) => [x / m, y / m]);
}

function draw(canvas, p) {
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
  const canvas = h("canvas", { class: "art-canvas", role: "img", "aria-label": "An abstract curve. Describe what you see." });
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
    count.textContent = `Image ${i + 1} of ${qs.length}`;
    draw(canvas, qs[i].params);
    hintText.textContent = qs[i].hint || "";
    hintText.hidden = true;
    hintBtn.style.display = qs[i].hint ? "" : "none";
    word.focus();
  }
  requestAnimationFrame(show);
  return { collect: () => ({ answers }), destroy: () => box.replaceChildren() };
}
