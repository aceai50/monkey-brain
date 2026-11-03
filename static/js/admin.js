import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, toast, topbar } from "./dom.js";

export function adminView(root, ctx) {
  const msg = h("div", {});
  const body = h("div", {});
  const email = h("input", { class: "input", id: "inv", type: "email", required: true, maxlength: "254", autocomplete: "off" });
  root.append(topbar("admin", () => logout(ctx), state.user),
    h("section", { class: "page-head" }, h("h1", {}, "Admin"), h("p", {}, "Add, view and remove beta users. Only you can see this page.")),
    h("section", { class: "admin" }, msg,
      h("form", { class: "panel", onsubmit: async (e) => {
        e.preventDefault(); msg.replaceChildren();
        try { await api("/api/admin/invites", { method: "POST", body: { email: email.value } }); toast("Invite added"); email.value = ""; load(); }
        catch (err) { msg.replaceChildren(notice("error", err.message)); }
      } },
        h("h3", {}, "Invite someone"),
        h("p", { class: "muted small" }, "An invite holds one seat and lets that email sign up even in invite-only mode."),
        h("div", { class: "field" }, h("label", { for: "inv" }, "Email"), email),
        h("button", { class: "btn btn--primary", type: "submit" }, "Add invite")),
      body));

  const confirmBtn = (label, doIt) => {
    const b = h("button", { class: "btn btn--quiet", type: "button" }, label);
    let armed = false;
    b.addEventListener("click", async () => {
      if (!armed) { armed = true; b.textContent = "Sure? Click again"; setTimeout(() => { armed = false; b.textContent = label; }, 3000); return; }
      b.disabled = true;
      try { await doIt(); load(); } catch (err) { msg.replaceChildren(notice("error", err.message)); b.disabled = false; }
    });
    return b;
  };

  async function load() {
    try {
      const d = await api("/api/admin/overview");
      const pct = Math.min(100, (d.seats.used / d.seats.max) * 100);
      const seatBar = h("div", { class: "seatbar", role: "img", "aria-label": `${d.seats.used} of ${d.seats.max} seats used` }, h("span", { class: "seatbar__fill" }));
      seatBar.firstChild.style.setProperty("--w", pct + "%");
      body.replaceChildren(
        h("div", { class: "panel" }, h("h3", {}, `${d.seats.used} of ${d.seats.max} seats used`), seatBar,
          h("p", { class: "muted small mono" }, `Registration mode: ${d.seats.mode}. Admin accounts don't use a seat.`)),
        h("h2", {}, "Users"),
        h("div", { class: "table-wrap" }, h("table", { class: "report" },
          h("thead", {}, h("tr", {}, ...["Alias", "Email", "Status", "Circuits", "Points", "Last trained", ""].map((c) => h("th", {}, c)))),
          h("tbody", {}, ...d.users.map((u) => h("tr", {},
            h("th", { scope: "row" }, u.alias || "(no profile)"), h("td", { class: "mono small" }, u.email),
            h("td", { class: "mono small" }, [u.is_admin ? "admin" : null, u.verified ? "verified" : "unconfirmed", u.has_google ? "google" : null].filter(Boolean).join(", ")),
            h("td", { class: "mono" }, u.sessions), h("td", { class: "mono" }, u.total_points), h("td", { class: "mono small" }, u.last_trained || "-"),
            h("td", {}, u.is_admin ? null : confirmBtn("Remove", () => api(`/api/admin/users/${u.id}`, { method: "DELETE"})))))))),
        h("h2", {}, "Pending invites"),
        d.invites.length ? h("div", { class: "table-wrap" }, h("table", { class: "report" },
          h("tbody", {}, ...d.invites.map((i) => h("tr", {}, h("th", { scope: "row", class: "mono small" }, i.email), h("td", { class: "mono small" }, (i.created_at || "").slice(0, 10)),
            h("td", {}, confirmBtn("Revoke", () => api(`/api/admin/invites/${encodeURIComponent(i.email)}`, { method: "DELETE" }))))))))
          : h("p", { class: "muted" }, "No pending invites."));
    } catch (err) { body.replaceChildren(notice("error", err.message)); }
  }
  load();
}
