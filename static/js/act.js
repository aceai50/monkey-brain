import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, render, toast, topbar } from "./dom.js";

const OUTCOME = { convinced: "Mission successful.", partial: "Half the room bought it.", busted: "Busted." };
const BAND = { bought_it: "Bought it", suspicious: "Suspicious", sees_through: "Saw through you" };
const MOVE_INTENT = "Every answer is a move: a fact you assert, a smooth non-answer, a bold claim, or turning it back on them. Some land, some don't — read the person, not the label.";

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
    if (s.phase === "prep") return drawPrep();
    if (s.phase === "finished") return drawReport();
    drawScene();
  }

  // ---------------------------------------------------------------- prep
  function drawPrep() {
    const chosen = new Set();
    const startBtn = h("button", { class: "btn btn--primary", type: "button", disabled: true,
      onclick: () => call(`/api/dark/act/${sid}/prep`, { topic_ids: [...chosen] }) }, "Enter the room");
    const refresh = () => {
      startBtn.disabled = chosen.size !== s.pick;
      startBtn.textContent = chosen.size === s.pick ? "Enter the room" : `Pick ${s.pick - chosen.size} more`;
    };
    const cards = s.topics.map((t) => {
      const box = h("input", { type: "checkbox", value: t.id, onchange: (e) => {
        if (e.target.checked) {
          if (chosen.size >= s.pick) { e.target.checked = false; return; }
          chosen.add(t.id);
        } else chosen.delete(t.id);
        refresh();
      } });
      return h("label", { class: "act-topic" }, box,
        h("span", { class: "act-topic__name" }, t.name),
        h("span", { class: "act-topic__card muted small" }, t.study_card));
    });
    render(stage,
      h("div", { class: "row-actions surv-top" },
        h("a", { class: "link-btn", href: "#/s-mode" }, "Leave (progress is saved)")),
      h("div", { class: "panel act-cover" },
        h("p", { class: "muted mono small" }, `Your cover · ${s.difficulty}`),
        h("h2", {}, s.cover.role),
        h("p", { class: "muted" }, s.cover.setting),
        h("p", {}, s.cover.brief)),
      h("h2", {}, "The room"),
      h("div", { class: "act-cast" }, ...s.cast.map((c) => h("div", { class: "act-person" },
        c.image_url ? h("img", { class: "act-face", src: c.image_url, alt: c.name,
          onerror: (e) => e.target.replaceWith(h("span", { class: "act-face act-face--alt" }, c.name.slice(0, 2).toUpperCase())) })
          : h("span", { class: "act-face act-face--alt" }, c.name.slice(0, 2).toUpperCase()),
        h("div", {}, h("strong", {}, c.name), h("p", { class: "muted small" }, c.role),
          h("p", { class: "small mono" }, c.hint))))),
      h("p", { class: "muted small mono" }, "Moves in this room: " + (s.tags || []).join(" · ")),
      h("p", { class: "muted small" }, MOVE_INTENT),
      h("h2", {}, `Cram — study ${s.pick} of ${s.topics.length}`),
      h("div", { class: "act-topics" }, ...cards),
      h("div", { class: "row-actions" }, startBtn));
    refresh();
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
        c.image_url ? h("img", { class: "act-face", src: c.image_url, alt: c.name,
          onerror: (e) => e.target.replaceWith(h("span", { class: "act-face act-face--alt" }, c.name.slice(0, 2).toUpperCase())) })
          : h("span", { class: "act-face act-face--alt" }, c.name.slice(0, 2).toUpperCase()),
        h("span", { class: "mono small" }, c.name)))),
      h("div", { class: "surv-chat", "aria-live": "polite" },
        ...(s.transcript || []).map((m) => m.role === "system"
          ? h("div", { class: "bubble bubble--sys" }, m.text)
          : h("div", { class: "bubble bubble--" + (m.role === "me" ? "me" : "them") },
              (m.speaker ? m.speaker + ": " : "") + m.text)),
        h("div", { class: "bubble bubble--them" }, s.node.line)),
      h("div", { class: "surv-options" }, ...s.node.options.map((o) =>
        h("button", { class: "btn surv-option", type: "button", onclick: () => call(`/api/dark/act/${sid}/choose`, { key: o.key }) },
          h("span", { class: "surv-option__text" }, o.text),
          h("span", { class: "surv-tag mono" }, o.tag),
          o.topic_chip ? h("span", { class: "surv-tag mono" }, o.topic_chip) : null))),
      s.ledger.length ? h("div", { class: "panel act-ledger" }, h("h3", {}, "What you've claimed"),
        h("ul", { class: "small" }, ...s.ledger.map((e) => h("li", {},
          h("span", {}, e.text),
          h("span", { class: "muted mono small" }, ` · to ${e.to}`),
          e.improvised === true ? h("span", { class: "act-flag" }, "improvised") : null,
          e.improvised === false ? h("span", { class: "muted mono small" }, " · backed") : null)))) : null);
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
      h("p", { class: "lede" }, r.cover.brief),
      h("div", { class: "surv-report" },
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, r.dark_points), h("div", { class: "metric__label" }, "dark points")),
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, `${r.doubt_left}/${r.max_doubt}`), h("div", { class: "metric__label" }, "doubt left")),
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, r.wasted), h("div", { class: "metric__label" }, "wasted picks"))),
      h("div", { class: "panel" }, h("h3", {}, "The verdict"),
        h("ul", { class: "act-verdicts" }, ...r.verdicts.map((v) => h("li", {},
          h("strong", {}, v.name), h("span", { class: "act-band act-band--" + v.band }, BAND[v.band] || v.band),
          h("p", { class: "muted small" }, v.line))))),
      h("div", { class: "panel" }, h("h3", {}, "What you claimed"),
        h("ul", { class: "small" }, ...r.ledger.map((e) => h("li", {},
          h("span", {}, e.text), h("span", { class: "muted mono small" }, ` · to ${e.to}`),
          e.improvised ? h("span", { class: "act-flag" }, "improvised") : h("span", { class: "muted mono small" }, " · backed"),
          e.survived === true ? h("span", { class: "cmp-up" }, " · held up") : null,
          e.survived === false ? h("span", { class: "cmp-down" }, " · caught") : null)))),
      h("div", { class: "reviews" }, h("h2", {}, "Review"),
        ...r.log.map((e) => {
          if (e.check) {
            return h("details", { class: "review" + (e.caught && e.caught.length ? " is-wrong" : " is-right") },
              h("summary", {}, `Check — ${e.speaker || "someone"}: "${e.line}"`),
              h("p", {}, e.text || "Nothing came of it."),
              e.caught && e.caught.length ? h("p", { class: "mono small" }, `Caught: ${e.caught.join(", ")}`) : null);
          }
          if (e.stammer) {
            return h("details", { class: "review is-wrong" },
              h("summary", {}, "You hesitated too long"),
              h("p", {}, "A stammer costs 1 doubt. Answer before the timer runs out."));
          }
          return h("details", { class: "review" + (e.ok ? " is-right" : " is-wrong") },
            h("summary", {}, `${e.speaker}: "${e.line}"`),
            h("p", {}, `You: "${e.chosen}"`),
            h("p", { class: "mono small" }, `${e.tag} · ${e.delta >= 0 ? "+" : ""}${e.delta} doubt (${e.doubt_after} total)`),
            e.reaction ? h("p", { class: "small" }, `${e.speaker}: "${e.reaction}"`) : null,
            e.ok ? null : h("p", { class: "small" }, `A better read: "${e.better || "pick a different approach."}"`),
            (e.options && e.options.length) ? h("ul", { class: "small act-intent" },
              ...e.options.map((o, i) => h("li", { class: i === e.ideal ? "is-ideal" : (o.works ? "" : "is-miss") },
                h("strong", {}, `${o.tag}${i === e.ideal ? " · ideal" : o.works ? " · would work" : ""}`),
                " — ", o.rationale))) : null);
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
