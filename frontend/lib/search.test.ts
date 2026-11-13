import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import {
  buildSearchRequest,
  fetchSearchCapabilities,
  initialMode,
  isModeAvailable,
  normalizeCapabilities,
  normalizeSearchResponse,
  runSearch,
  telemetryRows,
  validateSearchForm,
} from "./search";

afterEach(() => vi.unstubAllGlobals());

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const form = { mode: "normal" as const, query: "  hello ", topK: "5", collection: " docs " };

describe("request building", () => {
  it("sends only API fields, trimmed", () => {
    expect(buildSearchRequest(form)).toEqual({ mode: "normal", query: "hello", top_k: 5, collection: "docs" });
  });
  it("omits empty top_k and drops collection outside normal mode", () => {
    expect(buildSearchRequest({ ...form, mode: "fast", topK: "" })).toEqual({ mode: "fast", query: "hello" });
  });
  it("validates query and top_k", () => {
    expect(validateSearchForm({ ...form, query: " " })).toMatch(/query/i);
    expect(validateSearchForm({ ...form, topK: "0" })).toMatch(/Top-K/);
    expect(validateSearchForm({ ...form, topK: "101" })).toMatch(/Top-K/);
    expect(validateSearchForm({ ...form, topK: "x" })).toMatch(/Top-K/);
    expect(validateSearchForm(form)).toBeNull();
  });
});

describe("capabilities", () => {
  const raw = {
    modes: ["normal"],
    default_mode: "normal",
    agentic_enabled: false,
    allow_deep: false,
    availability: {
      normal: { available: true, reason: null },
      fast: { available: false, reason: "agentic_search.enabled is false" },
      deep: { available: false, reason: "disabled" },
    },
  };
  it("exposes per-mode availability with reasons", () => {
    const caps = normalizeCapabilities(raw);
    expect(isModeAvailable(caps, "normal")).toBe(true);
    expect(isModeAvailable(caps, "fast")).toBe(false);
    expect(caps.availability.fast.reason).toBe("agentic_search.enabled is false");
    expect(initialMode(caps)).toBe("normal");
  });
  it("falls back to modes when availability is missing, and to normal for an unusable default", () => {
    const caps = normalizeCapabilities({ modes: ["normal", "fast"], default_mode: "deep" });
    expect(isModeAvailable(caps, "fast")).toBe(true);
    expect(isModeAvailable(caps, "deep")).toBe(false);
    expect(initialMode(caps)).toBe("normal");
  });
  it("normalizes progress_stream strictly to a boolean (WS3 gate)", () => {
    expect(normalizeCapabilities({ ...raw, progress_stream: true }).progress_stream).toBe(true);
    expect(normalizeCapabilities({ ...raw, progress_stream: "yes" }).progress_stream).toBe(false);
    expect(normalizeCapabilities({}).progress_stream).toBe(false);
  });
  it("fetches via GET", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(raw));
    vi.stubGlobal("fetch", fetchMock);
    const caps = await fetchSearchCapabilities();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/search/capabilities");
    expect(caps.modes).toEqual(["normal"]);
  });
});

describe("response normalization", () => {
  it("normalizes citations and agentic telemetry", () => {
    const result = normalizeSearchResponse({
      mode: "fast",
      query: "q",
      elapsed_ms: 12.4,
      markdown: "# hi",
      structured: {
        citations: [{ id: 1, source: "a.md", page: 2, score: 0.5, text: "t" }, "junk", { source: 3 }],
        agentic: {
          mode: "FAST",
          total_llm_tokens: 10,
          llm_call_count: 0,
          evidence_files: ["x", 2],
          telemetry: { a: 1, b: { c: 2 }, d: null },
        },
      },
      is_error: false,
      error: null,
    });
    expect(result.citations).toHaveLength(2);
    expect(result.citations[0]).toEqual({ id: 1, source: "a.md", page: 2, score: 0.5, text: "t" });
    expect(result.citations[1]).toMatchObject({ id: null, source: "", page: null });
    expect(result.agentic?.evidence_files).toEqual(["x", "2"]);
    expect(result.agentic?.telemetry).toEqual([
      { key: "a", value: "1" },
      { key: "b", value: "c: 2" },
      { key: "d", value: "-" },
    ]);
    expect(result.agentic?.elapsed_ms).toBeNull();
  });
  it("telemetryRows ignores non-objects", () => {
    expect(telemetryRows("x")).toEqual([]);
    expect(telemetryRows(null)).toEqual([]);
  });
  it("keeps is_error results with their message", () => {
    const result = normalizeSearchResponse({ mode: "deep", query: "q", is_error: true, error: "boom", markdown: "boom" });
    expect(result).toMatchObject({ is_error: true, error: "boom", citations: [], agentic: null });
  });
});

describe("runSearch", () => {
  it("POSTs the JSON body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ mode: "normal", query: "hello", elapsed_ms: 1, markdown: "m", structured: {} }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await runSearch(form);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/search");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ mode: "normal", query: "hello", top_k: 5, collection: "docs" });
    expect(result.markdown).toBe("m");
  });
  it("surfaces a 403 gate as ApiError with its code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ success: false, error: { code: "DEEP_DISABLED", message: "deep off", details: { mode: "deep" } } }, 403)),
    );
    const error = await runSearch({ ...form, mode: "deep" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, code: "DEEP_DISABLED", message: "deep off" });
  });
  it("surfaces 422 and 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: "bad" }, 422)));
    expect(await runSearch(form).catch((e: unknown) => e)).toMatchObject({ status: 422, code: "validation_error" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ success: false, error: { code: "unauthorized", message: "no" } }, 401)));
    expect(await runSearch(form).catch((e: unknown) => e)).toMatchObject({ isUnauthorized: true });
  });
});
