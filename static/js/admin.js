import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, render, toast, topbar } from "./dom.js";

export function adminView(root, ctx) {
  const msg = h("div", {});
  const body = h("div", {});
  const email = h("input", { class: "input", id: "inv", type: "email", required: true, maxlength: "254", autocomplete: "off" });
  const genMsg = h("div", {});
  const pipeline = h("div", { class: "panel" });
  const genBtn = (pillar, label) => h("button", { class: "btn", type: "button", onclick: async () => {
    try {
      const r = await api("/api/admin/generate", { method: "POST", body: { pillar, force: true } });
      toast(`Queued: ${r.queued.join(", ")}`);
    } catch (e) { genMsg.replaceChildren(notice("error", e.message)); }
  } }, label);
  const facesBody = h("div", {});
  const faceLabel = h("input", { class: "input", id: "flabel", placeholder: "student_3", maxlength: "32", autocomplete: "off" });
  const faceFile = h("select", { class: "input", id: "ffile" });
  const facesMsg = h("div", {});
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
      pipeline,
      h("div", { class: "panel" },
        h("h3", {}, "Generate content"),
        h("p", { class: "muted small" }, "Regenerates today's pools with Gemini. Each pillar has its own lock and fallback."),
        genMsg,
        h("div", { class: "row-actions" },
          genBtn("all", "All pillars"), genBtn("critical", "Critical thinking"),
          genBtn("art", "Art"), genBtn("surveillance", "Surveillance"))),
      h("div", { class: "panel" },
        h("h3", {}, "S-mode faces"),
        h("p", { class: "muted small" }, "Register portraits from static/img/faces. The generator only picks registered labels."),
        facesMsg, facesBody,
        h("form", { class: "row-actions", onsubmit: async (e) => {
          e.preventDefault(); facesMsg.replaceChildren();
          try {
            await api("/api/admin/faces", { method: "POST", body: { label: faceLabel.value, filename: faceFile.value } });
            faceLabel.value = ""; toast("Face added"); loadFaces();
          } catch (err) { facesMsg.replaceChildren(notice("error", err.message)); }
        } },
          faceLabel,
          faceFile,
          h("button", { class: "btn", type: "submit" }, "Register face"))),
      body));

  async function loadFaces() {
    try {
      const d = await api("/api/admin/faces");
      faceFile.replaceChildren(...(d.files.length
        ? d.files.map((f) => h("option", { value: f }, f))
        : [h("option", { value: "" }, "No files in static/img/faces")]));
      facesBody.replaceChildren(d.faces.length
        ? h("div", { class: "table-wrap" }, h("table", { class: "report" },
          h("thead", {}, h("tr", {}, ...["Label", "File", ""].map((c) => h("th", {}, c)))),
          h("tbody", {}, ...d.faces.map((f) => h("tr", {},
            h("th", { scope: "row", class: "mono small" }, f.label),
            h("td", { class: "mono small" }, f.filename),
            h("td", {}, h("div", { class: "row-actions" },
              h("button", { class: "btn btn--quiet", type: "button", onclick: async () => {
                const next = prompt("New label", f.label);
                if (!next || next === f.label) return;
                try { await api(`/api/admin/faces/${encodeURIComponent(f.label)}`, { method: "PATCH", body: { label: next } }); loadFaces(); }
                catch (e) { facesMsg.replaceChildren(notice("error", e.message)); }
              } }, "Rename"),
              h("button", { class: "btn btn--quiet", type: "button", onclick: async () => {
                try { await api(`/api/admin/faces/${encodeURIComponent(f.label)}`, { method: "DELETE" }); loadFaces(); }
                catch (e) { facesMsg.replaceChildren(notice("error", e.message)); }
              } }, "Delete"))))))))
        : h("p", { class: "muted" }, "No faces registered yet. Add image files to static/img/faces and register them above."));
    } catch (e) { facesBody.replaceChildren(notice("error", e.message)); }
  }

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
      const srcText = (s) => {
        if (!s) return "-";
        if (typeof s === "string") return s;
        return [...new Set(Object.values(s))].join(" / ");
      };
      const chars = d.content?.surveillance?.characters || [];
      render(pipeline,
        h("h3", {}, "Content pipeline"),
        h("p", { class: "muted small" }, `Pools for ${d.date}. Gemini is used first; fallbacks keep the app playable.`),
        h("div", { class: "table-wrap" }, h("table", { class: "report" },
          h("thead", {}, h("tr", {}, ...["Pillar", "Source", "Ready", "Attempts", "Last error"].map((c) => h("th", {}, c)))),
          h("tbody", {}, ...Object.entries(d.content || {}).map(([k, v]) => h("tr", {},
            h("th", { scope: "row" }, k),
            h("td", { class: "mono small" }, srcText(v.source)),
            h("td", { class: "mono" }, v.ready ? "yes" : "no"),
            h("td", { class: "mono" }, v.attempts ?? 0),
            h("td", { class: "mono small" }, v.error ? String(v.error).slice(0, 70) : "-")))))),
        chars.length
          ? h("div", {}, h("p", { class: "muted small" }, "S-mode characters in today's pool:"),
              h("ul", { class: "small" }, ...chars.map((c) => h("li", {}, `${c.difficulty}: ${c.name} (${c.label})`))))
          : h("p", { class: "muted small" }, "No S-mode characters yet. Use Generate -> Surveillance, or start a run to kick it."));
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
  loadFaces();
}
