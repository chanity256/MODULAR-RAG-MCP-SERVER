import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_TOKEN_STORAGE_KEY } from "@/lib/api";
import { CHAT_WS_PATH, openChatTurn } from "./client";
import type { DownstreamFrame } from "./types";

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

function setup(payload = { message: "hi" } as Parameters<typeof openChatTurn>[0]) {
  const frames: DownstreamFrame[] = [];
  const closed: { terminal: boolean }[] = [];
  const turn = openChatTurn(
    payload,
    { onFrame: (f) => frames.push(f), onClosed: (i) => closed.push(i) },
    { WebSocketImpl: FakeWebSocket as unknown as new (url: string) => WebSocket },
  );
  return { turn, frames, closed, socket: FakeWebSocket.instances[FakeWebSocket.instances.length - 1] };
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  window.localStorage.clear();
  vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("openChatTurn", () => {
  it("sends exactly one user_message after the socket opens", () => {
    const { socket } = setup({ message: "hello", session_id: "s1", retrieval: "hybrid" });
    expect(socket.sent).toEqual([]);
    socket.open();
    expect(socket.sent.map((s) => JSON.parse(s))).toEqual([
      { type: "user_message", message: "hello", session_id: "s1", retrieval: "hybrid" },
    ]);
  });

  it("targets the chat endpoint and carries the stored token in the query", () => {
    window.localStorage.setItem(API_TOKEN_STORAGE_KEY, "s3cret/+");
    const { socket } = setup();
    expect(socket.url).toBe(`ws://${window.location.host}${CHAT_WS_PATH}?token=s3cret%2F%2B`);
  });

  it("omits the token when none is stored", () => {
    const { socket } = setup();
    expect(socket.url).toBe(`ws://${window.location.host}${CHAT_WS_PATH}`);
  });

  it("relays parsed frames and drops unknown or malformed ones", () => {
    const { socket, frames } = setup();
    socket.open();
    socket.receive({ type: "session", session_id: "s1" });
    socket.receive({ type: "future", x: 1 });
    socket.receive("{broken");
    socket.receive({ type: "stream", content: "a" });
    expect(frames.map((f) => f.type)).toEqual(["session", "stream"]);
  });

  it("ignores binary frames", () => {
    const { socket, frames } = setup();
    socket.open();
    socket.onmessage?.({ data: new ArrayBuffer(2) });
    expect(frames).toEqual([]);
  });

  it("reports terminal=true when a result preceded the close", () => {
    const { socket, closed } = setup();
    socket.open();
    socket.receive({ type: "result", content: "x", session_id: "s", grounded: true });
    socket.serverClose();
    expect(closed).toEqual([{ terminal: true }]);
  });

  it("reports an error frame as terminal too", () => {
    const { socket, closed } = setup();
    socket.open();
    socket.receive({ type: "error", code: "AUTH", message: "no" });
    socket.serverClose();
    expect(closed).toEqual([{ terminal: true }]);
  });

  it("reports terminal=false when the socket drops mid-turn", () => {
    const { socket, closed } = setup();
    socket.open();
    socket.receive({ type: "stream", content: "a" });
    socket.serverClose();
    expect(closed).toEqual([{ terminal: false }]);
  });

  it("reports a failed handshake (close without open) once", () => {
    const { socket, closed } = setup();
    socket.onerror?.();
    socket.serverClose();
    socket.serverClose();
    expect(closed).toEqual([{ terminal: false }]);
  });
});

describe("cancel", () => {
  it("sends a cancel frame while open, once", () => {
    const { turn, socket } = setup();
    socket.open();
    turn.cancel();
    turn.cancel();
    expect(socket.sent.map((s) => JSON.parse(s).type)).toEqual(["user_message", "cancel"]);
  });

  it("queues a cancel requested before the socket opens", () => {
    const { turn, socket } = setup();
    turn.cancel();
    expect(socket.sent).toEqual([]);
    socket.open();
    expect(socket.sent.map((s) => JSON.parse(s).type)).toEqual(["user_message", "cancel"]);
  });

  it("still delivers the cancelled result frame", () => {
    const { turn, socket, frames } = setup();
    socket.open();
    turn.cancel();
    socket.receive({ type: "result", content: "par", session_id: "s", grounded: true, cancelled: true });
    expect(frames[0]).toMatchObject({ type: "result", cancelled: true });
  });
});

describe("close", () => {
  it("closes the socket and silences all further callbacks", () => {
    const { turn, socket, frames, closed } = setup();
    socket.open();
    turn.close();
    expect(socket.closeCalls).toBe(1);
    socket.receive({ type: "stream", content: "late" });
    socket.serverClose();
    expect(frames).toEqual([]);
    expect(closed).toEqual([]);
  });

  it("does not send on a socket that opens after close()", () => {
    const { turn, socket } = setup();
    turn.close();
    socket.open();
    expect(socket.sent).toEqual([]);
  });
});
