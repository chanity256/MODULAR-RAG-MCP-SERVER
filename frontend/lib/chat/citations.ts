import type { Citation } from "./types";

export interface Segment {
  text: string;
  /** Code / math that must be left untouched by text rewriting. */
  protected: boolean;
}

const FENCE_OPEN = /^\s{0,3}(`{3,}|~{3,})/;
const INLINE_CODE = /`[^`\n]*`/g;

/** Split markdown into plain text and protected (fenced code, inline code) segments. */
export function splitProtected(text: string): Segment[] {
  const segments: Segment[] = [];
  const lines = text.split("\n");
  let fence: string | null = null;
  let buffer = "";
  let bufferProtected = false;

  const flush = () => {
    if (buffer !== "") segments.push({ text: buffer, protected: bufferProtected });
    buffer = "";
  };
  const push = (chunk: string, isProtected: boolean) => {
    if (chunk === "") return;
    if (buffer !== "" && bufferProtected !== isProtected) flush();
    bufferProtected = isProtected;
    buffer += chunk;
  };

  lines.forEach((line, index) => {
    const eol = index < lines.length - 1 ? "\n" : "";
    if (fence !== null) {
      push(line + eol, true);
      const close = line.trim();
      if (close.startsWith(fence) && close === close[0].repeat(close.length)) fence = null;
      return;
    }
    const open = FENCE_OPEN.exec(line);
    if (open) {
      fence = open[1];
      push(line + eol, true);
      return;
    }
    let cursor = 0;
    for (const match of line.matchAll(INLINE_CODE)) {
      push(line.slice(cursor, match.index), false);
      push(match[0], true);
      cursor = match.index + match[0].length;
    }
    push(line.slice(cursor) + eol, false);
  });
  flush();
  return segments;
}

// `[n]` that is not a link label (`[1](url)`), a definition (`[1]: ...`), or escaped/image.
const MARKER = /(?<![\\!])\[(\d{1,3})\](?!\(|:)/g;

export interface CitationMarker {
  /** 1-based citation number as written in the text. */
  n: number;
  /** 0-based index into the citations array. */
  index: number;
}

/** In-order `[n]` markers that point at an existing source; out-of-range numbers are ignored. */
export function parseCitationMarkers(text: string, citationCount: number): CitationMarker[] {
  const markers: CitationMarker[] = [];
  for (const segment of splitProtected(text)) {
    if (segment.protected) continue;
    for (const match of segment.text.matchAll(MARKER)) {
      const n = Number(match[1]);
      if (n >= 1 && n <= citationCount) markers.push({ n, index: n - 1 });
    }
  }
  return markers;
}

export const CITATION_HREF_PREFIX = "#cite-";

/** Rewrite valid `[n]` markers into links `[\[n\]](#cite-n)` that the renderer turns into source chips. */
export function linkCitationMarkers(text: string, citationCount: number): string {
  if (citationCount <= 0 || !text.includes("[")) return text;
  return splitProtected(text)
    .map((segment) =>
      segment.protected
        ? segment.text
        : segment.text.replace(MARKER, (whole, digits: string) => {
            const n = Number(digits);
            return n >= 1 && n <= citationCount ? `[\\[${n}\\]](${CITATION_HREF_PREFIX}${n})` : whole;
          }),
    )
    .join("");
}

/** `#cite-3` -> 3 (null for any other href). */
export function citationNumberFromHref(href: string | undefined): number | null {
  if (!href || !href.startsWith(CITATION_HREF_PREFIX)) return null;
  const n = Number(href.slice(CITATION_HREF_PREFIX.length));
  return Number.isInteger(n) && n >= 1 ? n : null;
}

/** Look up a citation by its displayed number, tolerating non-contiguous ids. */
export function findCitation(citations: readonly Citation[], n: number): Citation | undefined {
  return citations.find((c) => c.id === n) ?? citations[n - 1];
}

export function formatScore(score: number | null): string {
  return score === null ? "n/a" : score.toFixed(3);
}

export function citationLabel(citation: Citation): string {
  const name = citation.source.split(/[\\/]/).filter(Boolean).pop() ?? citation.source;
  return citation.page !== null ? `${name || "unknown"} p.${citation.page}` : name || "unknown";
}
