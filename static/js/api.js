// Access token lives in memory only (XSS can't read it from storage). The refresh token is an
// HttpOnly cookie that JS never sees.
let accessToken = null;
let refreshing = null;

export const setToken = (t) => { accessToken = t; };
let onLost = null;
export const setSessionLostHandler = (fn) => { onLost = fn; };

export class ApiError extends Error {
  constructor(status, detail) {
    let msg = "Something went wrong. Try again.";
    let code = null;
    if (typeof detail === "string") msg = detail;
    else if (Array.isArray(detail) && detail[0]?.msg) msg = detail[0].msg.replace(/^Value error, /, "");
    else if (detail && typeof detail === "object") { msg = detail.message || msg; code = detail.code || null; }
    super(msg);
    this.status = status;
    this.code = code;
  }
}

function send(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  return fetch(path, {
    method, headers, credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

export function refreshSession() {
  if (refreshing) return refreshing; // de-dupe concurrent refreshes within this tab
  refreshing = (async () => {
    for (let i = 0; i < 3; i++) {
      const res = await fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" });
      if (res.ok) { const d = await res.json(); accessToken = d.access_token; return d; }
      if (res.status === 409) { await new Promise((r) => setTimeout(r, 400)); continue; } // other tab won the race
      break;
    }
    accessToken = null;
    return null;
  })().finally(() => { refreshing = null; });
  return refreshing;
}

export async function api(path, opts = {}) {
  let res = await send(path, opts);
  if (res.status === 401 && opts.auth !== false) {
    if (await refreshSession()) res = await send(path, opts);
    else if (onLost) onLost();   // session is really gone: route to login instead of showing a dead page
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.detail);
  return data;
}
