import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, render, toast, topbar } from "./dom.js";

const JOB_PILLAR = { critical: "Critical thinking", art: "Art", surveillance: "Surveillance",
  act_deceive: "Act & Deceive" };
const JOB_ETA = { critical: 300, art: 300, surveillance: 240, act_deceive: 600 };
const fmtDur = (s) => {
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), r = s % 60;
  return m ? `${m} min${r ? ` ${r}s` : ""}` : `${r}s`;
};

export function adminView(root, ctx) {
  const msg = h("div", {});
  const body = h("div", {});
  const email = h("input", { class: "input", id: "inv", type: "email", required: true, maxlength: "254", autocomplete: "off" });
  const genMsg = h("div", {});
  const pipeline = h("div", { class: "panel" });
  const jobsBox = h("div", { class: "panel" });
  const logsBox = h("div", { class: "panel" });
  let logsState = [];
  let logsPage = 1;
  let logsArchive = false;
  let logsMeta = { pages: 1, total: 0, archived: 0 };
  let jobsState = [];
  let jobsPage = 1;
  let jobsMeta = { pages: 1, total: 0 };
  const jobClock = new Map();
  const openJobs = new Set();
  let jobsPoll = null;
  let jobsTick = null;

  function jobElapsed(j) {
    if (j.status !== "processing") return j.elapsed;
    const c = jobClock.get(j.id);
    return c ? c.base + (performance.now() - c.at) / 1000 : j.elapsed;
  }

  function renderJobs() {
    if (!jobsState.length) {
      render(jobsBox, h("h3", {}, "Generation jobs"),
        h("p", { class: "muted small" }, "No jobs yet. Trigger a generation above."));
      return;
    }
    const rows = jobsState.map((j) => {
      const eta = j.eta || JOB_ETA[j.pillar] || 120;
      const elapsed = jobElapsed(j);
      const label = j.status === "processing" ? `processing · ${Math.round(elapsed)}s / ~${fmtDur(eta)}`
        : j.status === "completed" ? `completed · 200 (${fmtDur(j.elapsed)})` : "error";
      const det = h("details", { class: "job" },
        h("summary", {},
          h("span", { class: `job-dot job-dot--${j.status}` }),
          h("span", { class: "mono small" }, JOB_PILLAR[j.pillar] || j.pillar),
          h("span", { class: "muted small job-secs", "data-id": j.id }, label)),
        h("p", { class: "mono small" }, j.status === "error" ? (j.error || "unknown error")
          : j.status === "completed" ? (j.message || "done")
          : `Running… estimated ${fmtDur(eta)}, about ${fmtDur(Math.max(0, eta - elapsed))} left`),
        h("p", { class: "muted small mono" },
          `started ${new Date(j.started_at).toLocaleTimeString()}` +
          (j.finished_at ? ` · finished ${new Date(j.finished_at).toLocaleTimeString()} · ${j.elapsed.toFixed(1)}s` : "")));
      det.open = openJobs.has(j.id);
      det.addEventListener("toggle", () => {
        if (det.open) openJobs.add(j.id); else openJobs.delete(j.id);
      });
      return det;
    });
    render(jobsBox,
      h("h3", {}, "Generation jobs"),
      h("p", { class: "muted small" }, "Live status, 5 per page. Yellow = running, green = completed, red = error. Click a row for the log."),
      h("div", { class: "jobs" }, ...rows),
      h("div", { class: "row-actions jobs-pager" },
        h("button", { class: "btn btn--quiet", type: "button", disabled: jobsPage <= 1,
          onclick: () => loadJobs(jobsPage - 1) }, "Newer"),
        h("span", { class: "mono small muted" },
          `Page ${jobsPage} of ${jobsMeta.pages} · ${jobsMeta.total} job${jobsMeta.total === 1 ? "" : "s"}`),
        h("button", { class: "btn btn--quiet", type: "button", disabled: jobsPage >= jobsMeta.pages,
          onclick: () => loadJobs(jobsPage + 1) }, "Older")));
    if (jobsState.some((j) => j.status === "processing") && !jobsTick) {
      jobsTick = setInterval(() => {
        for (const el of jobsBox.querySelectorAll(".job-secs")) {
          const j = jobsState.find((x) => x.id === el.dataset.id);
          if (j && j.status === "processing") {
            const eta = j.eta || JOB_ETA[j.pillar] || 120;
            el.textContent = `processing · ${Math.max(0, Math.round(jobElapsed(j)))}s / ~${fmtDur(eta)}`;
          }
        }
      }, 1000);
    } else if (!jobsState.some((j) => j.status === "processing") && jobsTick) {
      clearInterval(jobsTick); jobsTick = null;
    }
  }

  async function loadJobs(page = jobsPage) {    try {
      jobsPage = Math.max(1, page);
      const d = await api(`/api/admin/jobs?page=${jobsPage}&page_size=5`);
      jobsState = d.jobs || [];
      jobsMeta = { pages: d.pages || 1, total: d.total || 0 };
      if (jobsPage > jobsMeta.pages) return loadJobs(jobsMeta.pages);
      for (const j of jobsState) jobClock.set(j.id, { base: j.elapsed, at: performance.now() });
      renderJobs();
      const processing = jobsState.some((j) => j.status === "processing");
      if (processing && !jobsPoll) jobsPoll = setInterval(() => loadJobs(jobsPage), 2000);
      else if (!processing && jobsPoll) { clearInterval(jobsPoll); jobsPoll = null; }
    } catch { /* status polling is best-effort */ }
  }

  function renderLogs() {
    const rows = logsState.map((e) => h("tr", {},
      h("td", { class: "mono small" }, `${e.date} · ${new Date(e.started_at).toLocaleTimeString()}`),
      h("td", {}, e.pillar_label || e.pillar),
      h("td", { class: "mono small" }, e.trigger === "cron" ? "auto (cron)" : e.trigger),
      h("td", { class: "mono small" }, e.provider || "-"),
      h("td", { class: "mono" }, e.items == null ? "—" : String(e.items)),
      h("td", { class: "mono small" }, fmtDur(e.elapsed)),
      h("td", {},
        h("span", { class: `job-dot job-dot--${e.status}` }),
        h("span", { class: "small", title: e.message || e.error || "" },
          e.status === "error" ? "error" : e.status === "processing" ? "processing" : "completed"))));
    render(logsBox,
      h("h3", {}, "Generation log"),
      h("p", { class: "muted small" }, logsArchive
        ? "Showing all runs, newest first."
        : "Automatic and admin runs from the last 3 days. Older runs are archived."),
      rows.length
        ? h("div", { class: "table-wrap" }, h("table", { class: "report" },
            h("thead", {}, h("tr", {}, ...["When", "Pillar", "Trigger", "Provider · model", "Items", "Duration", "Status"].map((c) => h("th", {}, c)))),
            h("tbody", {}, ...rows)))
        : h("p", { class: "muted" }, "No runs logged yet."),
      h("div", { class: "row-actions jobs-pager" },
        h("button", { class: "btn btn--quiet", type: "button", disabled: logsPage <= 1,
          onclick: () => loadLogs(logsPage - 1) }, "Newer"),
        h("span", { class: "mono small muted" }, `Page ${logsPage} of ${logsMeta.pages} · ${logsMeta.total} runs`),
        h("button", { class: "btn btn--quiet", type: "button", disabled: logsPage >= logsMeta.pages,
          onclick: () => loadLogs(logsPage + 1) }, "Older"),
        h("button", { class: "btn btn--quiet", type: "button", onclick: () => {
          logsArchive = !logsArchive;
          loadLogs(1);
        } }, logsArchive ? "Show last 3 days" : `Show archive${logsMeta.archived ? ` (${logsMeta.archived})` : ""}`)));
  }

  async function loadLogs(page = logsPage) {
    try {
      logsPage = Math.max(1, page);
      const d = await api(`/api/admin/logs?page=${logsPage}&page_size=10&include_archived=${logsArchive}`);
      logsState = d.entries || [];
      logsMeta = { pages: d.pages || 1, total: d.total || 0, archived: d.archived || 0 };
      if (logsPage > logsMeta.pages) return loadLogs(logsMeta.pages);
      renderLogs();
    } catch { /* best effort */ }
  }

  async function runGenerate(body) {
    try {
      const r = await api("/api/admin/generate", { method: "POST", body });
      const parts = (r.jobs || []).map((j) => `${JOB_PILLAR[j.pillar] || j.pillar} ~${fmtDur(j.eta)}`);
      toast(`Queued — ${parts.join(" · ") || r.queued.join(", ")}`);
      jobsPage = 1;
      logsPage = 1;
      await loadJobs(1);
      await loadLogs(1);
    } catch (e) { render(genMsg, notice("error", e.message)); }
  }
  const genBtn = (pillar, label, eta) => {
    const secs = eta ?? JOB_ETA[pillar] ?? 120;
    return h("button", { class: "btn", type: "button",
      title: `Estimated ${fmtDur(secs)} (runs in the background, paced)`,
      onclick: () => runGenerate({ pillar, force: true }) }, `${label} · ~${fmtDur(secs)}`);
  };
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
        h("p", { class: "muted small" }, "Regenerates today's pools with Gemini, falling back to Groq. Requests are paced (one at a time) to respect free-tier limits, so runs take a few minutes. Surveillance creates one character per level by default; use +3 to add more to the pool."),
        genMsg,
        h("div", { class: "row-actions" },
          genBtn("all", "All pillars", 1440), genBtn("critical", "Critical thinking"),
          genBtn("art", "Art"), genBtn("surveillance", "Surveillance"),
          genBtn("act_deceive", "Act & Deceive"),
          h("button", { class: "btn", type: "button",
            title: `Estimated ${fmtDur(JOB_ETA.surveillance * 3)}`,
            onclick: () => runGenerate({ pillar: "surveillance", count: 3, append: true, force: false }) },
            `Surveillance +3 · ~${fmtDur(JOB_ETA.surveillance * 3)}`))),
      jobsBox,
      logsBox,
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
  loadJobs(1);
  loadLogs(1);
  return () => { clearInterval(jobsPoll); clearInterval(jobsTick); };
}
