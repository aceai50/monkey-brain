// Runs before first paint (loaded in <head>) so there is no flash of the wrong theme.
// Classic script, not a module: it must execute synchronously.
(function () {
  try {
    var t = localStorage.getItem("bm_theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
    if (localStorage.getItem("bm_s_mode") === "1") document.documentElement.setAttribute("data-s-mode", "");
  } catch (e) { /* storage blocked: fall back to the device setting */ }
})();
