import { linkCitationMarkers } from "./citations";

const FENCE_LINE = /^\s{0,3}(`{3,}|~{3,})/;

/** True when the text ends inside a fenced code block that was never closed. */
export function hasOpenFence(text: string): boolean {
  let fence: string | null = null;
  for (const line of text.split("\n")) {
    if (fence === null) {
      const open = FENCE_LINE.exec(line);
      if (open) fence = open[1];
    } else {
      const close = line.trim();
      if (close.startsWith(fence) && close === close[0].repeat(close.length)) fence = null;
    }
  }
  return fence !== null;
}

/**
 * Make partially streamed markdown render stably: an unterminated fence is closed so the text
 * after it stays inside the code block instead of being parsed as prose until the closing fence
 * arrives (which would make the layout jump).
 */
export function closeOpenFence(text: string): string {
  if (!hasOpenFence(text)) return text;
  return `${text}${text.endsWith("\n") ? "" : "\n"}\`\`\``;
}

/** Strip a trailing half-written `[12` / `[` so a citation marker never flashes as literal text. */
export function trimPartialMarker(text: string): string {
  return text.replace(/\[\d{0,3}$/, "");
}

export interface PrepareOptions {
  streaming: boolean;
  citationCount: number;
}

export function prepareMarkdown(content: string, { streaming, citationCount }: PrepareOptions): string {
  let text = content;
  if (streaming) text = closeOpenFence(hasOpenFence(text) ? text : trimPartialMarker(text));
  return linkCitationMarkers(text, citationCount);
}

/** `language-mermaid` (react-markdown's code class) -> `mermaid`. */
export function fenceLanguage(className: string | undefined): string | null {
  const match = /(?:^|\s)language-([\w+-]+)/.exec(className ?? "");
  return match ? match[1].toLowerCase() : null;
}

/** Decide how a fenced block is rendered; mermaid only once the answer is final. */
export function codeBlockKind(language: string | null, streaming: boolean): "mermaid" | "code" {
  return language === "mermaid" && !streaming ? "mermaid" : "code";
}
