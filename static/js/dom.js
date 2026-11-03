// Tiny DOM helper. Text only ever goes through createTextNode / textContent: never innerHTML.
export function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export function tally(on = 0, large = false) {
  const cells = Array.from({ length: 5 }, (_, i) => h("span", { class: "tally__cell" + (i < on ? " is-on" : "") }));
  return h("span", { class: "tally" + (large ? " tally--lg" : ""), role: "img", "aria-label": `${on} of 5 pillars complete` }, cells);
}

export async function playTally(el) {
  const cells = [...el.querySelectorAll(".tally__cell")];
  if (reducedMotion()) return cells.forEach((c) => c.classList.add("is-on"));
  for (const c of cells) { await sleep(130); c.classList.add("is-on"); }
}

export function notice(kind, text) {
  return h("div", { class: "notice", "data-kind": kind, role: kind === "error" ? "alert" : "status" }, text);
}

let toastTimer;
export function toast(text) {
  const el = document.getElementById("status");
  el.textContent = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.textContent = ""), 3500);
}

export const fmtClock = (sec) => {
  const t = Math.max(0, Math.ceil(sec));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
};

export function topbar(active, onLogout, user) {
  const link = (hash, label, key) =>
    h("a", { href: hash, "aria-current": active === key ? "page" : null }, label);
  return h("header", { class: "topbar" },
    h("a", { class: "wordmark", href: "#/dashboard" }, tally(0), "Brain Monkey"),
    h("nav", { class: "nav", "aria-label": "Main" },
      link("#/dashboard", "Dashboard", "dashboard"),
      link("#/leaderboard", "Leaderboard", "leaderboard"),
      link("#/customize", "Customize", "customize"),
      link("#/settings", "Settings", "settings"),
      user?.is_admin ? link("#/admin", "Admin", "admin") : null,
      h("button", { type: "button", onclick: onLogout }, "Log out")));
}

// Accessible on/off switch. `onChange(next)` must return a promise; if it rejects the switch snaps back.
export function switchToggle({ id, label, checked, onChange, onError }) {
  let value = !!checked;
  const btn = h("button", { type: "button", class: "switch", role: "switch", id, "aria-checked": String(value), "aria-labelledby": id + "-label" },
    h("span", { class: "switch__track" }, h("span", { class: "switch__thumb" })));
  const paint = (v) => btn.setAttribute("aria-checked", String(v));
  btn.addEventListener("click", async () => {
    if (btn.disabled) return;
    const next = !value;
    paint(next); btn.disabled = true;
    try { await onChange(next); value = next; }
    catch (e) { paint(value); onError?.(e); }
    finally { btn.disabled = false; }
  });
  return h("div", { class: "switch-row" }, btn, h("label", { class: "switch__label", id: id + "-label", for: id }, label));
}
