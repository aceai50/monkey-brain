import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { howToPlay } from "./guide.js";
import { h, notice, render, toast, topbar } from "./dom.js";

const OUTCOME = { convinced: "Mission successful.", partial: "Half the room bought it.", busted: "Busted." };
const BAND = { bought_it: "Bought it", suspicious: "Suspicious", sees_through: "Saw through you" };
const MOVE_INTENT = "You were handed a role and a few facts you actually know. Each turn offers four moves: assert something you know, deflect, stand by what you already said, or reach for a detail you were never given. That last one buys credibility now but is a debt — a later guest may call it in. Contradicting yourself is the loudest tell of all.";

export function actView(root, ctx) {
  const sid = ctx.arg;
  const stage = h("section", { class: "stage act" });
  root.append(topbar("s-mode", () => logout(ctx), state.user), stage);
  let s = null;
  let busy = false;
  let timer = null;
  let timedOut = false;

  async function load() {
    try { s = await api(`/api/dark/act/${sid}`); draw(); }
    catch (e) {
      render(stage, notice("error", e.message), h("a", { class: "btn", href: "#/s-mode" }, "Back to S-mode"));
    }
  }

  async function call(path, body) {
    if (busy) return;
    busy = true;
    try {
      s = await api(path, { method: "POST", body });
      timedOut = false;
      draw();
    } catch (e) { toast(e.message); }
    finally { busy = false; }
  }

  function draw() {
    clearInterval(timer);
    timer = null;
    if (!s) return;
    if (s.phase === "lobby") return drawLobby();
    if (s.phase === "finished") return drawReport();
    drawScene();
  }

  function face(c) {
    return c.image_url
      ? h("img", { class: "act-face", src: c.image_url, alt: c.name,
          onerror: (e) => e.target.replaceWith(h("span", { class: "act-face act-face--alt" }, c.name.slice(0, 2).toUpperCase())) })
      : h("span", { class: "act-face act-face--alt" }, c.name.slice(0, 2).toUpperCase());
  }

  function commitments() {
    const list = s.commitments || [];
    if (!list.length) return null;
    return h("div", { class: "panel act-commitments" }, h("h3", {}, "What you've claimed"),
      h("ul", { class: "small" }, ...list.map((e) => h("li", {},
        h("span", {}, e.value),
        h("span", { class: "muted mono small" }, ` · to ${e.to}`),
        !e.settled ? h("span", { class: "muted mono small" }, " · untested") : null,
        e.settled && e.exposed === true ? h("span", { class: "act-flag" }, "called in") : null,
        e.settled && e.exposed === false ? h("span", { class: "cmp-up small" }, " · held up") : null))));
  }

  // ---------------------------------------------------------------- lobby
  function drawLobby() {
    const playBtn = h("button", { class: "btn btn--primary", type: "button",
      onclick: () => call(`/api/dark/act/${sid}/play`, {}) }, "Enter the room");
    render(stage,
      h("div", { class: "row-actions surv-top" },
        h("a", { class: "link-btn", href: "#/s-mode" }, "Leave (progress is saved)")),
      h("div", { class: "panel act-cover" },
        h("p", { class: "muted mono small" }, `Your cover · ${s.difficulty}`),
        h("h2", {}, s.role),
        h("p", { class: "muted" }, s.setting)),
      h("h2", {}, "What you actually know"),
      h("ul", { class: "act-glimpses small" }, ...(s.glimpses || []).map((g) => h("li", {}, g))),
      h("h2", {}, "The room"),
      h("div", { class: "act-cast" }, ...s.cast.map((c) => h("div", { class: "act-person" },
        face(c),
        h("div", {}, h("strong", {}, c.name), h("p", { class: "muted small" }, c.role),
          h("p", { class: "small mono" }, c.hint))))),
      commitments(),
      h("p", { class: "muted small" }, MOVE_INTENT),
      howToPlay("act_deceive"),
      h("div", { class: "row-actions" }, playBtn));
  }

  // ---------------------------------------------------------------- scene
  function drawScene() {
    const doubtPips = Array.from({ length: s.max_doubt }, (_, i) =>
      h("span", { class: "act-doubt" + (i < s.doubt ? " is-on" : "") }));
    const stepBtn = (label, dir, enabled) => {
      const b = h("button", { class: "btn btn--quiet", type: "button", disabled: !enabled }, label);
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { const n = await api(`/api/dark/act/${sid}/${dir}`, { method: "POST" }); ctx.navigate(`#/act/${n.id}`); }
        catch (e) { toast(e.message); b.disabled = false; }
      });
      return b;
    };
    render(stage,
      h("div", { class: "row-actions surv-top" },
        stepBtn("Previous", "prev", s.can_prev),
        h("span", { class: "mono small muted" }, `Scenario ${(s.stack_index ?? 0) + 1} / ${s.pool_size ?? 1}`),
        stepBtn("Next", "next", s.can_next),
        h("a", { class: "link-btn", href: "#/s-mode" }, "Leave (progress is saved)")),
      h("div", { class: "act-hud" },
        h("div", { class: "act-hud__doubt" }, h("span", { class: "mono small muted" }, "Doubt"), ...doubtPips),
        h("span", { class: "mono small muted" }, `Turn ${s.turn + 1} / ${s.turns}`),
        s.timer.enabled ? h("span", { class: "mono small act-clock" }, `${Math.ceil(s.timer.seconds_left || 0)}s`) : null),
      h("div", { class: "act-board" }, ...s.cast.map((c) => h("div", {
        class: "act-person act-person--board" + (c.speaking ? " is-speaking" : "") },
        face(c), h("span", { class: "mono small" }, c.name)))),
      h("div", { class: "surv-chat", "aria-live": "polite" },
        ...(s.transcript || []).map((m) => m.role === "system"
          ? h("div", { class: "bubble bubble--sys" }, m.text)
          : h("div", { class: "bubble bubble--" + (m.role === "me" ? "me" : "them") },
              (m.speaker ? m.speaker + ": " : "") + m.text)),
        h("div", { class: "bubble bubble--them" }, s.node.line)),
      h("div", { class: "surv-options" }, ...s.node.options.map((o) =>
        h("button", { class: "btn surv-option", type: "button", onclick: () => call(`/api/dark/act/${sid}/choose`, { key: o.key }) },
          h("span", { class: "surv-option__text" }, o.text),
          h("span", { class: "surv-tag mono" }, o.tag)))),
      commitments());
    if (s.timer.enabled && typeof s.timer.seconds_left === "number") startTimer(s.timer.seconds_left);
  }

  function startTimer(seconds) {
    let left = seconds;
    timer = setInterval(() => {
      left -= 0.25;
      const el = stage.querySelector(".act-clock");
      if (el) el.textContent = `${Math.max(0, Math.ceil(left))}s`;
      if (left <= 0 && !timedOut) {
        timedOut = true;
        clearInterval(timer);
        call(`/api/dark/act/${sid}/timeout`, {});
      }
    }, 250);
  }

  // ---------------------------------------------------------------- review
  function drawReport() {
    const r = s.report;
    render(stage,
      h("h1", {}, OUTCOME[r.outcome] || "Run over."),
      h("p", { class: "lede" }, `You held "${r.role}" at ${r.setting}.`),
      r.note ? h("div", { class: "notice", "data-kind": "warn" }, r.note) : null,
      h("div", { class: "surv-report" },
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, r.dark_points), h("div", { class: "metric__label" }, "dark points")),
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, `${r.doubt_left}/${r.max_doubt}`), h("div", { class: "metric__label" }, "doubt left")),
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, r.exposed), h("div", { class: "metric__label" }, "called in")),
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, r.wasted), h("div", { class: "metric__label" }, "wasted picks"))),
      h("div", { class: "panel" }, h("h3", {}, "The verdict"),
        h("ul", { class: "act-verdicts" }, ...r.verdicts.map((v) => h("li", {},
          h("strong", {}, v.name), h("span", { class: "act-band act-band--" + v.band }, BAND[v.band] || v.band),
          h("p", { class: "muted small" }, v.line))))),
      h("div", { class: "panel" }, h("h3", {}, "What you claimed"),
        h("ul", { class: "small" }, ...(r.commitments || []).map((e) => h("li", {},
          h("span", {}, e.value), h("span", { class: "muted mono small" }, ` · to ${e.to}`),
          e.exposed === true ? h("span", { class: "cmp-down" }, " · called in") : null,
          e.exposed === false ? h("span", { class: "cmp-up" }, " · held up") : null)))),
      h("div", { class: "reviews" }, h("h2", {}, "Review"),
        ...r.log.map((e) => {
          if (e.kind === "end") {
            return h("details", { class: "review" },
              h("summary", {}, "The room closes"),
              h("p", { class: "muted small" }, e.line || ""));
          }
          if (e.kind === "check") {
            const bad = e.result === "exposed";
            return h("details", { class: "review" + (bad ? " is-wrong" : " is-right") },
              h("summary", {}, `Check — ${e.speaker || "someone"}: "${e.line}"`),
              h("p", {}, e.text || "Nothing came of it."),
              bad ? h("p", { class: "mono small" }, `Called in: ${e.fact || ""}`) : null);
          }
          if (e.stammer) {
            return h("details", { class: "review is-wrong" },
              h("summary", {}, "You hesitated too long"),
              h("p", {}, "A stammer costs 1 doubt. Answer before the timer runs out."));
          }
          if (!e.options) return null;
          const wrong = e.result === "fail" || e.result === "contradiction" || e.result === "debt";
          return h("details", { class: "review" + (wrong ? " is-wrong" : " is-right") },
            h("summary", {}, `${e.speaker}: "${e.line}"`),
            h("p", {}, `You: "${e.chosen}"`),
            h("p", { class: "mono small" }, `${e.tag} · ${e.result} · ${e.delta >= 0 ? "+" : ""}${e.delta} doubt (${e.doubt_after} total)`),
            e.reaction ? h("p", { class: "small" }, `${e.speaker}: "${e.reaction}"`) : null,
            wrong ? h("p", { class: "small" }, `A better read: "${e.better || "stick to what you know."}"`) : null,
            h("ul", { class: "small act-intent" },
              ...e.options.map((o, i) => h("li", { class: i === e.ideal ? "is-ideal" : (o.delta > 0 ? "is-miss" : "") },
                h("strong", {}, `${o.tag}${i === e.ideal ? " · safest" : ""}`),
                h("span", { class: "muted mono small" }, ` · ${o.delta >= 0 ? "+" : ""}${o.delta}`),
                " — ", o.text))));
        })),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn--primary", type: "button", onclick: async () => {
          try { const n = await api(`/api/dark/act/${sid}/restart`, { method: "POST" }); ctx.navigate(`#/act/${n.id}`); }
          catch (e) { toast(e.message); }
        } }, "Play again"),
        h("a", { class: "btn", href: "#/s-mode" }, "Back to S-mode")));
  }

  load();
  return () => { clearInterval(timer); };
}
