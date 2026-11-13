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
      agentic_enabled: true,
      allow_deep: false,
      progress_stream: true,
      max_connections: 100,
    });
  });

  it("defaults safely on garbage", () => {
    expect(normalizeCapabilities(null)).toMatchObject({
      retrievals: ["hybrid"],
      default_retrieval: "hybrid",
      agentic_enabled: false,
      allow_deep: false,
    });
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
