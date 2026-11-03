import { h } from "../dom.js";

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
    const next = (v) => { if (v !== "" && v != null) { answers[q.id] = v; done++; } i++; show(); };
    if (q.options) {
      area.replaceChildren(h("p", { class: "expr mono" }, q.prompt), h("p", { class: "muted small" }, "Which operator(s) make this true?"),
        h("div", { class: "options" }, ...q.options.map((o) => h("button", { class: "btn", type: "button", onclick: () => next(o) }, o))),
        h("button", { class: "link-btn", type: "button", onclick: () => next("") }, "Skip"));
    } else {
      const inp = h("input", { class: "input answer", type: "text", inputmode: "text", autocomplete: "off", autocapitalize: "off", spellcheck: "false", "aria-label": "Your answer", maxlength: "12" });
      const go = () => next(inp.value.replace(/[−–]/g, "-").trim());
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      area.replaceChildren(h("p", { class: "expr mono" }, q.prompt + (q.type === "calc" ? " = ?" : "")),
        h("div", { class: "row-actions" }, inp, h("button", { class: "btn btn--primary", type: "button", onclick: go }, "Lock in"),
          h("button", { class: "link-btn", type: "button", onclick: () => next("") }, "Skip")));
      inp.focus();
    }
  }
  show();
  return { collect: () => ({ answers }), destroy: () => box.replaceChildren() };
}
