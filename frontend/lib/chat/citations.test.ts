import { describe, expect, it } from "vitest";
import {
  citationLabel,
  citationNumberFromHref,
  findCitation,
  formatScore,
  linkCitationMarkers,
  parseCitationMarkers,
  splitProtected,
} from "./citations";

describe("parseCitationMarkers", () => {
  it("maps [n] to a zero-based source index in order", () => {
    expect(parseCitationMarkers("a [2] b [1] c [2]", 3)).toEqual([
      { n: 2, index: 1 },
      { n: 1, index: 0 },
      { n: 2, index: 1 },
    ]);
  });

  it("ignores out-of-range numbers, including 0", () => {
    expect(parseCitationMarkers("[0] [4] [99] [3]", 3)).toEqual([{ n: 3, index: 2 }]);
  });

  it("finds adjacent markers", () => {
    expect(parseCitationMarkers("claim [1][2]", 2).map((m) => m.n)).toEqual([1, 2]);
  });

  it("ignores markers inside code, links and definitions", () => {
    const text = ["`x[1]`", "[1](http://e.com)", "[1]: http://e.com", "```", "arr[1]", "```", "real [2]"].join("\n");
    expect(parseCitationMarkers(text, 2).map((m) => m.n)).toEqual([2]);
  });

  it("returns nothing when there are no sources", () => {
    expect(parseCitationMarkers("see [1]", 0)).toEqual([]);
  });
});

describe("linkCitationMarkers", () => {
  it("rewrites valid markers into cite links and leaves invalid ones", () => {
    expect(linkCitationMarkers("a [1] b [5]", 2)).toBe("a [\\[1\\]](#cite-1) b [5]");
  });

  it("does not touch fenced or inline code", () => {
    const text = "```js\nx[1]\n```\nand `y[1]` and [1]";
    expect(linkCitationMarkers(text, 1)).toBe("```js\nx[1]\n```\nand `y[1]` and [\\[1\\]](#cite-1)");
  });

  it("is the identity with no citations", () => {
    expect(linkCitationMarkers("[1]", 0)).toBe("[1]");
  });

  it("respects an unterminated fence (streaming)", () => {
    expect(linkCitationMarkers("```py\na[1]", 1)).toBe("```py\na[1]");
  });
});

describe("splitProtected", () => {
  it("round-trips the text", () => {
    const text = "a `b` c\n```\ncode\n```\nd";
    expect(splitProtected(text).map((s) => s.text).join("")).toBe(text);
  });
});

describe("helpers", () => {
  it("citationNumberFromHref", () => {
    expect(citationNumberFromHref("#cite-3")).toBe(3);
    expect(citationNumberFromHref("#cite-x")).toBeNull();
    expect(citationNumberFromHref("#cite-0")).toBeNull();
    expect(citationNumberFromHref("https://a")).toBeNull();
    expect(citationNumberFromHref(undefined)).toBeNull();
  });

  it("findCitation prefers id, then position", () => {
    const list = [
      { id: 5, source: "a", page: null, chunk_id: "", text: "", score: 1 },
      { id: 6, source: "b", page: null, chunk_id: "", text: "", score: 1 },
    ];
    expect(findCitation(list, 6)?.source).toBe("b");
    expect(findCitation(list, 2)?.source).toBe("b");
    expect(findCitation(list, 9)).toBeUndefined();
  });

  it("formatScore and citationLabel", () => {
    expect(formatScore(0.91234)).toBe("0.912");
    expect(formatScore(null)).toBe("n/a");
    expect(citationLabel({ id: 1, source: "data/docs/a.pdf", page: 4, chunk_id: "", text: "", score: 0 })).toBe("a.pdf p.4");
    expect(citationLabel({ id: 1, source: "C:\\docs\\b.md", page: null, chunk_id: "", text: "", score: 0 })).toBe("b.md");
  });
});
