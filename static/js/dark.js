import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { howToPlay } from "./guide.js";
import { h, notice, render, toast, topbar } from "./dom.js";

const SECRET_LABEL = { strength: "Strength", weakness: "Weakness", goal: "Goal" };
const OUTCOME_LABEL = { success: "Mission successful.", bored: "They got bored and left.", burned: "You burned every point." };

export function darkView(root, ctx) {
  const sid = ctx.arg;
  const stage = h("section", { class: "stage surv" });
  root.append(topbar("s-mode", () => logout(ctx), state.user), stage);
  let s = null;
  let busy = false;

  async function load() {
    try { s = await api(`/api/dark/${sid}`); draw(); }
    catch (e) {
      render(stage, notice("error", e.message), h("a", { class: "btn", href: "#/s-mode" }, "Back to S-mode"));
    }
  }

  async function choose(key) {
    if (busy) return;
    busy = true;
    try { s = await api(`/api/dark/${sid}/choose`, { method: "POST", body: { key } }); draw(); }
    catch (e) { toast(e.message); }
    finally { busy = false; }
  }

  async function again() {
    try {
      const n = await api(`/api/dark/${sid}/restart`, { method: "POST" });
      ctx.navigate(`#/dark/${n.id}`);
    } catch (e) { toast(e.message); }
  }

  function face() {
    const img = h("img", { class: "surv-face", src: s.image_url, alt: `Portrait of ${s.name}` });
    img.addEventListener("error", () => img.replaceWith(
      h("div", { class: "surv-face surv-face--alt", "aria-hidden": "true" }, s.name.slice(0, 2).toUpperCase())));
    return img;
  }

  function card() {
    return h("div", { class: "surv-card panel" },
      face(),
      h("div", { class: "surv-card__meta" },
        h("h2", {}, s.name),
        h("p", { class: "muted small mono" }, s.tone),
        h("p", {}, s.description),
        h("dl", { class: "surv-secrets" },
          ...Object.keys(SECRET_LABEL).map((k) => [
            h("dt", { class: "mono small" }, SECRET_LABEL[k]),
            h("dd", { class: "small" + (s.found[k] ? " surv-found" : " muted") }, s.found[k] ? s.secrets[k] : "?"),
          ]).flat())));
  }

  function pips() {
    return h("div", { class: "surv-pips" },
      h("div", { class: "surv-pips__row" }, h("span", { class: "mono small muted" }, "Social"),
        ...Array.from({ length: s.max_points }, (_, i) => h("span", { class: "pip" + (i < s.points ? " is-on" : "") }))),
      h("div", { class: "surv-pips__row" }, h("span", { class: "mono small muted" }, "Patience"),
        ...Array.from({ length: s.max_stalls }, (_, i) => h("span", { class: "stall" + (i < s.stalls ? " is-on" : "") }))));
  }

  function transcript() {
    const bubbles = s.messages.map((m) => h("div", { class: "bubble bubble--" + (m.role === "player" ? "me" : "them") }, m.text));
    if (s.node) bubbles.push(h("div", { class: "bubble bubble--them" }, s.node.text));
    return h("div", { class: "surv-chat", "aria-live": "polite" }, ...bubbles);
  }

  function options() {
    return h("div", { class: "surv-options" }, ...s.node.options.map((o) =>
      h("button", { class: "btn surv-option" + (o.locked ? " is-locked" : ""), type: "button",
        disabled: o.locked, onclick: () => choose(o.key) },
        h("span", { class: "surv-option__text" }, o.text),
        h("span", { class: "surv-tag mono" }, o.tag))));
  }

  function draw() {
    if (s.state === "finished") return report();
    const step = (label, dir, enabled) => {
      const b = h("button", { class: "btn btn--quiet", type: "button", disabled: !enabled }, label);
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const n = await api(`/api/dark/${sid}/${dir}`, { method: "POST" });
          ctx.navigate(`#/dark/${n.id}`);
        } catch (e) { toast(e.message); b.disabled = false; }
      });
      return b;
    };
    render(stage,
      h("div", { class: "row-actions surv-top" },
        step("Previous", "prev", s.can_prev),
        h("span", { class: "mono small muted" }, `Character ${(s.stack_index ?? 0) + 1} / ${s.pool_size ?? 1}`),
        step("Next", "next", s.can_next),
        h("a", { class: "link-btn", href: "#/s-mode" }, "Leave (progress is saved)")),
      howToPlay("surveillance"),
      card(), pips(), transcript(), options());
  }

  function report() {
    const r = s.report;
    const found = Object.keys(SECRET_LABEL).filter((k) => r.found[k]);
    const missed = Object.keys(SECRET_LABEL).filter((k) => !r.found[k]);
    render(stage,
      h("h1", {}, OUTCOME_LABEL[r.outcome] || "Run over."),
      h("p", { class: "lede" }, r.outcome === "success"
        ? `You walked out with all three secrets in ${r.turns} turns.`
        : `You left with ${found.length} of 3 secrets after ${r.turns} turns.`),
      h("div", { class: "surv-report" },
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, r.dark_points), h("div", { class: "metric__label" }, "dark points")),
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, `${r.points_left}/${s.max_points}`), h("div", { class: "metric__label" }, "social points left")),
        h("div", { class: "metric" }, h("div", { class: "metric__value" }, r.wasted), h("div", { class: "metric__label" }, "wasted picks"))),
      h("div", { class: "panel" },
        h("h3", {}, r.name),
        h("p", { class: "muted small mono" }, r.tone),
        h("p", {}, r.personality),
        h("p", { class: "mono small" }, "Their dialect: " + (r.tags || []).join(", ")),
        h("p", {}, r.read || "")),
      h("div", { class: "panel" },
        h("h3", {}, "What you found"),
        h("dl", { class: "surv-secrets" }, ...Object.keys(SECRET_LABEL).map((k) => [
          h("dt", { class: "mono small" }, SECRET_LABEL[k]),
          h("dd", { class: "small" + (r.found[k] ? " surv-found" : " muted") },
            r.secrets[k] + (r.found[k] ? "" : " (not uncovered)")),
        ]).flat()),
        missed.length
          ? h("p", { class: "muted small" }, "Missed: " + missed.map((k) => SECRET_LABEL[k].toLowerCase()).join(", "))
          : h("p", { class: "mono small" }, "All three uncovered.")),
      h("div", { class: "panel" },
        h("h3", {}, "Your style"),
        h("dl", { class: "surv-secrets" }, ...Object.entries(r.style).map(([k, n]) => [
          h("dt", { class: "mono small" }, k),
          h("dd", { class: "small" }, `${n} pick${n === 1 ? "" : "s"}`),
        ]).flat())),
      h("div", { class: "reviews" }, h("h2", {}, "Debrief"),
        ...r.log.map((e, i) => h("details", { class: "review" + (e.outcome === "optimal" ? " is-right" : " is-wrong") },
          h("summary", {}, `${i + 1}. "${e.chosen}"`),
          h("p", { class: "mono small" }, `${e.tag || e.approach || "?"} · ${e.outcome}` + (e.reveal ? ` · revealed ${e.reveal}` : "")),
          h("p", {}, e.rationale),
          h("p", { class: "muted small" }, `${r.name}: ${e.text}`)))),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn--primary", type: "button", onclick: again }, "Play again (new face)"),
        h("a", { class: "btn", href: "#/s-mode" }, "Back to S-mode")));
  }

  load();
  return () => {};
}
