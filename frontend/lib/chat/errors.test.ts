import { describe, expect, it } from "vitest";
import { describeChatError, formatUsage } from "./errors";
import { stageLabel } from "./status";

describe("describeChatError", () => {
  it("AUTH offers the token control", () => {
    expect(describeChatError({ code: "AUTH", message: "x" }).action).toBe("set_token");
  });

  it("retryable codes offer retry", () => {
    for (const code of ["RATE_LIMITED", "UPSTREAM", "INTERNAL", "CONNECTION", "SOMETHING_NEW"]) {
      expect(describeChatError({ code, message: "" }).action).toBe("retry");
    }
  });

  it("non-retryable codes offer no action and explain themselves", () => {
    for (const code of ["DEEP_DISABLED", "UNAVAILABLE", "BAD_REQUEST"]) {
      const view = describeChatError({ code, message: "" });
      expect(view.action).toBeNull();
      expect(view.hint.length).toBeGreaterThan(10);
    }
  });
});

describe("formatUsage", () => {
  it("formats full usage", () => {
    expect(formatUsage({ prompt_tokens: 1000, completion_tokens: 234, total_tokens: 1234 })).toBe(
      "1,234 tokens (in 1,000 / out 234)",
    );
  });

  it("derives a total, handles partial usage and nothing", () => {
    expect(formatUsage({ prompt_tokens: 1, completion_tokens: 2 })).toBe("3 tokens (in 1 / out 2)");
    expect(formatUsage({ total_tokens: 9 })).toBe("9 tokens");
    expect(formatUsage({})).toBeNull();
    expect(formatUsage(undefined)).toBeNull();
  });
});

describe("stageLabel", () => {
  it("prefers the server message, falls back to a label, null with no stage", () => {
    expect(stageLabel("retrieving", "searching")).toBe("searching");
    expect(stageLabel("retrying")).toBe("Retrying...");
    expect(stageLabel("generating", "  ")).toBe("Generating the answer...");
    expect(stageLabel(null)).toBeNull();
  });
});
