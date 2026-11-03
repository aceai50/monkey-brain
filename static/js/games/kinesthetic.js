import { h } from "../dom.js";

export function mount(box, trials, { onDone }) {
  const results = [];
  let dead = false;
  const msg = h("p", { class: "arena__msg mono" }, "Get ready.");
  const arena = h("div", { class: "arena", tabindex: "0", role: "application", "aria-label": "Reaction arena. Click or press space when it flashes." }, msg);
  const count = h("p", { class: "mono muted" });
  box.append(count, arena);
  arena.focus();

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  // Resolves with {rt} on first tap, or {early:true} if tapped while `armed` is false.
  const tap = (armedFn, timeoutMs) => new Promise((res) => {
    const off = () => { arena.removeEventListener("pointerdown", on); removeEventListener("keydown", key); clearTimeout(t); };
    const fire = () => { const now = performance.now(); off(); res({ at: now, early: !armedFn() }); };
    const on = (e) => { if (e.target.closest(".target")) return; fire(); };
    const key = (e) => { if (e.code === "Space") { e.preventDefault(); fire(); } };
    const t = timeoutMs ? setTimeout(() => { off(); res({ timeout: true }); }, timeoutMs) : null;
    arena.addEventListener("pointerdown", on); addEventListener("keydown", key);
  });

  async function reaction(t) {
    arena.className = "arena"; msg.textContent = "Wait for the flash.";
    let go = false, t0 = 0;
    const early = tap(() => go, null);
    const flash = wait(t.delay_ms).then(() => ({ flash: true }));
    const first = await Promise.race([early, flash]);
    if (dead) return;
    if (first.flash) {
      go = true; arena.classList.add("is-go"); msg.textContent = "Now!"; t0 = performance.now();
      const hit = await Promise.race([early, wait(1500).then(() => ({ timeout: true }))]);
      if (dead) return;
      if (hit.timeout) { results.push({ id: t.id, rt_ms: null, outcome: "miss" }); msg.textContent = "Too slow."; }
      else { const rt = Math.round(hit.at - t0); results.push({ id: t.id, rt_ms: rt, outcome: "hit" }); msg.textContent = `${rt} ms`; }
    } else {
      results.push({ id: t.id, rt_ms: null, outcome: "false_start" }); msg.textContent = "Too early. Zero for that one.";
    }
    arena.classList.remove("is-go");
  }

  async function target(t) {
    arena.className = "arena"; msg.textContent = "";
    await wait(t.delay_ms);
    if (dead) return;
    const el = h("button", { class: "target", type: "button", "aria-label": "Target" });
    el.style.width = el.style.height = t.size + "px";
    el.style.left = `calc(${t.x}% - ${t.size / 2}px)`; el.style.top = `calc(${t.y}% - ${t.size / 2}px)`;
    arena.append(el);
    const t0 = performance.now();
    const hit = await new Promise((res) => {
      const to = setTimeout(() => res(null), t.ttl_ms);
      el.addEventListener("pointerdown", (e) => { e.stopPropagation(); clearTimeout(to); res(performance.now()); });
    });
    el.remove();
    if (dead) return;
    if (hit == null) { results.push({ id: t.id, rt_ms: null, outcome: "miss" }); msg.textContent = "Missed."; }
    else { const rt = Math.round(hit - t0); results.push({ id: t.id, rt_ms: rt, outcome: "hit" }); msg.textContent = `${rt} ms`; }
    await wait(250);
  }

  (async () => {
    await wait(700);
    for (let k = 0; k < trials.length && !dead; k++) {
      count.textContent = `Trial ${k + 1} of ${trials.length}`;
      await (trials[k].type === "reaction" ? reaction(trials[k]) : target(trials[k]));
      if (!dead) await wait(600);
    }
    if (!dead) onDone();
  })();
  return { collect: () => ({ trials: results.slice(0, 120) }), destroy: () => { dead = true; box.replaceChildren(); } };
}
