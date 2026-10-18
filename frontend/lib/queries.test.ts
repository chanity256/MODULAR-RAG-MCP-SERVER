import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import { buildQuery, fetchOverview, fetchTrace, fetchTraces } from "./queries";

beforeEach(() => {
  window.localStorage.clear();
  vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("buildQuery", () => {
  it("skips empty values and encodes the rest", () => {
    expect(buildQuery({ a: "x y", b: undefined, c: "", d: 0, e: null })).toBe("?a=x+y&d=0");
    expect(buildQuery({})).toBe("");
  });
});

describe("typed client", () => {
  it("fetchTraces builds the query string and normalizes the page", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ items: [{ trace_id: "t" }], total: 1, limit: 500, offset: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    const page = await fetchTraces({ traceType: "query", keyword: " hello ", limit: 500 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/traces?trace_type=query&keyword=hello&limit=500");
    expect(page.items[0].trace_id).toBe("t");
  });

  it("fetchTraces passes the server-side routes filter", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ items: [], total: 0, limit: 500, offset: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    await fetchTraces({ traceType: "query", routes: "default", limit: 500 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/traces?trace_type=query&routes=default&limit=500");
  });

  it("fetchTraces sends the offset for server-side pagination", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(json({ items: [{ trace_id: "p2" }], total: 120, limit: 50, offset: 50 }));
    vi.stubGlobal("fetch", fetchMock);
    await fetchTraces({ traceType: "query", limit: 50, offset: 50 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/traces?trace_type=query&limit=50&offset=50");
  });

  it("fetchTrace encodes the id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ trace_id: "a/b", stages: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await fetchTrace("a/b");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/traces/a%2Fb");
  });

  it("surfaces the unified error envelope as ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ success: false, error: { code: "unauthorized", message: "Invalid API token", details: null } }, 401)),
    );
    const error = await fetchOverview().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, code: "unauthorized", message: "Invalid API token", isUnauthorized: true });
  });

  it("handles non-JSON error bodies", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>bad gateway</html>", { status: 502 })));
    await expect(fetchOverview()).rejects.toMatchObject({ status: 502, message: "Request failed (HTTP 502)" });
  });

  it("maps network failures to network_error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(fetchOverview()).rejects.toMatchObject({ status: 0, code: "network_error" });
  });
});
