/*
 * Derived from sirchmunk `web/lib/api.ts` (Apache-2.0, https://github.com/modelscope/sirchmunk).
 * Modified: project-owned token key (RAG_API_TOKEN, never the sidecar token), lazy
 * base-URL resolution, token-aware wsUrl, apiFetch helper. See ../NOTICE.
 */

/** localStorage key holding the value of the backend's `RAG_API_TOKEN`. */
export const API_TOKEN_STORAGE_KEY = "rag_api_token";

/**
 * API base URL. Read at call time (Next inlines `process.env.NEXT_PUBLIC_*` at build).
 * Empty string means same-origin, which is the single-port static-export deployment.
 */
export function getApiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE ?? "";
}

function trimTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

function normalizePath(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

export function getApiToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(API_TOKEN_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setApiToken(token: string): void {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(API_TOKEN_STORAGE_KEY, token);
    else window.localStorage.removeItem(API_TOKEN_STORAGE_KEY);
  } catch {
    // localStorage may be disabled; requests then go out unauthenticated.
  }
}

/** `Authorization: Bearer` header when a token is stored, otherwise an empty object. */
export function getAuthHeaders(): Record<string, string> {
  const token = getApiToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Build an absolute (or same-origin relative) HTTP URL for an API path. */
export function apiUrl(path: string): string {
  return `${trimTrailingSlash(getApiBase())}${normalizePath(path)}`;
}

/**
 * Build a WebSocket URL: http -> ws, https -> wss; same-origin mode derives the origin
 * from `window.location`. Browsers cannot set WS headers, so the token (if any) travels
 * as a `?token=` query parameter. Pass `token: null` to omit it, or a string to override.
 */
export function wsUrl(path: string, opts: { token?: string | null } = {}): string {
  const apiBase = getApiBase();
  let base: string;
  if (!apiBase && typeof window !== "undefined") {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    base = `${proto}//${window.location.host}`;
  } else {
    base = apiBase.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
  }

  const url = `${trimTrailingSlash(base)}${normalizePath(path)}`;
  const token = opts.token === undefined ? getApiToken() : (opts.token ?? "");
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

/** `fetch` against the API with the auth header merged in (caller headers win). */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = { ...getAuthHeaders(), ...(init.headers as Record<string, string> | undefined) };
  return fetch(apiUrl(path), { ...init, headers });
}
