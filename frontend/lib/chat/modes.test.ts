import { describe, expect, it } from "vitest";
import { buildModeOptions, resolveMode } from "./modes";
import type { ChatCapabilities } from "./types";

const caps = (over: Partial<ChatCapabilities> = {}): ChatCapabilities => ({
  enabled: true,
  retrievals: ["hybrid"],
  default_retrieval: "hybrid",
  agentic_enabled: false,
  allow_deep: false,
  progress_stream: false,
  max_connections: 100,
  ...over,
});

describe("buildModeOptions", () => {
  it("offers only hybrid before capabilities load", () => {
    expect(buildModeOptions(null).map((o) => o.mode)).toEqual(["hybrid"]);
  });

  it("hides agentic modes when the sidecar is disabled", () => {
    expect(buildModeOptions(caps()).map((o) => o.mode)).toEqual(["hybrid"]);
  });

  it("greys out DEEP with a reason when allow_deep is false", () => {
    const options = buildModeOptions(
      caps({ agentic_enabled: true, retrievals: ["hybrid", "agentic_fast", "agentic_deep"] }),
    );
    expect(options.map((o) => [o.mode, o.disabled])).toEqual([
      ["hybrid", false],
      ["agentic_fast", false],
      ["agentic_deep", true],
    ]);
    expect(options[2].reason).toMatch(/allow_deep/);
  });

  it("enables DEEP when allowed", () => {
    const options = buildModeOptions(
      caps({ agentic_enabled: true, allow_deep: true, retrievals: ["hybrid", "agentic_fast", "agentic_deep"] }),
    );
    expect(options.every((o) => !o.disabled)).toBe(true);
  });

  it("only lists modes the server says it serves", () => {
    const options = buildModeOptions(caps({ agentic_enabled: true, retrievals: ["hybrid", "agentic_fast"] }));
    expect(options.map((o) => o.mode)).toEqual(["hybrid", "agentic_fast"]);
  });
});

describe("resolveMode", () => {
  const full = caps({
    agentic_enabled: true,
    retrievals: ["hybrid", "agentic_fast", "agentic_deep"],
    default_retrieval: "agentic_fast",
  });

  it("keeps a usable selection", () => {
    expect(resolveMode("hybrid", full)).toBe("hybrid");
  });

  it("falls back to the server default when nothing is selected", () => {
    expect(resolveMode(null, full)).toBe("agentic_fast");
  });

  it("does not resolve to a disabled DEEP", () => {
    expect(resolveMode("agentic_deep", full)).toBe("agentic_fast");
  });

  it("falls back to hybrid when agentic is unavailable", () => {
    expect(resolveMode("agentic_fast", caps())).toBe("hybrid");
    expect(resolveMode(null, null)).toBe("hybrid");
  });
});
