import { describe, expect, it } from "vitest";
import {
  normalizeCapabilities,
  normalizeCitations,
  normalizeSessionDetail,
  normalizeSessionPage,
  parseDownstreamFrame,
} from "./normalize";

describe("parseDownstreamFrame", () => {
  it("parses every documented frame type", () => {
    expect(parseDownstreamFrame('{"type":"session","session_id":"s1"}')).toEqual({ type: "session", session_id: "s1" });
    expect(parseDownstreamFrame('{"type":"status","stage":"retrying","message":"m"}')).toEqual({
      type: "status",
      stage: "retrying",
      message: "m",
    });
    expect(parseDownstreamFrame('{"type":"search_log","task_id":"t","level":"info","message":"x","timestamp":5}')).toEqual({
      type: "search_log",
      task_id: "t",
      level: "info",
      message: "x",
      timestamp: 5,
    });
    expect(parseDownstreamFrame('{"type":"search_complete","message":"ok"}')).toEqual({ type: "search_complete", message: "ok" });
    expect(parseDownstreamFrame('{"type":"search_error","message":"bad"}')).toEqual({ type: "search_error", message: "bad" });
    expect(parseDownstreamFrame('{"type":"stream","content":"hi"}')).toEqual({ type: "stream", content: "hi" });
    expect(parseDownstreamFrame('{"type":"error","code":"AUTH","message":"no"}')).toEqual({
      type: "error",
      code: "AUTH",
      message: "no",
    });
  });

  it("normalizes sources and result frames", () => {
    const sources = parseDownstreamFrame(
      JSON.stringify({ type: "sources", citations: [{ id: 1, source: "a.pdf", page: 3, chunk_id: "c", text: "t", score: 0.5 }, "junk"] }),
    );
    expect(sources).toEqual({
      type: "sources",
      citations: [{ id: 1, source: "a.pdf", page: 3, chunk_id: "c", text: "t", score: 0.5 }],
    });

    const result = parseDownstreamFrame(
      JSON.stringify({
        type: "result",
        content: "x",
        session_id: "s",
        message_id: "m",
        grounded: false,
        rewritten_query: "rq",
        usage: { prompt_tokens: 1, completion_tokens: 2 },
        trace_id: "t",
        cancelled: true,
      }),
    );
    expect(result).toMatchObject({
      type: "result",
      grounded: false,
      cancelled: true,
      rewritten_query: "rq",
      usage: { prompt_tokens: 1, completion_tokens: 2, total_tokens: 3 },
    });
  });

  it("treats a missing grounded flag as grounded", () => {
    expect(parseDownstreamFrame('{"type":"result","content":"x","session_id":"s"}')).toMatchObject({ grounded: true });
  });

  it("parses the routing object and actual mode of an auto turn (MR4)", () => {
    const result = parseDownstreamFrame(
      JSON.stringify({
        type: "result",
        content: "x",
        session_id: "s",
        grounded: true,
        retrieval: "agentic_fast",
        routing: {
          requested: "auto",
          decided: "agentic_fast",
          candidates: ["hybrid", "agentic_fast", "agentic_deep"],
          backend: "cloudflare_workers_ai",
          model: "clef-flash",
          confidence: 0.74,
          probabilities: { hybrid: 0.2, agentic_fast: 0.74, agentic_deep: 0.06, junk: "x" },
          fallback_reason: null,
          input_tokens: 281,
          elapsed_ms: 402.1,
        },
      }),
    );
    expect(result).toMatchObject({
      type: "result",
      retrieval: "agentic_fast",
      routing: {
        requested: "auto",
        decided: "agentic_fast",
        backend: "cloudflare_workers_ai",
        model: "clef-flash",
        confidence: 0.74,
        fallback_reason: null,
        input_tokens: 281,
        elapsed_ms: 402.1,
      },
    });
    // Unknown probability keys are dropped.
    expect(result?.type === "result" && result.routing?.probabilities).toEqual({
      hybrid: 0.2,
      agentic_fast: 0.74,
      agentic_deep: 0.06,
    });
  });

  it("parses a fallback routing object and omits routing on plain turns (MR4)", () => {
    const fallback = parseDownstreamFrame(
      JSON.stringify({
        type: "result",
        content: "x",
        session_id: "s",
        retrieval: "hybrid",
        routing: { requested: "auto", decided: null, fallback_reason: "timeout" },
      }),
    );
    expect(fallback).toMatchObject({ retrieval: "hybrid", routing: { decided: null, fallback_reason: "timeout" } });

    // Non-auto turns carry no routing object.
    const plain = parseDownstreamFrame(JSON.stringify({ type: "result", content: "x", session_id: "s", retrieval: "hybrid" }));
    expect(plain).toMatchObject({ retrieval: "hybrid" });
    expect(plain?.type === "result" && plain.routing).toBeUndefined();
  });

  it("accepts the routing status stage (MR4)", () => {
    expect(parseDownstreamFrame('{"type":"status","stage":"routing","message":"正在选择检索模式"}')).toEqual({
      type: "status",
      stage: "routing",
      message: "正在选择检索模式",
    });
  });

  it("drops invalid JSON, non-objects, unknown types and incomplete frames", () => {
    expect(parseDownstreamFrame("not json")).toBeNull();
    expect(parseDownstreamFrame("[1,2]")).toBeNull();
    expect(parseDownstreamFrame('"str"')).toBeNull();
    expect(parseDownstreamFrame('{"type":"mystery"}')).toBeNull();
    expect(parseDownstreamFrame('{"type":"status","stage":"nope"}')).toBeNull();
    expect(parseDownstreamFrame('{"type":"stream"}')).toBeNull();
    expect(parseDownstreamFrame('{"type":"session"}')).toBeNull();
  });
});

describe("normalizeCitations", () => {
  it("falls back to the position for a missing id and null for a missing score", () => {
    const [citation] = normalizeCitations([{ source: "x.md", text: "t" }]);
    expect(citation).toMatchObject({ id: 1, source: "x.md", page: null, score: null, chunk_id: "" });
  });

  it("passes longdoc location fields through and drops ill-typed ones (PI6)", () => {
    const [good] = normalizeCitations([
      { source: "a.md", text: "t", section: "2 架构 > 2.1 模块", line_start: 12, line_end: 45 },
    ]);
    expect(good).toEqual({
      id: 1,
      source: "a.md",
      page: null,
      chunk_id: "",
      text: "t",
      score: null,
      section: "2 架构 > 2.1 模块",
      line_start: 12,
      line_end: 45,
    });

    // Missing keys stay absent (backend omits null fields entirely).
    const [bare] = normalizeCitations([{ id: 2, source: "b.pdf", page: 3, chunk_id: "c", text: "t", score: 0.5 }]);
    expect(bare).toEqual({ id: 2, source: "b.pdf", page: 3, chunk_id: "c", text: "t", score: 0.5 });

    // Ill-typed values are dropped, not coerced.
    const [bad] = normalizeCitations([{ source: "c.md", text: "t", section: 42, line_start: "12", line_end: null }]);
    expect(bad).not.toHaveProperty("section");
    expect(bad).not.toHaveProperty("line_start");
    expect(bad).not.toHaveProperty("line_end");
  });

  it("returns [] for non-arrays", () => {
    expect(normalizeCitations(undefined)).toEqual([]);
    expect(normalizeCitations({})).toEqual([]);
  });
});

describe("normalizeCapabilities", () => {
  it("maps the backend shape", () => {
    expect(
      normalizeCapabilities({
        enabled: true,
        retrievals: ["hybrid", "agentic_fast", "bogus"],
        default_retrieval: "agentic_fast",
        agentic_enabled: true,
        allow_deep: false,
        progress_stream: true,
        max_connections: 100,
      }),
    ).toEqual({
      enabled: true,
      retrievals: ["hybrid", "agentic_fast"],
      default_retrieval: "agentic_fast",
      auto_routing: false,
      agentic_enabled: true,
      allow_deep: false,
      longdoc_enabled: false,
      progress_stream: true,
      max_connections: 100,
    });
  });

  it("defaults safely on garbage", () => {
    expect(normalizeCapabilities(null)).toMatchObject({
      retrievals: ["hybrid"],
      default_retrieval: "hybrid",
      auto_routing: false,
      agentic_enabled: false,
      allow_deep: false,
    });
  });

  it("honors default_retrieval auto only with auto_routing (MR4)", () => {
    const withAuto = normalizeCapabilities({ default_retrieval: "auto", auto_routing: true });
    expect(withAuto.default_retrieval).toBe("auto");
    expect(withAuto.auto_routing).toBe(true);

    // Without auto_routing an auto default silently falls back to hybrid.
    expect(normalizeCapabilities({ default_retrieval: "auto" }).default_retrieval).toBe("hybrid");
    expect(normalizeCapabilities({ default_retrieval: "auto", auto_routing: false }).default_retrieval).toBe("hybrid");
  });

  it("honors default_retrieval longdoc only when enabled and served (PI6)", () => {
    const longdoc = normalizeCapabilities({
      default_retrieval: "longdoc",
      longdoc_enabled: true,
      retrievals: ["hybrid", "longdoc"],
    });
    expect(longdoc.default_retrieval).toBe("longdoc");
    expect(longdoc.longdoc_enabled).toBe(true);

    // Older server (no longdoc_enabled field) or longdoc not served: fall back to hybrid.
    expect(normalizeCapabilities({ default_retrieval: "longdoc" }).default_retrieval).toBe("hybrid");
    expect(
      normalizeCapabilities({ default_retrieval: "longdoc", longdoc_enabled: true, retrievals: ["hybrid"] })
        .default_retrieval,
    ).toBe("hybrid");
    expect(
      normalizeCapabilities({ default_retrieval: "longdoc", longdoc_enabled: false, retrievals: ["hybrid", "longdoc"] })
        .default_retrieval,
    ).toBe("hybrid");
  });
});

describe("sessions", () => {
  it("normalizes a page of summaries and skips malformed rows", () => {
    const sessions = normalizeSessionPage({
      items: [{ session_id: "s1", title: "T", created_at: "a", updated_at: "b", message_count: 4 }, { title: "no id" }],
      total: 2,
    });
    expect(sessions).toEqual([{ session_id: "s1", title: "T", created_at: "a", updated_at: "b", message_count: 4 }]);
  });

  it("normalizes a detail with messages", () => {
    const detail = normalizeSessionDetail({
      session_id: "s1",
      title: "T",
      created_at: "",
      updated_at: "",
      message_count: 1,
      messages: [{ id: "m1", role: "assistant", content: "x", sources: [{ id: 1 }], metadata: { grounded: true } }],
    });
    expect(detail?.messages[0]).toMatchObject({ id: "m1", session_id: "s1", metadata: { grounded: true } });
    expect(normalizeSessionDetail({})).toBeNull();
  });
});
