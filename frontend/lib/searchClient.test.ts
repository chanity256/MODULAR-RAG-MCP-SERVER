import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_TOKEN_STORAGE_KEY } from "@/lib/api";
import { SEARCH_WS_PATH, openSearchStream } from "./searchClient";
import type { SearchRequestBody, SearchResult } from "./search";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  closeCalls = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.closeCalls += 1;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(frame: unknown) {
    this.onmessage?.({ data: typeof frame === "string" ? frame : JSON.stringify(frame) });
  }
  serverClose() {
    this.readyState = 3;
    this.onclose?.();
  }
}

function setup(body: SearchRequestBody = { mode: "deep", query: "q" }) {
  const logs: unknown[] = [];
  const results: SearchResult[] = [];
  const errors: { code: string; message: string }[] = [];
  const closed: { gotResult: boolean }[] = [];
  const stream = openSearchStream(
    body,
    {
      onLog: (entry) => logs.push(entry),
      onResult: (result) => results.push(result),
      onError: (code, message) => errors.push({ code, message }),
      onClosed: (info) => closed.push(info),
    },
    { WebSocketImpl: FakeWebSocket as unknown as new (url: string) => WebSocket },
  );
  return {
    stream,
    logs,
    results,
    errors,
    closed,
    socket: FakeWebSocket.instances[FakeWebSocket.instances.length - 1],
  };
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  window.localStorage.clear();
  vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("openSearchStream", () => {
  it("sends exactly the search body after the socket opens", () => {
    const { socket } = setup({ mode: "deep", query: "hello", top_k: 3 });
    expect(socket.sent).toEqual([]);
    socket.open();
    expect(socket.sent.map((s) => JSON.parse(s))).toEqual([{ mode: "deep", query: "hello", top_k: 3 }]);
  });

  it("targets the search ws endpoint and carries the stored token in the query", () => {
    window.localStorage.setItem(API_TOKEN_STORAGE_KEY, "s3cret/+");
    const { socket } = setup();
    expect(socket.url).toBe(`ws://${window.location.host}${SEARCH_WS_PATH}?token=s3cret%2F%2B`);
  });

  it("relays search_log frames and drops malformed or unknown ones", () => {
    const { socket, logs } = setup();
    socket.open();
    socket.receive({ type: "search_log", task_id: "t", level: "warning", message: "slow", timestamp: 1.5 });
    socket.receive("{broken");
    socket.receive({ type: "future", x: 1 });
    socket.receive({ type: "search_log", message: "plain" });
    socket.onmessage?.({ data: new ArrayBuffer(2) });
    expect(logs).toEqual([
      { level: "warning", message: "slow", timestamp: 1.5 },
      { level: "info", message: "plain", timestamp: undefined },
    ]);
  });

  it("normalizes the search_result frame like the REST response", () => {
    const { socket, results } = setup();
    socket.open();
    socket.receive({
      type: "search_result",
      mode: "deep",
      query: "q",
      elapsed_ms: 8,
      markdown: "answer",
      structured: { citations: [{ id: 1, source: "a.md", page: 2, score: 0.9, text: "t" }] },
      is_error: false,
      error: null,
    });
    expect(results).toEqual([
      {
        mode: "deep",
        query: "q",
        elapsed_ms: 8,
        markdown: "answer",
        citations: [{ id: 1, source: "a.md", page: 2, score: 0.9, text: "t" }],
        agentic: null,
        is_error: false,
        error: null,
      },
    ]);
  });

  it("reports error frames and keeps gotResult=false on close", () => {
    const { socket, errors, closed, results } = setup();
    socket.open();
    socket.receive({ type: "error", code: "UNAVAILABLE", message: "disabled" });
    socket.serverClose();
    expect(errors).toEqual([{ code: "UNAVAILABLE", message: "disabled" }]);
    expect(results).toEqual([]);
    expect(closed).toEqual([{ gotResult: false }]);
  });

  it("reports gotResult=true when a result preceded the close", () => {
    const { socket, closed } = setup();
    socket.open();
    socket.receive({ type: "search_result", mode: "deep", query: "q", elapsed_ms: 1, markdown: "m", structured: {} });
    socket.serverClose();
    expect(closed).toEqual([{ gotResult: true }]);
  });

  it("cancel sends a cancel frame only while the socket is open", () => {
    const { stream, socket } = setup();
    stream.cancel();
    expect(socket.sent).toEqual([]);
    socket.open();
    stream.cancel();
    expect(socket.sent.at(-1)).toBe(JSON.stringify({ type: "cancel" }));
    stream.cancel(); // 重复 cancel 不再发送
    expect(socket.sent).toHaveLength(2);
  });

  it("close detaches: server frames and close no longer reach the handlers", () => {
    const { stream, socket, closed } = setup();
    socket.open();
    stream.close();
    expect(socket.closeCalls).toBe(1);
    socket.receive({ type: "search_log", message: "late" });
    socket.serverClose();
    expect(closed).toEqual([]);
  });
});
