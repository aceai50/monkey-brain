// Training runner. The SERVER owns time: each set has a server deadline; this file only displays it
// and submits when it hits zero (or when the player finishes early).
import { api } from "./api.js";
import { h, fmtClock, notice, sleep, tally } from "./dom.js";
import * as arithmetic from "./games/arithmetic.js";
import * as art from "./games/art.js";
import * as critical from "./games/critical.js";
import * as kinesthetic from "./games/kinesthetic.js";
import * as pattern from "./games/pattern.js";

const GAMES = { arithmetic, critical, pattern, art, kinesthetic };
const TITLES = {
  arithmetic: ["Arithmetic", "Solve as many as you can. Wrong answers cost half a point, so skip what you can't do."],
  critical: ["Critical thinking", "Find the flaw in each scenario. Take your time on each one; there's no penalty for a miss."],
  pattern: ["Pattern recognition", "Find the rule, pick what comes next. Wrong answers cost half a point."],
  art: ["Art and perspective", "One word for what you see first. There are no right answers."],
  kinesthetic: ["Reflexes", "React as fast as you can. Don't jump the gun: early taps score zero."],
};

export function trainView(root, ctx) {
  let alive = true;
  let tick = null;
  const stage = h("section", { class: "stage", "aria-live": "off" });
  const warn = (e) => { e.preventDefault(); e.returnValue = ""; };
  root.append(h("header", { class: "topbar" }, h("span", { class: "wordmark" }, tally(0), "Monkey Brain"),
    h("nav", { class: "nav" }, h("a", { href: "#/dashboard" }, "Leave (progress is saved)"))), stage);
  const cleanup = () => { alive = false; clearInterval(tick); removeEventListener("beforeunload", warn); };

  (async () => {
    try {
      let s = await api(`/api/sessions/${ctx.arg}`);
      if (s.state === "finished") return report(stage, s.report, ctx);
      addEventListener("beforeunload", warn);
      const total = s.pillars.length;
      const circuit = s.mode === "circuit";
      let first = true;
      for (let i = 0; i < total && alive; i++) {
        if (["submitted", "expired"].includes(s.pillars[i].status)) continue;
        const pillar = s.pillars[i].pillar;
        const doneCount = s.pillars.filter((p) => ["submitted", "expired"].includes(p.status)).length;
        if (circuit && !first) await breakScreen(stage, s.break_seconds, pillar, doneCount, () => alive);
        else await intro(stage, pillar, doneCount, s.pillars[i].level, total, circuit);
        first = false;
        if (!alive) return;
        await countIn(stage);
        const res = await api(`/api/sessions/${ctx.arg}/pillars/${i}/start`, { method: "POST" });
        await playSet(stage, res, doneCount, total, ctx.arg, i, () => alive, (t) => { tick = t; });
        s = await api(`/api/sessions/${ctx.arg}`);
      }
      if (!alive) return;
      removeEventListener("beforeunload", warn);
      s = await api(`/api/sessions/${ctx.arg}`);
      if (s.report) report(stage, s.report, ctx);
      else stage.replaceChildren(notice("error", "Something went wrong finishing this session."), h("a", { class: "btn", href: "#/dashboard" }, "Back to dashboard"));
    } catch (err) {
      if (!alive) return;
      removeEventListener("beforeunload", warn);
      stage.replaceChildren(h("h1", {}, "Can't run this."), notice("error", err.message), h("a", { class: "btn", href: "#/dashboard" }, "Back to dashboard"));
    }
  })();
  return cleanup;
}

function intro(stage, pillar, doneCount, level, total, circuit) {
  return new Promise((resolve) => {
    const [name, how] = TITLES[pillar];
    stage.replaceChildren(
      h("h1", {}, name), h("p", { class: "lede" }, how),
      h("p", { class: "mono muted" }, `${level} level. Four minutes. The clock starts after a short countdown.`),
      circuit ? tally(doneCount, true) : null,
      h("button", { class: "btn btn--primary", type: "button", onclick: resolve }, "Begin"));
    stage.querySelector("button").focus();
  });
}

async function breakScreen(stage, seconds, nextPillar, doneCount, alive) {
  const [name, how] = TITLES[nextPillar];
  const clock = h("div", { class: "metric__value big" }, fmtClock(seconds));
  stage.replaceChildren(h("h1", {}, "Breathe."), h("p", { class: "lede" }, "Drop your shoulders and unclench your jaw. Next up: " + name + "."),
    tally(doneCount, true), clock, h("p", { class: "muted mono" }, how));
  const end = Date.now() + seconds * 1000;
  while (alive() && Date.now() < end) {
    clock.textContent = fmtClock((end - Date.now()) / 1000);
    await sleep(200);
  }
}

async function countIn(stage) {
  const n = h("div", { class: "countin", "aria-live": "assertive" });
  stage.replaceChildren(n);
  for (const t of ["3", "2", "1"]) { n.textContent = t; await sleep(650); }
}

function playSet(stage, res, doneCount, total, sid, index, alive, setTick) {
  return new Promise((resolve) => {
    const offset = Date.parse(res.server_now) - Date.now();
    const deadline = Date.parse(res.deadline_at);
    const totalMs = 240000;
    const left = () => Math.max(0, deadline - (Date.now() + offset));
    const [name] = TITLES[res.pillar];
    const clock = h("span", { class: "clock mono" }, fmtClock(left() / 1000));
    const fill = h("span", { class: "timebar__fill" });
    const bar = h("div", { class: "timebar", role: "progressbar", "aria-label": "Time left", "aria-valuemin": "0", "aria-valuemax": "240" }, fill);
    const box = h("div", { class: "game" });
    stage.replaceChildren(
      h("div", { class: "hud" }, h("h2", {}, name), tally(doneCount), clock), bar, box);

    let finished = false;
    let game;
    async function finish() {
      if (finished) return;
      finished = true;
      clearInterval(timer);
      const payload = game.collect();
      game.destroy();
      stage.replaceChildren(h("p", { class: "lede" }, "Saving..."));
      for (let attempt = 0; attempt < 4; attempt++) {
        try { await api(`/api/sessions/${sid}/pillars/${index}/submit`, { method: "POST", body: payload }); break; }
        catch (e) { if (e.status === 409 || e.status === 404) break; await sleep(800); }   // 409: already closed by the server
      }
      resolve();
    }
    game = GAMES[res.pillar].mount(box, res.questions, { onDone: () => queueMicrotask(finish) });
    const timer = setInterval(() => {
      if (!alive()) { clearInterval(timer); game.destroy(); return resolve(); }
      const l = left();
      clock.textContent = fmtClock(l / 1000);
      fill.style.transform = `scaleX(${l / totalMs})`;
      bar.setAttribute("aria-valuenow", String(Math.round(l / 1000)));
      bar.classList.toggle("is-low", l < 30000);
      if (l <= 0) finish();
    }, 100);
    setTick(timer);
  });
}

function report(stage, r, ctx) {
  const circuit = r.mode === "circuit";
  const rows = r.pillars.map((p) => h("tr", {},
    h("th", { scope: "row" }, TITLES[p.pillar][0]),
    h("td", { class: "mono" }, p.level),
    h("td", { class: "mono" }, `${p.correct} of ${p.offered}`),
    h("td", { class: "mono" }, p.pillar === "kinesthetic" ? (p.avg_rt_ms ? `${p.avg_rt_ms}ms avg` : "-") : `${Math.round((p.accuracy || 0) * 100)}%`),
    h("td", {}, h("span", { class: "scorebar", role: "img", "aria-label": `${Math.round(p.score)} out of 100` }, h("span", { class: "scorebar__fill" }),
      h("span", { class: "scorebar__n mono" }, Math.round(p.score)))),
    circuit ? h("td", { class: "mono" }, `x${p.weight.toFixed(2)}`) : null,
    circuit ? h("td", { class: "mono" }, `x${p.volume.toFixed(2)}`) : null,
    circuit ? h("td", { class: "mono" }, p.points) : null));
  const head = ["Pillar", "Level", "Solved", "Accuracy", "Score"].concat(circuit ? ["Difficulty", "Volume", "Points"] : []);
  const reviews = r.pillars.find((p) => p.pillar === "critical")?.review || [];
  const arch = r.pillars.find((p) => p.pillar === "art")?.archetype;
  const flagged = r.pillars.filter((p) => p.flags?.length);
  stage.replaceChildren(
    h("h1", {}, circuit ? `${r.total_points} points.` : "Set complete."),
    h("p", { class: "lede" }, circuit ? `${r.questions_solved} of ${r.questions_offered} questions solved. Day ${r.streak} of your streak. Average level: ${r.level_avg.toFixed(1)} of 3.` : "Practice doesn't score points or change your Auto level."),
    tally(r.pillars.length === 5 ? 5 : 0, true),
    h("div", { class: "table-wrap" }, h("table", { class: "report" },
      h("thead", {}, h("tr", {}, ...head.map((c) => h("th", {}, c)))), h("tbody", {}, rows))),
    arch ? h("div", { class: "panel" }, h("h3", {}, arch.name), h("p", {}, arch.text), h("p", { class: "muted small mono" }, "Just for fun. It's a mirror, not a diagnosis.")) : null,
    flagged.length ? notice("error", "Some results were zeroed: " + flagged.map((p) => `${TITLES[p.pillar][0]} (${p.flags.join(", ").replace(/_/g, " ")})`).join("; ")) : null,
    reviews.length ? h("div", { class: "reviews" }, h("h2", {}, "Critical thinking review"), ...reviews.map((q) =>
      h("details", { class: "review" + (q.correct ? " is-right" : " is-wrong") },
        h("summary", {}, (q.correct ? "Right: " : "Missed: ") + (q.prompt || "")),
        h("p", {}, q.scenario || ""),
        h("p", { class: "mono small" }, `Your answer: ${q.your ?? "none"}`), h("p", { class: "mono small" }, `Best answer: ${q.right || ""}`),
        h("p", {}, q.explanation || "")))) : null,
    h("div", { class: "row-actions" },
      h("a", { class: "btn btn--primary", href: "#/dashboard" }, "Back to dashboard"),
      circuit ? h("a", { class: "btn", href: "#/leaderboard" }, "See the leaderboard") : null));
  for (const [i, el] of [...stage.querySelectorAll(".scorebar__fill")].entries()) el.style.setProperty("--w", Math.round(r.pillars[i].score) + "%");
}
