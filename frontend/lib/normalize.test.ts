import { describe, expect, it } from "vitest";
import { normalizeOverview, normalizeTraceDetail, normalizeTracePage } from "./normalize";

describe("normalizeTracePage", () => {
  it("fills defaults for a sparse payload", () => {
    const page = normalizeTracePage({ items: [{ trace_id: "t1", trace_type: "query" }], total: 3, limit: 50, offset: 0 });
    expect(page.total).toBe(3);
    expect(page.items[0]).toMatchObject({
      trace_id: "t1",
      metadata: {},
      stage_count: 0,
      routes: null,
      source: null,
      skipped: null,
      total_elapsed_ms: null,
    });
  });

  it("survives garbage", () => {
    expect(normalizeTracePage(null)).toEqual({ items: [], total: 0, limit: 0, offset: 0 });
  });
});

describe("normalizeTraceDetail", () => {
  it("keeps unmodelled stage fields and defaults details", () => {
    const trace = normalizeTraceDetail({
      trace_id: "t",
      trace_type: "query",
      stages: [{ stage: "chat_generation", elapsed_ms: 12.5, method: "x", provider: "openai", details: null }],
      timings: [{ stage: "rerank", elapsed_ms: 3 }],
    });
    expect(trace.stages[0].provider).toBe("openai");
    expect(trace.stages[0].details).toEqual({});
    expect(trace.timings).toEqual([{ stage: "rerank", elapsed_ms: 3 }]);
  });

  it("ignores non-finite numbers", () => {
    const trace = normalizeTraceDetail({ trace_id: "t", total_elapsed_ms: "fast", stages: "nope" });
    expect(trace.total_elapsed_ms).toBeNull();
    expect(trace.stages).toEqual([]);
  });
});

describe("normalizeOverview", () => {
  it("normalizes cards, totals, collections and latest traces", () => {
    const overview = normalizeOverview({
      components: [{ name: "LLM", provider: "openai", model: null, headline: "openai / gpt", extras: { a: 1 } }],
      totals: { collections: 1, documents: 2, chunks: 3, images: 4 },
      collections: [{ name: "default", document_count: 2, chunk_count: 3, image_count: 4 }],
      latest_ingestion: { trace_id: "i1", trace_type: "ingestion" },
      latest_query: null,
      warnings: ["collection stats unavailable", 5],
    });
    expect(overview.components[0].extras).toEqual({ a: 1 });
    expect(overview.totals).toEqual({ collections: 1, documents: 2, chunks: 3, images: 4 });
    expect(overview.latest_ingestion?.trace_id).toBe("i1");
    expect(overview.latest_query).toBeNull();
    expect(overview.warnings).toEqual(["collection stats unavailable"]);
  });

  it("defaults everything for an empty payload", () => {
    const overview = normalizeOverview({});
    expect(overview.components).toEqual([]);
    expect(overview.totals).toEqual({ collections: 0, documents: 0, chunks: 0, images: 0 });
  });
});
