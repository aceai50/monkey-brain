import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { codexCard, DARK, GYM } from "./guide.js";
import { h, notice, render, topbar } from "./dom.js";

export function codexView(root, ctx) {
  const darkZone = h("div", { class: "codex-zone" });
  root.append(
    topbar("codex", () => logout(ctx), state.user),
    h("section", { class: "page-head" },
      h("h1", {}, "The Codex"),
      h("p", {}, "Every game: what it is, how to play, and how to win.")),
    h("section", { class: "codex-band" },
      h("h2", {}, "The Gym"),
      h("p", { class: "muted small" }, "Five pillars. Points, streaks and the leaderboard live here."),
      h("div", { class: "codex-grid" }, ...GYM.map(codexCard))),
    h("div", { class: "codex-cut", "aria-hidden": "true" }),
    darkZone);

  const inSMode = () => {
    if (document.documentElement.hasAttribute("data-s-mode")) return true;
    try { return localStorage.getItem("bm_s_mode") === "1"; } catch { return false; }
  };

  api("/api/auth/config", { auth: false }).then((cfg) => {
    if (cfg.s_mode_enabled && inSMode()) {
      render(darkZone,
        h("section", { class: "codex-band theme-s" },
          h("h2", {}, "S-mode"),
          h("p", { class: "muted small" }, "The darker wing. Its scores are dark points — never your streak or leaderboard."),
          h("div", { class: "codex-grid" }, ...DARK.map(codexCard))));
    } else {
      render(darkZone,
        h("section", { class: "panel codex-tease" },
          h("h3", {}, "There's another wing."),
          h("p", { class: "muted" }, cfg.s_mode_enabled
            ? "Flip the S-mode switch at the bottom of your dashboard, and the darker games appear here."
            : "Somewhere at the bottom of your dashboard a switch waits. Find it, and the darker games open.")));
    }
  }).catch((e) => render(darkZone, notice("error", e.message)));
}
