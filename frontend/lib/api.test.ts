import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  API_TOKEN_STORAGE_KEY,
  apiFetch,
  apiUrl,
  getAuthHeaders,
  setApiToken,
  wsUrl,
} from "./api";

beforeEach(() => {
  window.localStorage.clear();
  vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("apiUrl", () => {
  it("is same-origin relative when no base is configured", () => {
    expect(apiUrl("/api/v1/chat/capabilities")).toBe("/api/v1/chat/capabilities");
  });

  it("adds a missing leading slash", () => {
    expect(apiUrl("api/v1/x")).toBe("/api/v1/x");
  });

  it("prefixes the configured base and avoids double slashes", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8600/");
    expect(apiUrl("/api/v1/x")).toBe("http://localhost:8600/api/v1/x");
    expect(apiUrl("api/v1/x")).toBe("http://localhost:8600/api/v1/x");
  });
});

describe("auth token", () => {
  it("uses a project-owned storage key, not the sidecar one", () => {
    expect(API_TOKEN_STORAGE_KEY).toBe("rag_api_token");
    window.localStorage.setItem("sirchmunk_api_token", "sidecar-secret");
    expect(getAuthHeaders()).toEqual({});
  });

  it("returns no header without a token", () => {
    expect(getAuthHeaders()).toEqual({});
  });

  it("returns a Bearer header from localStorage", () => {
    window.localStorage.setItem(API_TOKEN_STORAGE_KEY, "abc123");
    expect(getAuthHeaders()).toEqual({ Authorization: "Bearer abc123" });
  });

  it("setApiToken stores and clears the token", () => {
    setApiToken("t1");
    expect(window.localStorage.getItem(API_TOKEN_STORAGE_KEY)).toBe("t1");
    setApiToken("");
    expect(window.localStorage.getItem(API_TOKEN_STORAGE_KEY)).toBeNull();
  });
});

describe("wsUrl", () => {
  it("derives ws:// from window.location over http (same-origin)", () => {
    expect(wsUrl("/api/v1/chat")).toBe(`ws://${window.location.host}/api/v1/chat`);
  });

  it("derives wss:// from window.location over https (same-origin)", () => {
    vi.stubGlobal("location", { protocol: "https:", host: "rag.example.com" });
    expect(wsUrl("/api/v1/chat")).toBe("wss://rag.example.com/api/v1/chat");
  });

  it("converts http base to ws and https base to wss", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8600");
    expect(wsUrl("/api/v1/chat")).toBe("ws://localhost:8600/api/v1/chat");
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "https://api.example.com/");
    expect(wsUrl("api/v1/chat")).toBe("wss://api.example.com/api/v1/chat");
  });

  it("appends the stored token as an encoded ?token= query", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8600");
    window.localStorage.setItem(API_TOKEN_STORAGE_KEY, "a b&c=d/é");
    expect(wsUrl("/api/v1/chat")).toBe(
      `ws://localhost:8600/api/v1/chat?token=${encodeURIComponent("a b&c=d/é")}`,
    );
  });

  it("uses & when the path already has a query string", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8600");
    expect(wsUrl("/api/v1/tasks?id=1", { token: "t" })).toBe(
      "ws://localhost:8600/api/v1/tasks?id=1&token=t",
    );
  });

  it("omits the token with token: null and lets an explicit token override storage", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8600");
    window.localStorage.setItem(API_TOKEN_STORAGE_KEY, "stored");
    expect(wsUrl("/ws", { token: null })).toBe("ws://localhost:8600/ws");
    expect(wsUrl("/ws", { token: "explicit" })).toBe("ws://localhost:8600/ws?token=explicit");
  });
});

describe("apiFetch", () => {
  it("sends the auth header to the built URL and lets caller headers win", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8600");
    window.localStorage.setItem(API_TOKEN_STORAGE_KEY, "tok");
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/v1/x", { headers: { "Content-Type": "application/json" } });
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8600/api/v1/x", {
      headers: { Authorization: "Bearer tok", "Content-Type": "application/json" },
    });

    await apiFetch("/api/v1/x", { headers: { Authorization: "Bearer override" } });
    expect(fetchMock.mock.calls[1][1].headers).toEqual({ Authorization: "Bearer override" });
  });
});
