// Theme = "light" | "dark" | "system". "system" removes the override so the device setting applies.
export function applyTheme(theme) {
  const root = document.documentElement;
  try {
    if (theme === "light" || theme === "dark") { root.setAttribute("data-theme", theme); localStorage.setItem("bm_theme", theme); }
    else { root.removeAttribute("data-theme"); localStorage.removeItem("bm_theme"); }
  } catch { /* storage blocked: the attribute still applies for this visit */ }
}

export const systemPrefersDark = () => matchMedia("(prefers-color-scheme: dark)").matches;

// What the user is actually looking at right now.
export function effectiveTheme() {
  const t = document.documentElement.getAttribute("data-theme");
  return t === "light" || t === "dark" ? t : systemPrefersDark() ? "dark" : "light";
}
