import { api } from "./api.js";
import { logout, state } from "./auth.js";
import { h, notice, switchToggle, toast, topbar } from "./dom.js";
import { applyTheme, effectiveTheme } from "./theme.js";

// Same formula as the server (customize.volume_weight): 0.5 at the fewest questions, 1.0 at the full set.
const volume = (floor, count, max) => floor + (1 - floor) * Math.min(1, count / max);

// Appearance: dark/light switch. Applies instantly, saves to the account, rolls back if the save fails.
function appearancePanel() {
  const msg = h("div", {});
  const hint = h("p", { class: "muted small" });
  const say = () => { hint.textContent = state.user.theme === "system"
    ? "Following your device setting until you choose."
    : `Saved to your account: ${state.user.theme} mode on every device.`; };
  const save = async (theme) => { state.user = await api("/api/users/me", { method: "PATCH", body: { theme } }); say(); };
  const sw = switchToggle({
    id: "dark-mode", label: "Dark mode", checked: effectiveTheme() === "dark",
    onChange: async (dark) => {
      msg.replaceChildren();
      const before = state.user.theme;
      applyTheme(dark ? "dark" : "light");          // instant feedback
      try { await save(dark ? "dark" : "light"); }
      catch (e) { applyTheme(before); throw e; }     // undo the preview if it didn't save
    },
    onError: (e) => msg.replaceChildren(notice("error", e.message)),
  });
  const match = h("button", { class: "link-btn", type: "button", onclick: async () => {
    msg.replaceChildren();
    try {
      await save("system"); applyTheme("system");
      sw.querySelector("button").setAttribute("aria-checked", String(effectiveTheme() === "dark"));
    } catch (e) { msg.replaceChildren(notice("error", e.message)); }
  } }, "Match my device instead");
  say();
  return h("div", { class: "panel appearance" }, h("h3", {}, "Appearance"), sw, hint, match, msg);
}

export function customizeView(root, ctx) {
  const msg = h("div", {});
  const body = h("div", { class: "customize" });
  root.append(topbar("customize", () => logout(ctx), state.user),
    h("section", { class: "page-head" }, h("h1", {}, "Customize."),
      h("p", {}, "Pick your theme, and choose how many questions you get in each pillar, from a short set up to the full set we generate. Each pillar is still capped at four minutes.")),
    h("section", { class: "board-wrap" }, msg, appearancePanel(), body));

  async function load() {
    let cz;
    try { cz = await api("/api/users/me/customize"); } catch (e) { msg.replaceChildren(notice("error", e.message)); return; }
    const counts = { ...cz.counts };
    const rows = Object.entries(cz.limits).map(([pillar, lim]) => {
      const out = h("output", { class: "mono big-num", for: `c-${pillar}` });
      const vol = h("span", { class: "mono muted small" });
      const paint = () => {
        out.textContent = counts[pillar];
        vol.textContent = `Volume multiplier x${volume(cz.volume_floor, counts[pillar], lim.max).toFixed(2)}`;
      };
      const slider = h("input", { type: "range", id: `c-${pillar}`, min: lim.min, max: lim.max, step: "1", value: counts[pillar],
        "aria-label": `${lim.label}: number of questions`,
        oninput: (e) => { counts[pillar] = Number(e.target.value); paint(); } });
      paint();
      return h("div", { class: "panel cz-row" },
        h("div", { class: "cz-head" }, h("h3", {}, lim.label), out),
        slider,
        h("div", { class: "slider-scale mono small muted" }, h("span", {}, `${lim.min} fewest`), h("span", {}, `${lim.max} full set`)),
        vol);
    });
    const save = h("button", { class: "btn btn--primary", type: "button", onclick: async () => {
      save.disabled = true; msg.replaceChildren();
      try { await api("/api/users/me/customize", { method: "PUT", body: { counts } }); toast("Saved. Applies from your next session."); }
      catch (e) { msg.replaceChildren(notice("error", e.message)); } finally { save.disabled = false; }
    } }, "Save my question counts");
    const reset = h("button", { class: "btn", type: "button", onclick: () => {
      for (const [p, l] of Object.entries(cz.limits)) counts[p] = l.default;
      body.querySelectorAll("input[type=range]").forEach((el, i) => {
        const p = Object.keys(cz.limits)[i]; el.value = counts[p]; el.dispatchEvent(new Event("input"));
      });
    } }, "Reset to defaults");
    body.replaceChildren(
      h("div", { class: "panel" }, h("h3", {}, "How this affects your points"),
        h("p", { class: "muted" }, "Fewer questions means a shorter workout but a lower volume multiplier (down to x0.50). The full set is x1.00. Your score is the share of the questions you get right, and Auto difficulty also looks at how many you got right out of how many you attempted.")),
      ...rows, h("div", { class: "row-actions" }, save, reset));
  }
  load();
}
