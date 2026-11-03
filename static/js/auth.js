import { api, refreshSession, setToken } from "./api.js";
import { h, notice, playTally, sleep, tally, toast } from "./dom.js";
import { applyTheme } from "./theme.js";

export const state = { user: null };

export async function loadUser() {
  state.user = await api("/api/users/me");
  applyTheme(state.user.theme);   // the account's choice follows you across devices
  return state.user;
}

export async function bootstrap() {
  if (await refreshSession()) {
    try { await loadUser(); } catch { state.user = null; }
  }
}

export async function logout(ctx) {
  try { await api("/api/auth/logout", { method: "POST", auth: false }); } catch { /* cookie cleared best-effort */ }
  setToken(null);
  state.user = null;
  ctx.navigate("#/login");
}

async function afterLogin(session, ctx) {
  setToken(session.access_token);
  await loadUser();
  ctx.navigate(state.user.profile_complete ? "#/dashboard" : "#/profile");
}

async function mountGoogleButton(slot, ctx, cfg, showError) {
  if (!cfg?.google_client_id) { slot.append(h("p", { class: "muted small mono" }, "Google sign-in isn't configured on this server.")); return; }
  for (let i = 0; i < 40 && !window.google?.accounts?.id; i++) await sleep(150);
  if (!window.google?.accounts?.id) { slot.append(h("p", { class: "muted small mono" }, "Couldn't load Google sign-in. Use email instead.")); return; }
  window.google.accounts.id.initialize({
    client_id: cfg.google_client_id,
    callback: async (r) => {
      try { await afterLogin(await api("/api/auth/google", { method: "POST", body: { credential: r.credential }, auth: false }), ctx); }
      catch (e) { showError(e.message); }
    },
  });
  window.google.accounts.id.renderButton(slot, { theme: "outline", size: "large", shape: "rectangular", text: "continue_with", width: 320 });
}

// Sends the "set your password" link (signup and reset share one endpoint shape). 60s cooldown.
function linkForm({ endpoint, label, sent, getEmail }) {
  const msg = h("div", {});
  const btn = h("button", { class: "btn btn--primary btn--block", type: "submit" }, label);
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); msg.replaceChildren(); btn.disabled = true;
    try {
      const r = await api(endpoint, { method: "POST", body: { email: getEmail() }, auth: false });
      msg.replaceChildren(notice("ok", r.message), h("p", { class: "muted small" }, sent));
      for (let s = 60; s > 0; s--) { btn.textContent = `Send again in ${s}s`; await sleep(1000); }
      btn.textContent = "Send again"; btn.disabled = false;
    } catch (err) { msg.replaceChildren(notice("error", err.message)); btn.disabled = false; }
  } }, btn);
  return { form, msg };
}

export function authView(root, ctx) {
  let mode = ctx.params.get("mode") === "register" ? "register" : "login";
  const flash = ctx.params.get("pw") === "1" ? "Password saved. Log in to continue." : null;
  let cfg = null;

  const hero = h("section", { class: "auth-hero" },
    h("h1", {}, "Your brain has reps too."),
    h("p", { class: "lede" }, "Five sets, under 25 minutes, every day. The game gets harder when you get better, and eases off when you've been away."),
    tally(0, true));
  const seatNote = h("p", { class: "muted small mono" });
  const card = h("section", { class: "auth-card panel", "aria-label": "Sign in" });
  root.append(h("header", { class: "topbar" }, h("span", { class: "wordmark" }, tally(0), "Monkey Brain")), hero, card);
  playTally(hero);
  api("/api/auth/config", { auth: false }).then((c) => {
    cfg = c;
    seatNote.textContent = c.invite_only ? "Invite-only beta." : `${c.seats_left} of ${c.max_users} early-access seats left.`;
    draw();
  }).catch(() => {});

  function draw() {
    const msg = h("div", {});
    const showError = (t) => msg.replaceChildren(notice("error", t));
    const email = h("input", { class: "input", id: "email", type: "email", autocomplete: "email", required: true, maxlength: "254" });
    const gsi = h("div", { class: "gsi-slot" });
    if (flash) msg.append(notice("ok", flash));

    const tabs = h("div", { class: "auth-tabs", role: "tablist" },
      ...[["login", "Log in"], ["register", "Sign up"]].map(([m, label]) =>
        h("button", { type: "button", role: "tab", "aria-selected": String(mode === m), onclick: () => { mode = m; draw(); } }, label)));

    let body;
    if (mode === "register" || mode === "forgot") {
      const forgot = mode === "forgot";
      const { form, msg: m2 } = linkForm({
        endpoint: forgot ? "/api/auth/forgot-password" : "/api/auth/register",
        label: forgot ? "Email me a reset link" : "Email me a sign-up link",
        sent: "Open the link, choose a password, then log in. Check spam if nothing shows up in a minute.",
        getEmail: () => email.value,
      });
      form.prepend(h("div", { class: "field" }, h("label", { for: "email" }, "Email"), email,
        h("span", { class: "hint" }, forgot ? "We'll send a link that works once." : "You'll choose your password after you confirm this address.")));
      body = [msg, m2, form, seatNote];
      if (forgot) body.push(h("button", { type: "button", class: "link-btn", onclick: () => { mode = "login"; draw(); } }, "Back to log in"));
    } else {
      const pw = h("input", { class: "input", id: "pw", type: "password", required: true, maxlength: "72", autocomplete: "current-password" });
      const submit = h("button", { class: "btn btn--primary btn--block", type: "submit" }, "Log in");
      const form = h("form", { onsubmit: async (e) => {
        e.preventDefault(); submit.disabled = true; msg.replaceChildren();
        try { await afterLogin(await api("/api/auth/login", { method: "POST", body: { email: email.value, password: pw.value }, auth: false }), ctx); }
        catch (err) { showError(err.message); } finally { submit.disabled = false; }
      } },
        h("div", { class: "field" }, h("label", { for: "email" }, "Email"), email),
        h("div", { class: "field" }, h("label", { for: "pw" }, "Password"), pw),
        submit);
      body = [msg, form, h("button", { type: "button", class: "link-btn", onclick: () => { mode = "forgot"; draw(); } }, "Forgot password?")];
    }
    card.replaceChildren(tabs, ...body, h("div", { class: "divider" }, "or"), gsi);
    if (cfg) mountGoogleButton(gsi, ctx, cfg, showError);
  }
  draw();
}

export function setPasswordView(root, ctx) {
  const token = ctx.params.get("token") || "";
  history.replaceState(null, "", "#/set-password");   // keep the token out of history and the address bar
  const msg = h("div", {});
  const pw = h("input", { class: "input", id: "pw", type: "password", required: true, minlength: "8", maxlength: "72", autocomplete: "new-password" });
  const pw2 = h("input", { class: "input", id: "pw2", type: "password", required: true, minlength: "8", maxlength: "72", autocomplete: "new-password" });
  const btn = h("button", { class: "btn btn--primary", type: "submit" }, "Save password");
  root.append(
    h("header", { class: "topbar" }, h("span", { class: "wordmark" }, tally(0), "Monkey Brain")),
    h("section", { class: "page-head" }, h("h1", {}, "Choose a password."), h("p", {}, "Opening this link confirmed your email. Pick a password and you're in.")),
    h("section", { class: "auth-card panel" }, token ? msg : notice("error", "This link is missing its token. Request a new one from the log in page."),
      token ? h("form", { onsubmit: async (e) => {
        e.preventDefault(); msg.replaceChildren();
        if (pw.value !== pw2.value) { msg.replaceChildren(notice("error", "Passwords don't match.")); return; }
        btn.disabled = true;
        try {
          await api("/api/auth/set-password", { method: "POST", body: { token, password: pw.value }, auth: false });
          ctx.navigate("#/login?pw=1");
        } catch (err) { msg.replaceChildren(notice("error", err.message)); btn.disabled = false; }
      } },
        h("div", { class: "field" }, h("label", { for: "pw" }, "Password"), pw, h("span", { class: "hint" }, "At least 8 characters.")),
        h("div", { class: "field" }, h("label", { for: "pw2" }, "Repeat password"), pw2),
        btn) : h("a", { class: "btn", href: "#/login" }, "Back to log in")));
}

export function profileView(root, ctx) {
  const u = state.user;
  const msg = h("div", {});
  const name = h("input", { class: "input", id: "name", maxlength: "50", required: true, autocomplete: "name" });
  const alias = h("input", { class: "input", id: "alias", maxlength: "20", required: true, autocomplete: "off", pattern: "[A-Za-z0-9_.\\-]{3,20}" });
  const btn = h("button", { class: "btn btn--primary", type: "submit" }, "Create profile");
  root.append(
    h("header", { class: "topbar" }, h("span", { class: "wordmark" }, tally(0), "Monkey Brain"),
      h("nav", { class: "nav" }, h("button", { type: "button", onclick: () => logout(ctx) }, "Log out"))),
    h("section", { class: "page-head" }, h("h1", {}, "Pick your name."), h("p", {}, "You only do this once. Your alias is what other people see on the leaderboard.")),
    h("section", { class: "auth-card panel" }, msg,
      h("form", { onsubmit: async (e) => {
        e.preventDefault(); btn.disabled = true; msg.replaceChildren();
        try {
          state.user = await api("/api/users/me/profile", { method: "POST", body: { name: name.value, alias: alias.value } });
          toast("Profile created"); ctx.navigate("#/dashboard");
        } catch (err) { msg.replaceChildren(notice("error", err.message)); } finally { btn.disabled = false; }
      } },
        h("div", { class: "field" }, h("label", { for: "e" }, "Email"), h("input", { class: "input", id: "e", value: u.email, readonly: true, "aria-readonly": "true" }), h("span", { class: "hint" }, "Tied to your sign-in, so it can't be changed.")),
        h("div", { class: "field" }, h("label", { for: "name" }, "Name"), name),
        h("div", { class: "field" }, h("label", { for: "alias" }, "Alias"), alias, h("span", { class: "hint" }, "3-20 characters. Letters, numbers, dot, dash, underscore.")),
        btn)));
}
