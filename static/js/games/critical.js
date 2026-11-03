import { h } from "../dom.js";

export function mount(box, qs, { onDone }) {
  const answers = {};
  let i = 0, off = null;
  const area = h("div", { class: "q q--read" });
  box.append(area);

  function show() {
    if (i >= qs.length) return onDone();
    const q = qs[i];
    const pick = (k) => { answers[q.id] = k; i++; show(); };
    area.replaceChildren(
      h("p", { class: "muted mono" }, `Scenario ${i + 1} of ${qs.length}`),
      h("p", { class: "scenario" }, q.scenario), h("h3", {}, q.prompt),
      h("div", { class: "options options--stack" }, ...q.options.map((o, k) =>
        h("button", { class: "btn btn--left", type: "button", onclick: () => pick(k) }, h("span", { class: "key mono" }, k + 1), o))),
      h("button", { class: "link-btn", type: "button", onclick: () => { i++; show(); } }, "Skip"));
  }
  const keys = (e) => { const k = Number(e.key); if (k >= 1 && k <= 4 && i < qs.length && !e.target.matches("input")) { answers[qs[i].id] = k - 1; i++; show(); } };
  addEventListener("keydown", keys);
  show();
  return { collect: () => ({ answers }), destroy: () => { removeEventListener("keydown", keys); box.replaceChildren(); } };
}
