import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, topbar } from "./dom.js";

export function leaderboardView(root, ctx) {
  const u = state.user;
  let tab = "daily";
  const body = h("div", { class: "board" });
  const tabs = h("div", { class: "auth-tabs", role: "tablist" });
  root.append(topbar("leaderboard", () => logout(ctx), u),
    h("section", { class: "page-head" }, h("h1", {}, "Leaderboard"),
      h("p", {}, "Points are weighted by difficulty, so Easy runs can't out-score Hard ones. The daily board resets at midnight IST.")),
    h("section", { class: "board-wrap" }, tabs, body));

  async function load() {
    tabs.replaceChildren(...[["daily", "Today"], ["hall", "Hall of fame"]].map(([k, label]) =>
      h("button", { type: "button", role: "tab", "aria-selected": String(tab === k), onclick: () => { tab = k; load(); } }, label)));
    body.replaceChildren(h("p", { class: "muted mono" }, "Loading..."));
    try {
      const d = await api(`/api/leaderboard/${tab}`);
      const me = d.me ? h("p", { class: "mono" }, `Your rank: ${d.me.rank} with ${d.me.points} points.${u.display_on_leaderboard ? "" : " (Hidden from others.)"}`)
        : h("p", { class: "muted mono" }, tab === "daily" ? "You haven't finished today's circuit yet." : "Finish a circuit to enter the hall.");
      if (!d.entries.length) { body.replaceChildren(me, h("p", { class: "muted" }, "Nobody's on the board yet. Be the first.")); return; }
      const cols = tab === "daily" ? ["Rank", "Alias", "Points"] : ["Rank", "Alias", "Points", "Streak", "Circuits"];
      body.replaceChildren(me, h("div", { class: "table-wrap" }, h("table", { class: "report" },
        h("thead", {}, h("tr", {}, ...cols.map((c) => h("th", {}, c)))),
        h("tbody", {}, ...d.entries.map((e) => h("tr", { class: e.alias === u.alias ? "is-me" : null },
          h("td", { class: "mono" }, e.rank), h("th", { scope: "row" }, e.alias), h("td", { class: "mono" }, e.points),
          ...(tab === "hall" ? [h("td", { class: "mono" }, e.streak), h("td", { class: "mono" }, e.sessions)] : [])))))));
    } catch (err) { body.replaceChildren(notice("error", err.message)); }
  }
  load();
}
