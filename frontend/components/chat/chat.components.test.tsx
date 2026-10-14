import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider } from "@/context/ThemeContext";
import type { ChatCapabilities, ChatMessage, Citation } from "@/lib/chat/types";
import Composer from "./Composer";
import MarkdownContent from "./MarkdownContent";
import MessageBubble, { UNGROUNDED_NOTICE } from "./MessageBubble";
import ModeSelector from "./ModeSelector";

const mermaidMock = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(),
}));
vi.mock("mermaid", () => ({ default: mermaidMock }));

afterEach(() => {
  cleanup();
  mermaidMock.initialize.mockReset();
  mermaidMock.render.mockReset();
});

const CITATIONS: Citation[] = [
  { id: 1, source: "docs/alpha.pdf", page: 3, chunk_id: "chunk-a", text: "alpha snippet", score: 0.9123 },
  { id: 2, source: "docs/beta.md", page: null, chunk_id: "chunk-b", text: "beta snippet", score: 0.5 },
];

function assistant(over: Partial<ChatMessage> = {}): ChatMessage {
  return { id: "a1", role: "assistant", content: "", status: "done", citations: [], logs: [], ...over };
}

function renderMarkdown(content: string, extra: Partial<React.ComponentProps<typeof MarkdownContent>> = {}) {
  return render(
    <ThemeProvider>
      <MarkdownContent content={content} streaming={false} citations={[]} {...extra} />
    </ThemeProvider>,
  );
}

describe("MarkdownContent", () => {
  it("renders GFM tables and code blocks", () => {
    const { container } = renderMarkdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n```js\nconst x = 1;\n```");
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelector("pre code")?.textContent).toContain("const x = 1;");
  });

  it("renders KaTeX math", () => {
    const { container } = renderMarkdown("Euler: $e^{i\\pi}+1=0$\n\n$$\\int_0^1 x\\,dx$$");
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(2);
  });

  it("does not render raw HTML", () => {
    const { container } = renderMarkdown('<script>window.__x = 1</script><img src="x" onerror="1"><b>bold</b>');
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("drops markdown images and neutralises javascript: links", () => {
    const { container } = renderMarkdown("![t](http://evil/x.png) [click](javascript:alert(1))");
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")?.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
  });

  it("opens external links safely", () => {
    const { container } = renderMarkdown("[x](https://example.com)");
    const link = container.querySelector("a");
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(link?.getAttribute("rel")).toContain("noopener");
  });

  it("tolerates partial markdown while streaming (open fence is closed)", async () => {
    const { container } = render(
      <ThemeProvider>
        <MarkdownContent content={"intro\n```py\nprint(1)"} streaming citations={[]} />
      </ThemeProvider>,
    );
    await waitFor(() => expect(container.querySelector("pre code")?.textContent).toContain("print(1)"));
  });

  it("applies the latest streamed content", async () => {
    const view = render(
      <ThemeProvider>
        <MarkdownContent content="first" streaming citations={[]} />
      </ThemeProvider>,
    );
    await waitFor(() => expect(view.container.textContent).toContain("first"));
    view.rerender(
      <ThemeProvider>
        <MarkdownContent content="first second" streaming citations={[]} />
      </ThemeProvider>,
    );
    await waitFor(() => expect(view.container.textContent).toContain("first second"));
  });

  it("renders a mermaid block as a diagram once final", async () => {
    mermaidMock.render.mockResolvedValue({ svg: '<svg data-testid="diagram"></svg>' });
    renderMarkdown("```mermaid\ngraph TD; A-->B\n```");
    await waitFor(() => expect(screen.getByRole("img", { name: "Mermaid diagram" })).toBeTruthy());
    expect(mermaidMock.render).toHaveBeenCalledWith(expect.any(String), "graph TD; A-->B");
  });

  it("falls back to the source code when mermaid fails", async () => {
    mermaidMock.render.mockRejectedValue(new Error("parse error"));
    const { container } = renderMarkdown("```mermaid\nnot a diagram\n```");
    await waitFor(() => expect(container.querySelector("[data-mermaid-fallback]")).not.toBeNull());
    expect(container.textContent).toContain("not a diagram");
  });

  it("does not load mermaid for a streaming mermaid block or ordinary code", async () => {
    render(
      <ThemeProvider>
        <MarkdownContent content={"```mermaid\ngraph TD; A-->B\n```\n\n```js\nx\n```"} streaming citations={[]} />
      </ThemeProvider>,
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mermaidMock.render).not.toHaveBeenCalled();
  });

  it("turns valid [n] markers into buttons and ignores out-of-range ones", () => {
    const onClick = vi.fn();
    renderMarkdown("Fact [2]. Bogus [7].", { citations: CITATIONS, onCitationClick: onClick });
    const button = screen.getByRole("button", { name: "2" });
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledWith(2);
    expect(screen.queryByRole("button", { name: "7" })).toBeNull();
    expect(screen.getByText(/Bogus \[7\]/)).toBeTruthy();
  });
});

describe("MessageBubble", () => {
  it("shows a prominent banner when the answer is not grounded", () => {
    render(
      <ThemeProvider>
        <MessageBubble message={assistant({ content: "chatty", grounded: false })} />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("ungrounded-banner").textContent).toContain(UNGROUNDED_NOTICE);
  });

  it("shows no banner for grounded or unknown answers", () => {
    const { rerender } = render(
      <ThemeProvider>
        <MessageBubble message={assistant({ content: "a", grounded: true })} />
      </ThemeProvider>,
    );
    expect(screen.queryByTestId("ungrounded-banner")).toBeNull();
    rerender(
      <ThemeProvider>
        <MessageBubble message={assistant({ content: "a" })} />
      </ThemeProvider>,
    );
    expect(screen.queryByTestId("ungrounded-banner")).toBeNull();
  });

  it("renders sources with score, file and chunk id, highlighted by a marker click", () => {
    render(
      <ThemeProvider>
        <MessageBubble message={assistant({ content: "See [1] and [2].", citations: CITATIONS })} />
      </ThemeProvider>,
    );
    expect(screen.queryByText("alpha snippet")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "2" }));

    const sources = screen.getByRole("region", { name: "Sources" });
    expect(within(sources).getByText("alpha.pdf p.3")).toBeTruthy();
    expect(within(sources).getByText("score 0.912")).toBeTruthy();
    expect(within(sources).getByText("chunk chunk-a")).toBeTruthy();
    const items = within(sources).getAllByRole("listitem");
    expect(items[0].getAttribute("data-highlighted")).toBeNull();
    expect(items[1].getAttribute("data-highlighted")).toBe("true");
  });

  it("shows the rewritten query and token usage once finished", () => {
    render(
      <ThemeProvider>
        <MessageBubble
          message={assistant({ content: "a", rewrittenQuery: "rag overview", usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } })}
        />
      </ThemeProvider>,
    );
    expect(screen.getByText(/Searched as: rag overview/)).toBeTruthy();
    expect(screen.getByText(/15 tokens/)).toBeTruthy();
  });

  it("shows the stage while streaming and the collapsible search log", () => {
    render(
      <ThemeProvider>
        <MessageBubble
          message={assistant({
            status: "streaming",
            stage: "retrieving",
            stageMessage: "Searching docs",
            logs: [{ level: "info", message: "opened index" }],
          })}
        />
      </ThemeProvider>,
    );
    expect(screen.getByRole("status").textContent).toContain("Searching docs");
    expect(screen.queryByText("opened index")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Search log \(1\)/ }));
    expect(screen.getByText("opened index")).toBeTruthy();
  });

  it("flags a stopped answer as partial", () => {
    render(
      <ThemeProvider>
        <MessageBubble message={assistant({ content: "part", status: "cancelled" })} />
      </ThemeProvider>,
    );
    expect(screen.getByText(/answer above is partial/)).toBeTruthy();
  });
});

describe("Composer", () => {
  it("sends on Enter and clears the box", () => {
    const onSend = vi.fn(() => true);
    render(<Composer busy={false} onSend={onSend} onStop={() => undefined} />);
    const box = screen.getByLabelText("Message");
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello");
    expect((box as HTMLTextAreaElement).value).toBe("");
  });

  it("does not send on Shift+Enter or while composing (IME)", () => {
    const onSend = vi.fn(() => true);
    render(<Composer busy={false} onSend={onSend} onStop={() => undefined} />);
    const box = screen.getByLabelText("Message");
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("ignores empty input", () => {
    const onSend = vi.fn(() => true);
    render(<Composer busy={false} onSend={onSend} onStop={() => undefined} />);
    const box = screen.getByLabelText("Message");
    fireEvent.change(box, { target: { value: "   " } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows Stop instead of Send while busy and cancels on click", () => {
    const onStop = vi.fn();
    const onSend = vi.fn(() => true);
    render(<Composer busy onSend={onSend} onStop={onStop} />);
    expect(screen.queryByRole("button", { name: "Send message" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(onStop).toHaveBeenCalled();
    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });
});

describe("ModeSelector", () => {
  const caps: ChatCapabilities = {
    enabled: true,
    retrievals: ["hybrid", "agentic_fast", "agentic_deep"],
    default_retrieval: "hybrid",
    agentic_enabled: true,
    allow_deep: false,
    progress_stream: true,
    max_connections: 10,
  };

  it("disables DEEP with a reason and reports selection changes", () => {
    const onChange = vi.fn();
    render(<ModeSelector capabilities={caps} value="hybrid" onChange={onChange} />);
    const deep = screen.getByRole("radio", { name: /Agentic \(deep\)/ }) as HTMLButtonElement;
    expect(deep.disabled).toBe(true);
    expect(deep.title).toMatch(/allow_deep/);
    fireEvent.click(screen.getByRole("radio", { name: /Agentic \(fast\)/ }));
    expect(onChange).toHaveBeenCalledWith("agentic_fast");
  });

  it("collapses to a label when only hybrid is available", () => {
    render(<ModeSelector capabilities={{ ...caps, agentic_enabled: false }} value="hybrid" onChange={() => undefined} />);
    expect(screen.queryByRole("radio")).toBeNull();
  });
});
