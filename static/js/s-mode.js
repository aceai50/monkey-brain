import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, render, topbar } from "./dom.js";

const OUTCOME_LABEL = {
  success: "mission successful", bored: "they got bored", burned: "burned out",
  convinced: "convinced the room", partial: "partly convincing", busted: "busted",
};

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

export function sModeView(root, ctx) {
  let pillars = [];
  let runsData = null;
  let pillar = "surveillance";
  let difficulty = "medium";
  let timerOn = false;
  let timerPreset = "normal";
  let customSeconds = 20;
  let showDebts = true;
  const msg = h("div", {});
  const runs = h("div", { class: "hist-body" });
  const settingsBox = h("div", {});
  const diffBox = h("div", {});

  const startBtn = h("button", { class: "btn btn--primary", type: "button", onclick: async () => {
    startBtn.disabled = true;
    msg.replaceChildren();
    try {
      const res = await api("/api/dark/practice", { method: "POST",
        body: { pillar, difficulty, settings: { timer: { enabled: timerOn, preset: timerPreset,
          seconds: timerPreset === "custom" ? customSeconds : undefined }, show_debts: showDebts } } });
      ctx.navigate(res.runner);
    } catch (e) { render(msg, notice("error", e.message)); startBtn.disabled = false; }
  } }, "Start");

  const pillarSelect = h("select", { class: "input", id: "dpillar", onchange: (e) => {
    pillar = e.target.value; drawSettings(); drawDiff();
  } });

  function drawDiff() {
    const p = pillars.find((x) => x.id === pillar);
    const opts = (p ? p.difficulty : ["easy", "medium", "hard"]).map((d) => [d, d[0].toUpperCase() + d.slice(1)]);
    render(diffBox, segmented("Difficulty", opts, difficulty, (v) => { difficulty = v; }));
  }

  function toggleRow(label, value, onChange) {
    const sw = h("button", { type: "button", class: "switch", role: "switch",
      "aria-checked": String(value), "aria-label": label },
      h("span", { class: "switch__track" }, h("span", { class: "switch__thumb" })));
    sw.addEventListener("click", () => {
      const next = sw.getAttribute("aria-checked") !== "true";
      sw.setAttribute("aria-checked", String(next));
      onChange(next);
    });
    return h("div", { class: "switch-row" }, sw, h("span", { class: "switch__label" }, label));
  }

  function drawSettings() {
    if (pillar !== "act_deceive") { settingsBox.replaceChildren(); return; }
    const presetBox = h("div", {});
    const drawPresets = () => {
      if (!timerOn) { presetBox.replaceChildren(); return; }
      const kids = [segmented("Timer", [["relaxed", "Relaxed"], ["normal", "Normal"],
        ["tight", "Tight"], ["custom", "Custom"]], timerPreset, (v) => { timerPreset = v; drawPresets(); })];
      if (timerPreset === "custom") {
        const inp = h("input", { class: "input", type: "number", min: "10", max: "90", step: "5",
          value: String(customSeconds),
          oninput: (e) => { customSeconds = Math.min(90, Math.max(10, parseInt(e.target.value || "20", 10) || 20)); } });
        kids.push(h("div", { class: "field" }, h("label", { class: "label" }, "Seconds per answer"), inp));
      }
      render(presetBox, ...kids);
    };
    render(settingsBox,
      h("h3", { class: "mono small" }, "Actor settings"),
      toggleRow("Timed scene", timerOn, (v) => { timerOn = v; drawPresets(); }),
      presetBox,
      toggleRow("Reveal which claims were called in", showDebts, (v) => { showDebts = v; }));
    drawPresets();
  }

  root.append(
    topbar("s-mode", () => logout(ctx), state.user),
    h("section", { class: "page-head" },
      h("h1", {}, "S-mode"),
      h("p", {}, "A darker wing of the gym. Its scores are dark points and never touch your streak, your points or the leaderboard.")),
    h("section", { class: "board-wrap" },
      h("div", { class: "panel dark-entry" },
        h("h2", {}, "Dark practice"),
        h("p", { class: "lede" }, "Pick a pillar, pick a level, and step in. Progress saves when you leave."),
        h("div", { class: "field" }, h("label", { for: "dpillar" }, "Pillar"), pillarSelect),
        h("div", { class: "field" }, h("span", { class: "label" }, "Difficulty"), diffBox),
        settingsBox,
        msg,
        h("div", { class: "row-actions" }, startBtn)),
      h("h2", {}, "Recent runs"),
      runs,
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn--quiet", type: "button", onclick: () => exitSMode(ctx) }, "Leave S-mode"))));

  function drawRuns() {
    if (!runsData) return;
    const d = runsData;
    if (!d.entries.length) { render(runs, h("p", { class: "muted" }, "No dark runs yet.")); return; }
    const label = (id) => (pillars.find((p) => p.id === id) || {}).label || id;
    render(runs, h("div", { class: "table-wrap" }, h("table", { class: "report" },
      h("thead", {}, h("tr", {}, ...["Date", "Pillar", "Level", "Outcome", "Dark points"].map((c) => h("th", {}, c)))),
      h("tbody", {}, ...d.entries.map((e) => h("tr", {},
        h("td", { class: "mono" }, e.date),
        h("td", {}, label(e.pillar || "surveillance")),
        h("td", { class: "mono" }, e.difficulty || "-"),
        h("td", {}, OUTCOME_LABEL[e.outcome] || "-"),
        h("td", { class: "mono" }, e.total_points ?? 0)))))));
  }

  api("/api/dark/pillars").then((d) => {
    pillars = d.pillars || [];
    pillarSelect.replaceChildren(...pillars.map((p) => h("option", { value: p.id }, p.label)));
    drawDiff();
    drawSettings();
    drawRuns();
  }).catch((e) => render(msg, notice("error", e.message)));

  api("/api/sessions/history?mode=dark&limit=8").then((d) => {
    runsData = d;
    drawRuns();
  }).catch((e) => render(runs, notice("error", e.message)));
}
