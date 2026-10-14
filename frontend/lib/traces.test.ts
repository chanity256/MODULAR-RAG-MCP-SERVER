import { describe, expect, it } from "vitest";
import {
  QUERY_STAGES,
  agenticRoutes,
  buildWaterfall,
  filterBySource,
  findStage,
  formatMs,
  rerankComparison,
  scalarDetails,
  sourceOptions,
  stageElapsedMs,
  stageHits,
  stageProvider,
  traceSource,
} from "./traces";
import type { StageRecord } from "./types";

const stage = (name: string, extra: Partial<StageRecord> = {}): StageRecord => ({
  stage: name,
  elapsed_ms: null,
  method: null,
  details: {},
  ...extra,
});

const trace = (routes: string | null, metadata: Record<string, unknown> = {}) => ({ routes, metadata });

describe("traceSource", () => {
  it("defaults to hybrid", () => {
    expect(traceSource(trace(null))).toBe("hybrid");
    expect(traceSource(trace("  "))).toBe("hybrid");
  });

  it("uses routes, normalized", () => {
    expect(traceSource(trace("Chat"))).toBe("chat");
    expect(traceSource(trace("agentic"))).toBe("agentic");
  });

  it("falls back to metadata.routes and ignores non-strings", () => {
    expect(traceSource(trace(null, { routes: "chat" }))).toBe("chat");
    expect(traceSource(trace(null, { routes: 3 }))).toBe("hybrid");
  });
});

describe("source filtering", () => {
  const items = [trace(null), trace("chat"), trace("agentic"), trace("chat")];

  it("lists default first then alphabetical", () => {
    expect(sourceOptions(items)).toEqual(["hybrid", "agentic", "chat"]);
    expect(sourceOptions([trace("chat")])).toEqual(["chat"]);
    expect(sourceOptions([])).toEqual([]);
  });

  it("filters by source and passes everything for all", () => {
    expect(filterBySource(items, "chat")).toHaveLength(2);
    expect(filterBySource(items, "hybrid")).toEqual([items[0]]);
    expect(filterBySource(items, "all")).toHaveLength(4);
    expect(filterBySource(items, "missing")).toEqual([]);
  });
});

describe("stage helpers", () => {
  it("findStage returns the last match", () => {
    const stages = [stage("a", { method: "1" }), stage("a", { method: "2" })];
    expect(findStage(stages, "a")?.method).toBe("2");
    expect(findStage(stages, "b")).toBeUndefined();
  });

  it("stageElapsedMs falls back to details.elapsed_ms", () => {
    expect(stageElapsedMs(stage("a", { elapsed_ms: 5 }))).toBe(5);
    expect(stageElapsedMs(stage("a", { elapsed_ms: 0, details: { elapsed_ms: 7 } }))).toBe(7);
    expect(stageElapsedMs(stage("a"))).toBe(0);
  });

  it("stageProvider reads a top-level extra, then details", () => {
    expect(stageProvider(stage("a", { provider: "x" }))).toBe("x");
    expect(stageProvider(stage("a", { details: { provider: "y" } }))).toBe("y");
    expect(stageProvider(stage("a"))).toBeNull();
  });

  it("scalarDetails keeps short primitives only, capped", () => {
    const out = scalarDetails({ a: 1, b: "x", c: { n: 1 }, d: null, e: "y".repeat(80), f: true, g: 2, h: 3 }, 4);
    expect(out).toEqual([
      ["a", "1"],
      ["b", "x"],
      ["f", "true"],
      ["g", "2"],
    ]);
  });
});

describe("buildWaterfall", () => {
  it("computes shares of the sum and widths relative to the slowest stage", () => {
    const rows = buildWaterfall(
      [
        { stage: "dense_retrieval", elapsed_ms: 30 },
        { stage: "rerank", elapsed_ms: 10 },
      ],
      [],
      QUERY_STAGES,
    );
    expect(rows.map((r) => r.stage)).toEqual(["dense_retrieval", "rerank"]);
    expect(rows[0].percent).toBeCloseTo(75);
    expect(rows[1].percent).toBeCloseTo(25);
    expect(rows[0].width).toBe(100);
    expect(rows[1].width).toBeCloseTo(33.333, 2);
  });

  it("appends unknown stages (chat_*) and sums same-named ones", () => {
    const rows = buildWaterfall(
      [{ stage: "hybrid", elapsed_ms: 20 }],
      [
        stage("hybrid", { elapsed_ms: 20 }),
        stage("chat_generation", { elapsed_ms: 60 }),
        stage("chat_generation", { elapsed_ms: 20 }),
        stage("chat_history", { elapsed_ms: 0 }),
      ],
      QUERY_STAGES,
    );
    expect(rows.map((r) => r.stage)).toEqual(["hybrid", "chat_generation"]);
    expect(rows[1].elapsed_ms).toBe(80);
    expect(rows[1].percent).toBeCloseTo(80);
  });

  it("uses every stage when the backend sent no timings", () => {
    const rows = buildWaterfall([], [stage("fusion", { elapsed_ms: 4 }), stage("odd", { details: { elapsed_ms: 4 } })], QUERY_STAGES);
    expect(rows.map((r) => [r.stage, r.percent])).toEqual([
      ["fusion", 50],
      ["odd", 50],
    ]);
  });

  it("returns nothing when no stage has time", () => {
    expect(buildWaterfall([], [stage("a")])).toEqual([]);
  });
});

describe("hits and rerank", () => {
  it("stageHits prefers preview over after", () => {
    const s = stage("dense_retrieval", { details: { preview: [{ chunk_id: "a", score: 0.9 }], after: [{ chunk_id: "z" }] } });
    expect(stageHits(s)).toEqual([{ chunk_id: "a", score: 0.9 }]);
    expect(stageHits(stage("x", { details: { after: [{ chunk_id: "b" }] } }))).toEqual([{ chunk_id: "b", score: null }]);
    expect(stageHits(undefined)).toEqual([]);
  });

  it("rerankComparison reports movement", () => {
    const rows = rerankComparison({
      before: [{ chunk_id: "a" }, { chunk_id: "b" }, { chunk_id: "c" }],
      after: [
        { chunk_id: "c", score: 0.9 },
        { chunk_id: "b", score: 0.5 },
        { chunk_id: "a", score: 0.1 },
        { chunk_id: "n", score: 0.05 },
      ],
    });
    expect(rows.map((r) => [r.chunk_id, r.before, r.delta, r.marker])).toEqual([
      ["c", 3, 2, "up"],
      ["b", 2, 0, "same"],
      ["a", 1, -2, "down"],
      ["n", null, null, "new"],
    ]);
  });

  it("rerankComparison tolerates missing lists", () => {
    expect(rerankComparison({})).toEqual([]);
  });
});

describe("agenticRoutes", () => {
  it("is null without agentic info", () => {
    expect(agenticRoutes([stage("hybrid", { details: { routes: { dense: 1, sparse: 2 } } })])).toBeNull();
  });

  it("collects route counts and trigger", () => {
    const result = agenticRoutes([
      stage("hybrid", {
        details: { routes: { dense: 5, sparse: 4, agentic: 2 }, agentic_trigger: { strategy: "auto", triggered: true, reason: "weak" } },
      }),
      stage("fusion", { details: { routes: { dense: 5, sparse: 4, agentic: 2 } } }),
    ]);
    expect(result?.rows.map((r) => r.stage)).toEqual(["hybrid", "fusion"]);
    expect(result?.trigger).toMatchObject({ strategy: "auto", triggered: true });
  });
});

describe("formatMs", () => {
  it("formats by magnitude and handles missing values", () => {
    expect(formatMs(null)).toBe("-");
    expect(formatMs(0.057)).toBe("0.06 ms");
    expect(formatMs(12.34)).toBe("12.3 ms");
    expect(formatMs(1234.5)).toBe("1235 ms");
  });
});
