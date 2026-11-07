import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, render, topbar } from "./dom.js";

const DARK_PILLARS = [
  { id: "surveillance", name: "Surveillance", live: true,
    blurb: "One conversation. Read a stranger and earn what they never say out loud." },
  { id: "leverage", name: "Leverage", live: false, blurb: "Coming eventually." },
  { id: "contraband", name: "Contraband", live: false, blurb: "Coming eventually." },
];

function segmented(name, options, value, onChange) {
  const wrap = h("div", { class: "segmented", role: "radiogroup", "aria-label": name });
  const draw = () => wrap.replaceChildren(...options.map(([v, label]) =>
    h("button", { type: "button", role: "radio", "aria-checked": String(v === value),
      onclick: () => { value = v; draw(); onChange(v); } }, label)));
  draw();
  return wrap;
}

export function enterSMode(ctx) {
  localStorage.setItem("bm_s_mode", "1");
  document.documentElement.setAttribute("data-s-mode", "");
  ctx.navigate("#/s-mode");
}

export function exitSMode(ctx) {
  localStorage.removeItem("bm_s_mode");
  document.documentElement.removeAttribute("data-s-mode");
  if (ctx) ctx.navigate("#/dashboard");
}

const OUTCOME_LABEL = { success: "Mission successful", bored: "They got bored and left", burned: "You burned every point" };

export function sModeView(root, ctx) {
  let difficulty = "medium";
  const msg = h("div", {});
  const runs = h("div", { class: "hist-body" });
  const startBtn = h("button", { class: "btn btn--primary", type: "button", onclick: async () => {
    startBtn.disabled = true;
    msg.replaceChildren();
    try {
      const s = await api("/api/dark/start", { method: "POST", body: { difficulty } });
      ctx.navigate(`#/dark/${s.id}`);
    } catch (e) { render(msg, notice("error", e.message)); startBtn.disabled = false; }
  } }, "Start run");

  const diff = segmented("Difficulty", [["easy", "Easy"], ["medium", "Medium"], ["hard", "Hard"]],
    difficulty, (v) => { difficulty = v; });

  root.append(
    topbar("s-mode", () => logout(ctx), state.user),
    h("section", { class: "page-head" },
      h("h1", {}, "S-mode"),
      h("p", {}, "A darker wing of the gym. Its scores are called dark points and never touch your streak, your points or the leaderboard.")),
    h("section", { class: "board-wrap" },
      h("div", { class: "panel dark-entry" },
        h("h2", {}, "Surveillance"),
        h("p", { class: "lede" }, "Five social points, three secrets they won't hand over for free. Pick your approach badly and the door closes."),
        h("div", { class: "field" }, h("span", { class: "label" }, "Difficulty"), diff),
        msg,
        h("div", { class: "row-actions" }, startBtn)),
      h("h2", {}, "Recent runs"),
      runs,
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn--quiet", type: "button", onclick: () => exitSMode(ctx) }, "Leave S-mode"))));

  api("/api/sessions/history?mode=dark&limit=8").then((d) => {
    if (!d.entries.length) { render(runs, h("p", { class: "muted" }, "No dark runs yet.")); return; }
    render(runs, h("div", { class: "table-wrap" }, h("table", { class: "report" },
      h("thead", {}, h("tr", {}, ...["Date", "Difficulty", "Outcome", "Dark points"].map((c) => h("th", {}, c)))),
      h("tbody", {}, ...d.entries.map((e) => h("tr", {},
        h("td", { class: "mono" }, e.date),
        h("td", { class: "mono" }, e.difficulty || "-"),
        h("td", {}, OUTCOME_LABEL[e.outcome] || "-"),
        h("td", { class: "mono" }, e.total_points ?? 0)))))));
  }).catch((e) => render(runs, notice("error", e.message)));
}
