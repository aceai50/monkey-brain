import { api, apiBlob } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, render, toast, topbar } from "./dom.js";

const PILLAR_LABEL = {
  arithmetic: "Arithmetic", critical: "Critical thinking", pattern: "Pattern recognition",
  art: "Art and perspective", kinesthetic: "Reflexes", surveillance: "Surveillance",
  act_deceive: "Act & Deceive",
};
const PILLAR_ORDER = ["arithmetic", "critical", "pattern", "art", "kinesthetic", "surveillance", "act_deceive"];
const OUTCOME_LABEL = { success: "mission successful", bored: "they got bored", burned: "burned out",
  convinced: "convinced the room", partial: "partly convincing", busted: "busted" };

function segmented(name, options, value, onChange) {
  const wrap = h("div", { class: "segmented", role: "radiogroup", "aria-label": name });
  const draw = () => wrap.replaceChildren(...options.map(([v, label]) =>
    h("button", { type: "button", role: "radio", "aria-checked": String(v === value),
      onclick: () => { value = v; draw(); onChange(v); } }, label)));
  draw();
  return wrap;
}

function dayOffset(date, back) {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d) - back * 86400000).toISOString().slice(0, 10);
}

export function historyView(root, ctx) {
  let entries = [];
  let nextBefore = null;
  let archivedDays = 0;
  let archiveLoaded = false;
  let tab = "days";
  let metric = "score";
  let mode = "circuit";
  let busy = true;
  const chosen = new Set();

  const tabs = h("div", { class: "auth-tabs", role: "tablist" });
  const sectionBar = h("div", { class: "hist-section" });
  const body = h("div", { class: "hist-body" });
  root.append(topbar("history", () => logout(ctx), state.user),
    h("section", { class: "page-head" }, h("h1", {}, "History"),
      h("p", {}, "Every circuit you finished, day by day. The last 30 days load automatically; anything older stays archived until you ask for it.")),
    h("section", { class: "board-wrap" }, sectionBar, tabs, body));

  function drawSectionBar() {
    sectionBar.replaceChildren(segmented("Section", [["circuit", "Normal"], ["dark", "S-mode"]], mode, (v) => {
      mode = v;
      entries = [];
      nextBefore = null;
      archivedDays = 0;
      archiveLoaded = false;
      chosen.clear();
      drawSectionBar();
      loadMore();
    }));
  }

  function drawTabs() {
    tabs.replaceChildren(...[["days", "Previous scores"], ["compare", "Compare"]].map(([k, label]) =>
      h("button", { type: "button", role: "tab", "aria-selected": String(tab === k),
        onclick: () => { tab = k; drawTabs(); draw(); } }, label)));
  }

  function pickDefaults() {
    chosen.clear();
    if (!entries.length) return;
    chosen.add(entries[0].date);
    for (const back of [7, 30]) {
      const target = dayOffset(entries[0].date, back);
      const cand = entries.filter((e) => e.date <= target).at(-1);
      if (cand && !chosen.has(cand.date)) chosen.add(cand.date);
    }
  }

  async function loadMore(before) {
    busy = true;
    draw();
    const qs = new URLSearchParams({ days: "30", limit: "10", mode });
    if (archiveLoaded) qs.set("include_archived", "true");
    if (before) qs.set("before", before);
    try {
      const d = await api(`/api/sessions/history?${qs}`);
      entries = entries.concat(d.entries);
      nextBefore = d.next_before;
      archivedDays = d.archived_days;
      if (!before) pickDefaults();
    } catch (e) {
      render(body, notice("error", e.message));
      busy = false;
      return;
    }
    busy = false;
    draw();
  }

  function dayCard(e) {
    const head = ["Pillar", "Level", "Solved", "Accuracy", "Score", "Points"];
    const rows = e.pillars.map((p) => h("tr", {},
      h("th", { scope: "row" }, PILLAR_LABEL[p.pillar] || p.pillar),
      h("td", { class: "mono" }, p.level ?? "-"),
      h("td", { class: "mono" }, `${p.correct ?? 0} of ${p.offered ?? 0}`),
      h("td", { class: "mono" }, `${Math.round((p.accuracy || 0) * 100)}%`),
      h("td", { class: "mono" }, Math.round(p.score ?? 0)),
      h("td", { class: "mono" }, p.points ?? "-")));
    const reviews = e.pillars.find((p) => p.pillar === "critical")?.review || [];
    return h("details", { class: "panel hist-day" },
      h("summary", {}, h("span", { class: "mono" }, e.date),
        h("span", { class: "hist-day__pts mono" }, `${e.total_points ?? 0} pts`),
        h("span", { class: "muted small mono" }, e.difficulty || ""),
        e.outcome ? h("span", { class: "muted small mono" }, OUTCOME_LABEL[e.outcome] || e.outcome) : null),
      h("div", { class: "table-wrap" }, h("table", { class: "report" },
        h("thead", {}, h("tr", {}, ...head.map((c) => h("th", {}, c)))), h("tbody", {}, ...rows))),
      reviews.length ? h("div", { class: "reviews" }, h("h3", {}, "Critical thinking review"),
        ...reviews.map((q) => h("details", { class: "review" + (q.correct ? " is-right" : " is-wrong") },
          h("summary", {}, (q.correct ? "Right: " : "Missed: ") + (q.prompt || "")),
          h("p", {}, q.scenario || ""),
          h("p", { class: "mono small" }, `Your answer: ${q.your ?? "none"}`),
          h("p", { class: "mono small" }, `Best answer: ${q.right || ""}`),
          h("p", {}, q.explanation || "")))) : null);
  }

  async function download(fmt) {
    try {
      const blob = await apiBlob(`/api/sessions/history/download?format=${fmt}&days=${archiveLoaded ? "all" : "30"}&mode=${mode}`);
      const url = URL.createObjectURL(blob);
      const a = h("a", { href: url, download: `monkey-brain-history.${fmt}` });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (e) { toast(e.message); }
  }

  function drawDays() {
    render(body,
      h("div", { class: "row-actions hist-tools" },
        h("button", { class: "btn", type: "button", onclick: () => download("csv") }, "Download CSV"),
        h("button", { class: "btn", type: "button", onclick: () => download("json") }, "Download JSON")),
      ...entries.map(dayCard),
      !entries.length ? h("p", { class: "muted" }, "No finished circuits yet.") : null,
      nextBefore ? h("button", { class: "btn", type: "button", onclick: () => loadMore(nextBefore) }, "Load older") : null,
      !nextBefore && archivedDays > 0 && !archiveLoaded
        ? h("button", { class: "btn", type: "button", onclick: () => {
            archiveLoaded = true;
            loadMore(entries.length ? entries[entries.length - 1].date : null);
          } }, `Load archive (${archivedDays} older circuit${archivedDays === 1 ? "" : "s"})`) : null);
  }

  function cellEl(v, prev) {
    if (v == null) return h("td", { class: "mono" }, "-");
    const delta = prev == null ? null : v - prev;
    return h("td", { class: "mono" }, String(v),
      delta === null || delta === 0 ? null
        : h("span", { class: "cmp-delta " + (delta > 0 ? "cmp-up" : "cmp-down") },
          `${delta > 0 ? "▲ +" : "▼ "}${delta}`));
  }

  function drawCompare() {
    if (entries.length < 2) {
      render(body, h("p", { class: "muted" }, "Finish at least two circuits and they'll show up here for comparison."));
      return;
    }
    const byDate = () => new Map(entries.map((e) => [e.date, e]));
    const tableWrap = h("div", {});
    const picker = h("div", { class: "cmp-pick panel" }, h("h3", {}, "Pick days"),
      h("div", { class: "cmp-days" }, ...entries.map((e) => h("label", { class: "check" },
        h("input", { type: "checkbox", checked: chosen.has(e.date), onchange: (ev) => {
          if (ev.target.checked) chosen.add(e.date); else chosen.delete(e.date);
          paintTable();
        } }),
        h("span", {}, `${e.date} — ${e.total_points ?? 0} pts`)))));
    body.replaceChildren(picker, tableWrap);

    function paintTable() {
      const days = [...chosen].sort();
      if (!days.length) { tableWrap.replaceChildren(h("p", { class: "muted" }, "Pick at least one day.")); return; }
      const map = byDate();
      const val = (e, p) => {
        const row = e.pillars.find((x) => x.pillar === p);
        if (!row) return null;
        return metric === "score" ? Math.round(row.score ?? 0) : row.points ?? 0;
      };
      const rows = PILLAR_ORDER.map((p) => {
        let prev = null;
        const cells = days.map((d) => {
          const v = val(map.get(d), p);
          const el = cellEl(v, prev);
          prev = v;
          return el;
        });
        return h("tr", {}, h("th", { scope: "row" }, PILLAR_LABEL[p]), ...cells);
      });
      let prevT = null;
      rows.push(h("tr", { class: "cmp-total" }, h("th", { scope: "row" }, "Total points"),
        ...days.map((d) => {
          const v = map.get(d).total_points ?? 0;
          const el = cellEl(v, prevT);
          prevT = v;
          return el;
        })));
      tableWrap.replaceChildren(
        h("div", { class: "row-actions hist-tools" },
          segmented("Metric", [["score", "Score"], ["points", "Points"]], metric, (v) => { metric = v; paintTable(); })),
        h("p", { class: "muted small" }, "Columns run oldest to newest. Deltas compare each day with the one to its left: ▲ improved, ▼ declined."),
        h("div", { class: "table-wrap" }, h("table", { class: "report cmp-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Pillar"), ...days.map((d) => h("th", {}, d.slice(5))))),
          h("tbody", {}, ...rows))));
    }
    paintTable();
  }

  function draw() {
    if (busy) { body.replaceChildren(h("p", { class: "muted mono" }, "Loading...")); return; }
    if (tab === "days") drawDays(); else drawCompare();
  }

  drawSectionBar();
  drawTabs();
  loadMore();
}
