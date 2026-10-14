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

function stub(searchResponse: () => Promise<Response>) {
  const fetchMock = vi.fn((url: string) =>
    url.endsWith("/capabilities") ? Promise.resolve(json(CAPS)) : searchResponse(),
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
});
