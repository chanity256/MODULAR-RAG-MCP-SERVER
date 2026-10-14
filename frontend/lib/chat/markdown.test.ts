import { describe, expect, it } from "vitest";
import { closeOpenFence, codeBlockKind, fenceLanguage, hasOpenFence, prepareMarkdown, trimPartialMarker } from "./markdown";

describe("hasOpenFence / closeOpenFence", () => {
  it("detects an unterminated fence", () => {
    expect(hasOpenFence("text\n```js\nconst a = 1")).toBe(true);
    expect(hasOpenFence("```js\nx\n```")).toBe(false);
    expect(hasOpenFence("no code")).toBe(false);
  });

  it("handles tilde fences and longer closing fences", () => {
    expect(hasOpenFence("~~~\nx")).toBe(true);
    expect(hasOpenFence("```\nx\n````")).toBe(false);
    expect(hasOpenFence("````\n```\nx")).toBe(true);
  });

  it("closes a dangling fence, with or without a trailing newline", () => {
    expect(closeOpenFence("```py\nx = 1")).toBe("```py\nx = 1\n```");
    expect(closeOpenFence("```py\nx = 1\n")).toBe("```py\nx = 1\n```");
  });

  it("leaves balanced text untouched", () => {
    const text = "a\n```\nb\n```\nc";
    expect(closeOpenFence(text)).toBe(text);
  });
});

describe("trimPartialMarker", () => {
  it("removes a half-typed marker at the end only", () => {
    expect(trimPartialMarker("fact [1")).toBe("fact ");
    expect(trimPartialMarker("fact [")).toBe("fact ");
    expect(trimPartialMarker("fact [12")).toBe("fact ");
    expect(trimPartialMarker("fact [1] more")).toBe("fact [1] more");
    expect(trimPartialMarker("fact [1]")).toBe("fact [1]");
  });
});

describe("prepareMarkdown", () => {
  it("links markers and closes fences while streaming", () => {
    expect(prepareMarkdown("see [1]\n```js\nx", { streaming: true, citationCount: 1 })).toBe(
      "see [\\[1\\]](#cite-1)\n```js\nx\n```",
    );
  });

  it("hides a dangling marker only while streaming", () => {
    expect(prepareMarkdown("see [1", { streaming: true, citationCount: 2 })).toBe("see ");
    expect(prepareMarkdown("see [1", { streaming: false, citationCount: 2 })).toBe("see [1");
  });

  it("is a plain pass-through for final text without citations", () => {
    expect(prepareMarkdown("# T\n\nbody", { streaming: false, citationCount: 0 })).toBe("# T\n\nbody");
  });
});

describe("fenceLanguage / codeBlockKind", () => {
  it("extracts the language from react-markdown class names", () => {
    expect(fenceLanguage("language-mermaid")).toBe("mermaid");
    expect(fenceLanguage("foo language-TypeScript")).toBe("typescript");
    expect(fenceLanguage(undefined)).toBeNull();
    expect(fenceLanguage("hljs")).toBeNull();
  });

  it("renders mermaid only once the answer is complete", () => {
    expect(codeBlockKind("mermaid", false)).toBe("mermaid");
    expect(codeBlockKind("mermaid", true)).toBe("code");
    expect(codeBlockKind("js", false)).toBe("code");
    expect(codeBlockKind(null, false)).toBe("code");
  });
});
