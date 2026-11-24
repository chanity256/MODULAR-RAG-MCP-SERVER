import { describe, expect, it } from "vitest";
import { buildModeOptions, FALLBACK_REASON_LABELS, modeLabel, resolveMode } from "./modes";
import type { ChatCapabilities } from "./types";

const caps = (over: Partial<ChatCapabilities> = {}): ChatCapabilities => ({
  enabled: true,
  retrievals: ["hybrid"],
  default_retrieval: "hybrid",
  auto_routing: false,
  agentic_enabled: false,
  allow_deep: false,
  longdoc_enabled: false,
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

  it("offers Auto only when the server reports auto_routing (MR4)", () => {
    expect(buildModeOptions(caps({ auto_routing: true })).map((o) => o.mode)).toEqual(["auto", "hybrid"]);
    expect(buildModeOptions(caps({ auto_routing: false })).map((o) => o.mode)).toEqual(["hybrid"]);
    expect(buildModeOptions(caps({ auto_routing: true, agentic_enabled: true, retrievals: ["hybrid", "agentic_fast"] })).map((o) => o.mode)).toEqual([
      "auto",
      "hybrid",
      "agentic_fast",
    ]);
  });

  it("offers Longdoc last when the server reports longdoc_enabled and serves it (PI6)", () => {
    const longdoc = caps({ longdoc_enabled: true, retrievals: ["hybrid", "longdoc"] });
    expect(buildModeOptions(longdoc).map((o) => o.mode)).toEqual(["hybrid", "longdoc"]);

    // Full ordering: Auto, Hybrid, Agentic (fast), Agentic (deep), Longdoc.
    const full = caps({
      auto_routing: true,
      agentic_enabled: true,
      allow_deep: true,
      longdoc_enabled: true,
      retrievals: ["hybrid", "agentic_fast", "agentic_deep", "longdoc"],
    });
    expect(buildModeOptions(full).map((o) => o.mode)).toEqual(["auto", "hybrid", "agentic_fast", "agentic_deep", "longdoc"]);
  });

  it("offers Longdoc even when the agentic sidecar is off (PI6)", () => {
    expect(buildModeOptions(caps({ longdoc_enabled: true, retrievals: ["hybrid", "longdoc"] })).map((o) => o.mode)).toEqual([
      "hybrid",
      "longdoc",
    ]);
  });

  it("hides Longdoc when not enabled or not served", () => {
    expect(buildModeOptions(caps({ retrievals: ["hybrid", "longdoc"] })).map((o) => o.mode)).toEqual(["hybrid"]);
    expect(buildModeOptions(caps({ longdoc_enabled: true, retrievals: ["hybrid"] })).map((o) => o.mode)).toEqual(["hybrid"]);
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

  it("keeps an auto selection and honors the auto default (MR4)", () => {
    const auto = caps({ auto_routing: true, default_retrieval: "auto" });
    expect(resolveMode("auto", auto)).toBe("auto");
    expect(resolveMode(null, auto)).toBe("auto");
    // Without auto_routing the requested auto falls back to hybrid.
    expect(resolveMode("auto", caps())).toBe("hybrid");
    expect(resolveMode(null, caps({ default_retrieval: "auto" }))).toBe("hybrid");
  });

  it("keeps a longdoc selection and honors the longdoc default (PI6)", () => {
    const longdoc = caps({ longdoc_enabled: true, retrievals: ["hybrid", "longdoc"], default_retrieval: "longdoc" });
    expect(resolveMode("longdoc", longdoc)).toBe("longdoc");
    expect(resolveMode(null, longdoc)).toBe("longdoc");
    // Without longdoc_enabled (older server) the requested longdoc falls back to hybrid.
    expect(resolveMode("longdoc", caps())).toBe("hybrid");
    expect(resolveMode(null, caps({ default_retrieval: "longdoc" }))).toBe("hybrid");
  });
});

describe("modeLabel / fallback reasons (MR4)", () => {
  it("labels known modes and passes through unknown values", () => {
    expect(modeLabel("auto")).toBe("Auto");
    expect(modeLabel("hybrid")).toBe("Hybrid");
    expect(modeLabel("agentic_fast")).toBe("Agentic (fast)");
    expect(modeLabel("agentic_deep")).toBe("Agentic (deep)");
    expect(modeLabel("longdoc")).toBe("Longdoc");
    expect(modeLabel("mystery")).toBe("mystery");
    expect(modeLabel(null)).toBe("");
  });

  it("maps every documented fallback reason", () => {
    for (const reason of ["timeout", "unreachable", "invalid_output", "low_confidence", "error"]) {
      expect(FALLBACK_REASON_LABELS[reason]).toBeTruthy();
    }
  });
});
