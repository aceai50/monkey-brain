import { h } from "../dom.js";

function display(q) {
  if (q.type === "grid") {
    return h("div", { class: "grid-pattern", style: null, role: "img", "aria-label": "Symbol grid with one missing cell" },
      ...q.display.flatMap((row) => row.map((c) => h("span", { class: "cell mono" + (c === "?" ? " is-q" : "") }, c))));
  }
  return h("div", { class: "seq", role: "img", "aria-label": "Sequence: " + q.display.join(", ") },
    ...q.display.map((c) => h("span", { class: "cell mono" + (c === "?" ? " is-q" : "") }, c)));
}

export function mount(box, qs, { onDone }) {
  const answers = {};
  let i = 0, done = 0;
  const count = h("p", { class: "mono muted" });
  const area = h("div", { class: "q" });
  box.append(count, area);

  function show() {
    if (i >= qs.length) return onDone();
    const q = qs[i];
    count.textContent = `${done} answered`;
    const pick = (k) => { if (k != null) { answers[q.id] = k; done++; } i++; show(); };
    const d = display(q);
    if (q.type === "grid") d.style.setProperty("--n", q.display.length);
    area.replaceChildren(h("p", {}, q.prompt), d,
      h("div", { class: "options" }, ...q.options.map((o, k) => h("button", { class: "btn mono", type: "button", onclick: () => pick(k) }, o))),
      h("button", { class: "link-btn", type: "button", onclick: () => pick(null) }, "Skip"));
  }
  show();
  return { collect: () => ({ answers }), destroy: () => box.replaceChildren() };
}
