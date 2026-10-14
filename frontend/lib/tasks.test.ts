import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import {
  fetchTasks,
  followTask,
  normalizeTask,
  progressPercent,
  type SocketLike,
  type TaskRecord,
} from "./tasks";

class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = [];
  onopen: SocketLike["onopen"] = null;
  onmessage: SocketLike["onmessage"] = null;
  onerror: SocketLike["onerror"] = null;
  onclose: SocketLike["onclose"] = null;
  closed = false;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.();
  }
  send(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

function frame(version: number, status: string, detail: Record<string, unknown> = {}) {
  return { type: "task_event", task_id: "t1", status, detail, timestamp: "2026-01-01T00:00:00Z", version };
}

function record(version: number, status: TaskRecord["status"], detail: Record<string, unknown> = {}): TaskRecord {
  return normalizeTask({ task_id: "t1", kind: "ingestion", status, version, detail });
}

const socketFactory = (url: string) => new FakeSocket(url);

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
  vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
  window.localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("normalizeTask", () => {
  it("maps a REST snapshot", () => {
    const task = normalizeTask({
      task_id: "t1",
      kind: "ingestion",
      status: "running",
      version: 3,
      detail: { params: { files: ["a.pdf"] }, progress: { stage: "embed", current: 2, total: 4 } },
      created_at: "c",
      updated_at: "u",
    });
    expect(task.status).toBe("running");
    expect(task.version).toBe(3);
    expect(task.detail.progress).toEqual({ stage: "embed", current: 2, total: 4 });
    expect(task.detail.params).toEqual({ files: ["a.pdf"] });
    expect(task.detail.result).toBeNull();
  });

  it("maps a WS frame, taking updated_at from timestamp", () => {
    const task = normalizeTask(frame(5, "failed", { error: { type: "ValueError", message: "bad" } }));
    expect(task.updated_at).toBe("2026-01-01T00:00:00Z");
    expect(task.kind).toBeNull();
    expect(task.detail.error).toEqual({ type: "ValueError", message: "bad" });
  });

  it("is defensive about junk", () => {
    const task = normalizeTask(null);
    expect(task.status).toBe("pending");
    expect(task.version).toBe(-1);
    expect(task.detail.progress).toBeNull();
  });
});

describe("progressPercent", () => {
  it("clamps and tolerates unknown totals", () => {
    expect(progressPercent(null)).toBe(0);
    expect(progressPercent({ stage: "x", current: 1, total: 0 })).toBe(0);
    expect(progressPercent({ stage: "x", current: 1, total: 4 })).toBe(25);
    expect(progressPercent({ stage: "x", current: 9, total: 4 })).toBe(100);
  });
});

describe("followTask over WebSocket", () => {
  it("connects to the task events URL and streams updates until terminal", async () => {
    const updates: TaskRecord[] = [];
    const transports: string[] = [];
    const follower = followTask("t1", {
      onUpdate: (t) => updates.push(t),
      onTransportChange: (t) => transports.push(t),
      createSocket: socketFactory,
      fetchSnapshot: vi.fn(),
    });
    const socket = FakeSocket.instances[0];
    expect(socket.url).toBe(`ws://${window.location.host}/api/v1/tasks/t1/events`);
    socket.open();
    socket.send(frame(1, "running", { progress: { stage: "load", current: 0, total: 2 } }));
    socket.send(frame(2, "running", { progress: { stage: "load", current: 1, total: 2 } }));
    socket.send(frame(3, "succeeded", { result: { files: 1 } }));

    const final = await follower.done;
    expect(final?.status).toBe("succeeded");
    expect(updates.map((u) => u.version)).toEqual([1, 2, 3]);
    expect(transports).toEqual(["websocket"]);
    expect(socket.closed).toBe(true);
  });

  it("appends the stored token to the WS URL", () => {
    window.localStorage.setItem("rag_api_token", "s3cret");
    followTask("t1", { onUpdate: vi.fn(), createSocket: socketFactory, fetchSnapshot: vi.fn() });
    expect(FakeSocket.instances[0].url).toContain("?token=s3cret");
  });

  it("drops frames that are not newer than the last seen version", async () => {
    const updates: number[] = [];
    const follower = followTask("t1", {
      onUpdate: (t) => updates.push(t.version),
      createSocket: socketFactory,
      fetchSnapshot: vi.fn(),
    });
    const socket = FakeSocket.instances[0];
    socket.send(frame(2, "running"));
    socket.send(frame(2, "running"));
    socket.send(frame(1, "pending"));
    socket.send(frame(3, "failed", { error: { type: "X", message: "boom" } }));
    const final = await follower.done;
    expect(updates).toEqual([2, 3]);
    expect(final?.detail.error?.message).toBe("boom");
  });

  it("ignores malformed frames", async () => {
    const updates: number[] = [];
    const follower = followTask("t1", {
      onUpdate: (t) => updates.push(t.version),
      createSocket: socketFactory,
      fetchSnapshot: vi.fn(),
    });
    const socket = FakeSocket.instances[0];
    socket.onmessage?.({ data: "not json" });
    socket.send({ type: "something_else" });
    socket.send(frame(1, "succeeded"));
    await follower.done;
    expect(updates).toEqual([1]);
  });

  it("stop() closes the socket and resolves null without polling", async () => {
    const fetchSnapshot = vi.fn();
    const follower = followTask("t1", { onUpdate: vi.fn(), createSocket: socketFactory, fetchSnapshot });
    follower.stop();
    expect(await follower.done).toBeNull();
    expect(FakeSocket.instances[0].closed).toBe(true);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchSnapshot).not.toHaveBeenCalled();
  });
});

describe("followTask polling fallback", () => {
  it("falls back to polling when the socket errors, de-duplicating by version", async () => {
    const snapshots = [record(1, "running"), record(1, "running"), record(2, "running"), record(3, "succeeded")];
    const fetchSnapshot = vi.fn(async () => snapshots.shift() as TaskRecord);
    const updates: number[] = [];
    const transports: string[] = [];
    const follower = followTask("t1", {
      onUpdate: (t) => updates.push(t.version),
      onTransportChange: (t) => transports.push(t),
      createSocket: socketFactory,
      fetchSnapshot,
      pollIntervalMs: 100,
    });
    FakeSocket.instances[0].onerror?.();
    await vi.advanceTimersByTimeAsync(1000);
    const final = await follower.done;
    expect(final?.status).toBe("succeeded");
    expect(updates).toEqual([1, 2, 3]);
    expect(transports).toEqual(["websocket", "polling"]);
    expect(fetchSnapshot).toHaveBeenCalledTimes(4);
  });

  it("switches to polling when the socket closes before the task is terminal", async () => {
    const fetchSnapshot = vi.fn(async () => record(4, "succeeded"));
    const updates: number[] = [];
    const follower = followTask("t1", {
      onUpdate: (t) => updates.push(t.version),
      createSocket: socketFactory,
      fetchSnapshot,
      pollIntervalMs: 100,
    });
    const socket = FakeSocket.instances[0];
    socket.open();
    socket.send(frame(2, "running"));
    socket.onclose?.();
    await vi.advanceTimersByTimeAsync(10);
    await follower.done;
    expect(updates).toEqual([2, 4]);
    expect(socket.closed).toBe(true);
    expect(fetchSnapshot).toHaveBeenCalledTimes(1);
  });

  it("does not poll after a clean terminal close", async () => {
    const fetchSnapshot = vi.fn();
    const follower = followTask("t1", { onUpdate: vi.fn(), createSocket: socketFactory, fetchSnapshot });
    const socket = FakeSocket.instances[0];
    socket.send(frame(1, "succeeded"));
    socket.onclose?.();
    await follower.done;
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchSnapshot).not.toHaveBeenCalled();
  });

  it("abandons a socket that never opens", async () => {
    const fetchSnapshot = vi.fn(async () => record(1, "succeeded"));
    const follower = followTask("t1", {
      onUpdate: vi.fn(),
      createSocket: socketFactory,
      fetchSnapshot,
      connectTimeoutMs: 500,
    });
    await vi.advanceTimersByTimeAsync(600);
    await follower.done;
    expect(FakeSocket.instances[0].closed).toBe(true);
    expect(fetchSnapshot).toHaveBeenCalledTimes(1);
  });

  it("does not time out an opened socket", async () => {
    const fetchSnapshot = vi.fn();
    followTask("t1", { onUpdate: vi.fn(), createSocket: socketFactory, fetchSnapshot, connectTimeoutMs: 500 });
    FakeSocket.instances[0].open();
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchSnapshot).not.toHaveBeenCalled();
  });

  it("falls back to polling when creating the socket throws", async () => {
    const fetchSnapshot = vi.fn(async () => record(1, "succeeded"));
    const follower = followTask("t1", {
      onUpdate: vi.fn(),
      createSocket: () => {
        throw new Error("no WebSocket");
      },
      fetchSnapshot,
    });
    expect((await follower.done)?.status).toBe("succeeded");
  });

  it("polls on a WS error frame (e.g. not_found) so REST reports the authoritative error", async () => {
    const fetchSnapshot = vi.fn(async () => {
      throw new ApiError(404, "not_found", "Task 't1' not found");
    });
    const follower = followTask("t1", { onUpdate: vi.fn(), createSocket: socketFactory, fetchSnapshot });
    FakeSocket.instances[0].send({ type: "error", code: "not_found", message: "nope" });
    await expect(follower.done).rejects.toMatchObject({ status: 404, code: "not_found" });
  });

  it("fails fast on 401 while polling", async () => {
    const fetchSnapshot = vi.fn(async () => {
      throw new ApiError(401, "unauthorized", "Invalid or missing token");
    });
    const follower = followTask("t1", { onUpdate: vi.fn(), createSocket: socketFactory, fetchSnapshot });
    FakeSocket.instances[0].onclose?.();
    await expect(follower.done).rejects.toMatchObject({ status: 401, isUnauthorized: true });
    expect(fetchSnapshot).toHaveBeenCalledTimes(1);
  });

  it("retries transient poll failures and gives up after maxPollFailures", async () => {
    const fetchSnapshot = vi.fn(async () => {
      throw new TypeError("network down");
    });
    const follower = followTask("t1", {
      onUpdate: vi.fn(),
      createSocket: socketFactory,
      fetchSnapshot,
      pollIntervalMs: 50,
      maxPollFailures: 3,
    });
    const rejected = expect(follower.done).rejects.toMatchObject({ code: "network_error" });
    FakeSocket.instances[0].onerror?.();
    await vi.advanceTimersByTimeAsync(1000);
    await rejected;
    expect(fetchSnapshot).toHaveBeenCalledTimes(3);
  });

  it("recovers after a transient poll failure", async () => {
    let call = 0;
    const fetchSnapshot = vi.fn(async () => {
      call += 1;
      if (call === 1) throw new TypeError("blip");
      return record(2, "succeeded");
    });
    const follower = followTask("t1", {
      onUpdate: vi.fn(),
      createSocket: socketFactory,
      fetchSnapshot,
      pollIntervalMs: 50,
    });
    FakeSocket.instances[0].onerror?.();
    await vi.advanceTimersByTimeAsync(500);
    expect((await follower.done)?.version).toBe(2);
  });

  it("stop() halts polling", async () => {
    const fetchSnapshot = vi.fn(async () => record(1, "running"));
    const follower = followTask("t1", {
      onUpdate: vi.fn(),
      createSocket: socketFactory,
      fetchSnapshot,
      pollIntervalMs: 100,
    });
    FakeSocket.instances[0].onerror?.();
    await vi.advanceTimersByTimeAsync(250);
    const calls = fetchSnapshot.mock.calls.length;
    follower.stop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchSnapshot.mock.calls.length).toBe(calls);
    expect(await follower.done).toBeNull();
  });

  it("polls through the default fetch path with the auth header", async () => {
    window.localStorage.setItem("rag_api_token", "tok");
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ task_id: "t1", kind: "ingestion", status: "succeeded", version: 9, detail: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const follower = followTask("t1", { onUpdate: vi.fn(), createSocket: socketFactory });
    FakeSocket.instances[0].onerror?.();
    expect((await follower.done)?.version).toBe(9);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/tasks/t1");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });
});

describe("fetchTasks", () => {
  it("builds the kind/limit query and normalizes the page", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          items: [{ task_id: "a", kind: "evaluation", status: "failed", version: 2, detail: { error: { type: "E", message: "m" } } }],
          total: 1,
          limit: 5,
          offset: 0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const page = await fetchTasks({ kind: "evaluation", limit: 5 });
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/v1/tasks?kind=evaluation&limit=5");
    expect(page.total).toBe(1);
    expect(page.items[0].detail.error?.message).toBe("m");
  });
});
