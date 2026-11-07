import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, switchToggle, tally, toast, topbar } from "./dom.js";
import { enterSMode, exitSMode } from "./s-mode.js";

const PILLARS = [
  ["arithmetic", "Arithmetic"], ["critical", "Critical thinking"], ["pattern", "Pattern recognition"],
  ["art", "Art and perspective"], ["kinesthetic", "Reflexes"],
];
const LEVELS = [["auto", "Auto"], ["easy", "Easy"], ["medium", "Medium"], ["hard", "Hard"]];

function segmented(name, options, value, onChange) {
  const wrap = h("div", { class: "segmented", role: "radiogroup", "aria-label": name });
  const draw = () => wrap.replaceChildren(...options.map(([v, label]) =>
    h("button", { type: "button", role: "radio", "aria-checked": String(v === value), onclick: () => { value = v; draw(); onChange(v); } }, label)));
  draw();
  return wrap;
}

export function dashboardView(root, ctx) {
  const u = state.user;
  const first = u.name.split(" ")[0];
  const metric = (value, label) => h("div", { class: "metric" }, h("div", { class: "metric__value" }, value), h("div", { class: "metric__label" }, label));
  const circuit = h("section", { class: "circuit panel" }, h("p", { class: "muted mono" }, "Loading your numbers..."));
  const metrics = h("aside", { class: "metrics", "aria-label": "Your numbers" });
  const practice = h("section", { class: "practice panel" });
  const privacy = privacyPanel();
  const mult = h("section", { class: "mult panel", "aria-label": "How your points are calculated" });
  root.append(
    topbar("dashboard", () => logout(ctx), u),
    h("section", { class: "page-head" }, h("h1", {}, `Hey, ${first}.`),
      h("p", {}, "Today's circuit takes about 23 minutes: five sets of four minutes with thirty seconds to breathe in between.")),
    circuit, metrics, practice, privacy, mult);
  sModeGate(root, ctx);

  let pick = "auto";
  async function start() {
    try {
      const s = await api("/api/sessions/circuit", { method: "POST", body: { difficulty: pick } });
      ctx.navigate("#/train/" + s.id);
    } catch (err) {
      circuit.prepend(notice("error", err.message));
    }
  }

  api("/api/users/me/dashboard").then((d) => {
    const t = d.today;
    const done = t ? t.pillars.filter((p) => ["submitted", "expired"].includes(p.status)).length : 0;
    const body = [h("h2", {}, "Daily circuit")];
    if (t?.state === "finished") {
      body.push(h("p", {}, `Done for today: ${t.report.total_points} points. The next circuit opens at midnight IST.`), tally(5, true),
        h("a", { class: "btn btn--primary", href: "#/train/" + t.id }, "View today's report"));
    } else if (t) {
      body.push(h("p", {}, `You're ${done} of 5 sets in. Pick up where you left off.`), tally(done, true),
        h("a", { class: "btn btn--primary", href: "#/train/" + t.id }, "Resume circuit"));
    } else {
      body.push(h("p", {}, "Arithmetic, critical thinking, patterns, art, and reflexes."),
        h("div", { class: "field" }, h("span", { class: "label" }, "Difficulty"),
          segmented("Difficulty", LEVELS, pick, (v) => { pick = v; hint.textContent = HINTS[v]; })),
        tally(0, true));
      var hint = h("p", { class: "muted small mono" }, "Auto adapts to you. It starts easy and climbs as you improve.");
      body.splice(3, 0, hint);
      body.push(h("button", { class: "btn btn--primary", type: "button", onclick: start }, "Start daily circuit"));
    }
    circuit.replaceChildren(...body);

    const hist = h("div", { class: "history", role: "img", "aria-label": "Points for the last 7 days" },
      ...d.history.map((x) => h("span", { class: "history__day" + (x.points != null ? " is-on" : ""), title: `${x.date}: ${x.points ?? "no circuit"}` },
        h("span", { class: "history__bar" }), h("span", { class: "history__n mono small" }, x.points ?? "-"))));
    metrics.replaceChildren(
      metric(d.streak, "day streak"), metric(d.total_points, "lifetime points"),
      metric(d.today_points ?? "-", "today's score"),
      metric(d.questions_solved, d.today_solved != null ? `questions solved (${d.today_solved} of ${d.today_offered} today)` : "questions solved"),
      metric(d.best_reaction_ms ? `${d.best_reaction_ms}ms` : "-", "best reaction time"), hist);
    drawMultipliers(mult, d.multipliers);
    for (const x of metrics.querySelectorAll(".history__bar")) {
      const n = Number(x.parentElement.querySelector(".history__n").textContent);
      x.style.setProperty("--h", Number.isFinite(n) ? Math.min(100, (n / 400) * 100) + "%" : "4%");
    }
  }).catch((e) => circuit.replaceChildren(notice("error", e.message)));

  drawPractice(practice, ctx);
}

const PILLAR_LABEL = Object.fromEntries(PILLARS);

// Leaderboard privacy: saves immediately, snaps back if the server says no.
function privacyPanel() {
  const note = h("p", { class: "muted small" });
  const msg = h("div", {});
  const say = (pub) => { note.textContent = pub
    ? "Public: your alias and points appear on the daily board and the hall of fame."
    : "Private: other players can't see you or your points. You still see your own rank."; };
  say(state.user.display_on_leaderboard);
  const sw = switchToggle({
    id: "privacy", label: "Show my score on the leaderboard", checked: state.user.display_on_leaderboard,
    onChange: async (pub) => {
      msg.replaceChildren();
      state.user = await api("/api/users/me", { method: "PATCH", body: { display_on_leaderboard: pub } });
      say(pub); toast(pub ? "Your score is public" : "Your score is private");
    },
    onError: (e) => msg.replaceChildren(notice("error", e.message)),
  });
  return h("section", { class: "privacy panel" }, h("h3", {}, "Leaderboard privacy"), sw, note, msg);
}

// The two multipliers behind your points, per pillar. Points = score x difficulty x volume.
function drawMultipliers(box, m) {
  box.replaceChildren(
    h("div", { class: "row-actions row-actions--between" }, h("h3", {}, "How your points add up"),
      h("a", { class: "btn", href: "#/customize" }, "Customize questions")),
    h("p", { class: "muted small" }, "Points per pillar = score x difficulty multiplier x volume multiplier. Harder levels and more questions both multiply what you earn."),
    h("div", { class: "table-wrap" }, h("table", { class: "report" },
      h("thead", {}, h("tr", {}, ...["Pillar", "Auto level", "Difficulty x", "Questions", "Volume x"].map((c) => h("th", {}, c)))),
      h("tbody", {}, ...PILLARS.map(([k]) => h("tr", {},
        h("th", { scope: "row" }, PILLAR_LABEL[k]), h("td", { class: "mono" }, `${m[k].level}%`),
        h("td", { class: "mono" }, `x${m[k].difficulty.toFixed(2)}`), h("td", { class: "mono" }, m[k].count),
        h("td", { class: "mono" }, `x${m[k].volume.toFixed(2)}`)))))));
}

const HINTS = {
  auto: "Auto adapts to you. It starts easy and climbs as you improve.",
  easy: "Easy sessions count for about half the points of Expert.",
  medium: "Medium doesn't train Auto much. Pick Hard three days running and Auto follows you up.",
  hard: "Hard earns more points. Strong results on three Hard days in a row push Auto up.",
};

function drawPractice(box, ctx) {
  let pillar = "arithmetic", level = "medium", slider = 0.5;
  const msg = h("div", {});
  const sliderEl = h("input", { type: "range", min: "0", max: "1", step: "0.05", value: "0.5", id: "slider", "aria-describedby": "slidehint",
    oninput: (e) => { slider = Number(e.target.value); level = "custom"; paint(); } });
  const slideHint = h("p", { class: "muted small mono", id: "slidehint" });
  const levelWrap = h("div", {});
  const paint = () => {
    slideHint.textContent = level === "custom"
      ? `Custom ${Math.round(slider * 100)}%: questions run from easier to harder in a smooth curve.`
      : `${level[0].toUpperCase() + level.slice(1)}: every question at that level.`;
    levelWrap.replaceChildren(segmented("Practice difficulty", [["easy", "Easy"], ["medium", "Medium"], ["hard", "Hard"]].concat(level === "custom" ? [["custom", "Custom"]] : []), level,
      (v) => { level = v; if (v === "easy") sliderEl.value = slider = 0; if (v === "medium") sliderEl.value = slider = 0.5; if (v === "hard") sliderEl.value = slider = 1; paint(); }));
  };
  paint();
  const go = h("button", { class: "btn", type: "button", onclick: async () => {
    msg.replaceChildren(); go.disabled = true;
    try {
      const s = await api("/api/practice", { method: "POST", body: { pillar, difficulty: level, slider } });
      ctx.navigate("#/train/" + s.id);
    } catch (e) { msg.replaceChildren(notice("error", e.message)); go.disabled = false; }
  } }, "Start practice");
  box.append(h("h2", {}, "Practice one pillar"),
    h("p", { class: "muted" }, "Four minutes on a single pillar. Practice doesn't change Auto difficulty and doesn't score points."), msg,
    h("div", { class: "field" }, h("label", { for: "pillar" }, "Pillar"),
      h("select", { class: "input", id: "pillar", onchange: (e) => { pillar = e.target.value; } }, ...PILLARS.map(([v, l]) => h("option", { value: v }, l)))),
    h("div", { class: "field" }, h("span", { class: "label" }, "Difficulty"), levelWrap,
      h("label", { class: "label", for: "slider" }, "Or slide between levels"), sliderEl,
      h("div", { class: "slider-scale mono small muted" }, h("span", {}, "Easy"), h("span", {}, "Medium"), h("span", {}, "Hard")), slideHint),
    go);
}

export function settingsView(root, ctx) {
  const u = state.user;
  const msg = h("div", {});
  const name = h("input", { class: "input", id: "name", maxlength: "50", required: true, value: u.name });
  const alias = h("input", { class: "input", id: "alias", maxlength: "20", required: true, value: u.alias, autocomplete: "off", pattern: "[A-Za-z0-9_.\\-]{3,20}" });
  const board = privacyPanel();
  const save = h("button", { class: "btn btn--primary", type: "submit" }, "Save changes");

  const delMsg = h("div", {});
  const delPw = h("input", { class: "input", id: "delpw", type: "password", maxlength: "72", autocomplete: "current-password" });
  const delConfirm = h("input", { class: "input", id: "delconfirm", autocomplete: "off", placeholder: "DELETE" });
  const delBtn = h("button", { class: "btn", type: "submit" }, "Delete my account");

  root.append(
    topbar("settings", () => logout(ctx), u),
    h("section", { class: "page-head" }, h("h1", {}, "Settings")),
    h("div", { class: "settings" },
      h("form", { class: "panel", onsubmit: async (e) => {
        e.preventDefault(); save.disabled = true; msg.replaceChildren();
        try {
          state.user = await api("/api/users/me", { method: "PATCH", body: { name: name.value, alias: alias.value } });
          toast("Changes saved");
        } catch (err) { msg.replaceChildren(notice("error", err.message)); } finally { save.disabled = false; }
      } },
        h("h3", {}, "Profile"), msg,
        h("div", { class: "field" }, h("label", { for: "email" }, "Email"), h("input", { class: "input", id: "email", value: u.email, readonly: true }), h("span", { class: "hint" }, "Can't be changed. It's tied to your sign-in.")),
        h("div", { class: "field" }, h("label", { for: "name" }, "Name"), name),
        h("div", { class: "field" }, h("label", { for: "alias" }, "Alias"), alias, h("span", { class: "hint" }, "Shown to other people.")),
        save),
      board,
      h("form", { class: "panel panel--hatch", onsubmit: async (e) => {
        e.preventDefault(); delMsg.replaceChildren();
        if (delConfirm.value !== "DELETE") { delMsg.replaceChildren(notice("error", "Type DELETE exactly to confirm.")); return; }
        delBtn.disabled = true;
        try {
          await api("/api/users/me/delete", { method: "POST", body: { confirm: "DELETE", password: u.has_password ? delPw.value : null } });
          await logout(ctx);
        } catch (err) { delMsg.replaceChildren(notice("error", err.message)); delBtn.disabled = false; }
      } },
        h("h3", {}, "Delete account"),
        h("p", { class: "muted" }, "Permanently removes your profile, scores, and sessions. This can't be undone."), delMsg,
        u.has_password ? h("div", { class: "field" }, h("label", { for: "delpw" }, "Password"), delPw) : null,
        h("div", { class: "field" }, h("label", { for: "delconfirm" }, "Type DELETE to confirm"), delConfirm),
        delBtn)));
}

// The hidden door. Sits alone at the very bottom of the dashboard, no nav entry.
function sModeGate(root, ctx) {
  api("/api/auth/config", { auth: false }).then((cfg) => {
    if (!cfg.s_mode_enabled) return;
    const on = document.documentElement.hasAttribute("data-s-mode");
    const sw = h("button", { type: "button", class: "switch", role: "switch",
      "aria-checked": String(on), "aria-label": "S-mode" },
      h("span", { class: "switch__track" }, h("span", { class: "switch__thumb" })));
    sw.addEventListener("click", () => {
      const next = sw.getAttribute("aria-checked") !== "true";
      sw.setAttribute("aria-checked", String(next));
      if (next) {
        sModeLoading();
        setTimeout(() => enterSMode(ctx), 1600);
      } else {
        exitSMode(ctx);
      }
    });
    root.append(h("section", { class: "s-mode-gate" },
      h("h3", { class: "mono" }, "S - M O D E"),
      h("p", { class: "muted small" }, "There is another wing. It does not count toward your points."),
      sw));
  }).catch(() => {});
}

function sModeLoading() {
  const el = h("div", { class: "s-mode-loader", role: "status", "aria-live": "assertive" },
    h("div", { class: "hourglass" }),
    h("div", { class: "loadbar" }, h("span")),
    h("p", { class: "mono small" }, "Entering S-mode..."));
  document.body.append(el);
  setTimeout(() => el.remove(), 1800);
}

