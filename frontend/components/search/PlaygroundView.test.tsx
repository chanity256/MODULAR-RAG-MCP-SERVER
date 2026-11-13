import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider } from "@/context/ThemeContext";
import PlaygroundView from "./PlaygroundView";

vi.mock("mermaid", () => ({ default: { initialize: vi.fn(), render: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const CAPS = {
  modes: ["normal"],
  default_mode: "normal",
  agentic_enabled: false,
  allow_deep: false,
  availability: {
    normal: { available: true, reason: null },
    fast: { available: false, reason: "agentic search disabled" },
    deep: { available: false, reason: "deep disabled" },
  },
};

const DEEP_STREAM_CAPS = {
  modes: ["normal", "fast", "deep"],
  default_mode: "normal",
  agentic_enabled: true,
  allow_deep: true,
  availability: {
    normal: { available: true, reason: null },
    fast: { available: true, reason: null },
    deep: { available: true, reason: null },
  },
  progress_stream: true,
};

const DEEP_RESULT = {
  mode: "deep",
  query: "what is rag",
  elapsed_ms: 9,
  markdown: "deep answer",
  structured: { citations: [] },
  is_error: false,
  error: null,
};

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
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
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  serverClose() {
    this.readyState = 3;
    this.onclose?.();
  }
}

function stub(searchResponse: () => Promise<Response>, caps: unknown = CAPS) {
  const fetchMock = vi.fn((url: string) =>
    url.endsWith("/capabilities") ? Promise.resolve(json(caps)) : searchResponse(),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPage() {
  return render(
    <ThemeProvider>
      <PlaygroundView />
    </ThemeProvider>,
  );
}

async function submit(query = "what is rag") {
  await screen.findByText(/Fast unavailable: agentic search disabled/);
  fireEvent.change(screen.getByLabelText("Query"), { target: { value: query } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
}

describe("PlaygroundView", () => {
  it("disables unavailable modes and shows the reason", async () => {
    stub(() => new Promise(() => {}));
    renderPage();
    await screen.findByText(/Deep unavailable: deep disabled/);
    expect((screen.getByRole("button", { name: "Fast" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Normal" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows loading then the result with citations", async () => {
    let resolve!: (r: Response) => void;
    stub(() => new Promise<Response>((r) => (resolve = r)));
    renderPage();
    await submit();
    expect(await screen.findByText(/Running Normal search/)).toBeTruthy();
    resolve(
      json({
        mode: "normal",
        query: "what is rag",
        elapsed_ms: 42,
        markdown: "answer body",
        structured: { citations: [{ id: 1, source: "doc.md", page: 3, score: 0.75, text: "snippet text" }] },
        is_error: false,
        error: null,
      }),
    );
    expect(await screen.findByText("answer body")).toBeTruthy();
    expect(screen.getByText("doc.md")).toBeTruthy();
    expect(screen.getByText("score 0.7500")).toBeTruthy();
    expect(screen.getByText("42 ms")).toBeTruthy();
  });

  it("shows an error panel for is_error results", async () => {
    stub(() => Promise.resolve(json({ mode: "normal", query: "q", elapsed_ms: 1, markdown: "boom", structured: {}, is_error: true, error: "sidecar unreachable" })));
    renderPage();
    await submit();
    const alert = await screen.findByText("sidecar unreachable");
    expect(alert.closest("[role=alert]")).not.toBeNull();
  });

  it("shows HTTP errors", async () => {
    stub(() => Promise.resolve(json({ success: false, error: { code: "AGENTIC_DISABLED", message: "turn it on", details: null } }, 403)));
    renderPage();
    await submit();
    await waitFor(() => expect(screen.getByText("turn it on")).toBeTruthy());
    expect(screen.getByText("Agentic search is disabled")).toBeTruthy();
  });

  it("streams deep progress logs over WS when progress_stream is enabled", async () => {
    stub(() => Promise.reject(new Error("POST must not be used while streaming")), DEEP_STREAM_CAPS);
    vi.stubGlobal("WebSocket", FakeWebSocket);
    FakeWebSocket.instances = [];
    renderPage();

    await waitFor(() =>
      expect((screen.getByRole("button", { name: /^Deep/ }) as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByRole("button", { name: /^Deep/ }));
    fireEvent.change(screen.getByLabelText("Query"), { target: { value: "what is rag" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    expect(socket.sent).toEqual([]); // 结果帧由 onopen 之后的起始帧触发
    socket.open();
    expect(JSON.parse(socket.sent[0])).toEqual({ mode: "deep", query: "what is rag", top_k: 5 });

    socket.open();
    socket.receive({ type: "search_log", task_id: "t", level: "info", message: "planning search" });
    expect(await screen.findByText(/Search log \(1\)/)).toBeTruthy();
    expect(screen.getByText("planning search")).toBeTruthy();

    socket.receive({ type: "search_result", ...DEEP_RESULT });
    // 结果帧到达但连接未关：running 仍在（结果随关闭一起收尾）
    socket.serverClose();
    expect(await screen.findByText("deep answer")).toBeTruthy();
  });

  it("falls back to the synchronous POST when the socket closes without a result", async () => {
    const fetchMock = stub(() => Promise.resolve(json(DEEP_RESULT)), DEEP_STREAM_CAPS);
    vi.stubGlobal("WebSocket", FakeWebSocket);
    FakeWebSocket.instances = [];
    renderPage();

    await waitFor(() =>
      expect((screen.getByRole("button", { name: /^Deep/ }) as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByRole("button", { name: /^Deep/ }));
    fireEvent.change(screen.getByLabelText("Query"), { target: { value: "what is rag" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    socket.open();
    socket.serverClose();

    expect(await screen.findByText("deep answer")).toBeTruthy();
    expect(screen.getByText(/Live progress unavailable/)).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/v1/search"))).toBe(true);
  });

  it("shows the server error frame instead of falling back", async () => {
    stub(() => Promise.reject(new Error("POST must not be used")), DEEP_STREAM_CAPS);
    vi.stubGlobal("WebSocket", FakeWebSocket);
    FakeWebSocket.instances = [];
    renderPage();

    await waitFor(() =>
      expect((screen.getByRole("button", { name: /^Deep/ }) as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByRole("button", { name: /^Deep/ }));
    fireEvent.change(screen.getByLabelText("Query"), { target: { value: "what is rag" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    socket.open();
    socket.receive({ type: "error", code: "UNAVAILABLE", message: "stream disabled" });
    socket.serverClose();
    await waitFor(() => expect(screen.getByText("stream disabled")).toBeTruthy());
    expect(screen.queryByText(/Live progress unavailable/)).toBeNull();
  });
});
