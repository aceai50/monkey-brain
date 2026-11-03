import { setSessionLostHandler } from "./api.js";
import { authView, bootstrap, profileView, setPasswordView, state } from "./auth.js";
import { customizeView } from "./customize.js";
import { dashboardView, settingsView } from "./dashboard.js";
import { toast } from "./dom.js";
import { adminView } from "./admin.js";
import { leaderboardView } from "./leaderboard.js";
import { trainView } from "./training.js";

const root = document.getElementById("app");
const views = {
  login: authView, "set-password": setPasswordView, profile: profileView, dashboard: dashboardView,
  settings: settingsView, leaderboard: leaderboardView, customize: customizeView, train: trainView, admin: adminView,
};
const PUBLIC = new Set(["login", "set-password"]);
let cleanup = null;

function navigate(hash) {
  if (location.hash === hash) render(); else location.hash = hash;
}

function render() {
  cleanup?.(); cleanup = null;
  const [full, qs] = location.hash.replace(/^#\/?/, "").split("?");
  const [path, arg] = full.split("/");
  let name = views[path] ? path : "dashboard";
  if (!state.user) { if (!PUBLIC.has(name)) name = "login"; }
  else if (!state.user.profile_complete) name = "profile";
  else if (PUBLIC.has(name) || name === "profile") name = "dashboard";
  else if (name === "admin" && !state.user.is_admin) name = "dashboard";
  const wanted = "#/" + name + (name === "train" && arg ? "/" + arg : "");
  if (!location.hash.startsWith(wanted)) history.replaceState(null, "", wanted + (PUBLIC.has(name) && qs ? "?" + qs : ""));
  root.replaceChildren();
  cleanup = views[name](root, { navigate, params: new URLSearchParams(qs || ""), arg }) || null;
  document.title = "Brain Monkey";
  window.scrollTo(0, 0);
  const h1 = root.querySelector("h1");
  h1?.setAttribute("tabindex", "-1");
  h1?.focus({ preventScroll: true });
}

// Custom cursor (fine pointers only)
const cur = document.getElementById("cursor");
if (matchMedia("(pointer: fine)").matches && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
  addEventListener("pointermove", (e) => { cur.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`; }, { passive: true });
  document.addEventListener("pointerover", (e) => cur.classList.toggle("is-active", !!e.target.closest("a, button, input, label, [role=tab]")));
}

setSessionLostHandler(() => {
  if (!state.user) return;
  state.user = null;
  toast("Your session ended. Log in again.");
  navigate("#/login");
});
addEventListener("hashchange", render);
await bootstrap();
render();
