import { describe, expect, it } from "vitest";
import { ApiError, parseErrorEnvelope, toApiError } from "./errors";

describe("parseErrorEnvelope", () => {
  it("reads the unified envelope", () => {
    const body = { success: false, error: { code: "not_found", message: "Trace 'x' not found", details: null } };
    expect(parseErrorEnvelope(body, 404)).toEqual({
      code: "not_found",
      message: "Trace 'x' not found",
      details: null,
    });
  });

  it("keeps structured details", () => {
    const details = [{ loc: ["query", "limit"], msg: "too big", type: "x" }];
    const body = { success: false, error: { code: "validation_error", message: "Request validation failed", details } };
    expect(parseErrorEnvelope(body, 422).details).toEqual(details);
  });

  it("falls back to FastAPI detail strings", () => {
    expect(parseErrorEnvelope({ detail: "Not authenticated" }, 401)).toEqual({
      code: "unauthorized",
      message: "Not authenticated",
      details: null,
    });
  });

  it("derives code and message from status for unknown bodies", () => {
    expect(parseErrorEnvelope(null, 503)).toEqual({
      code: "service_unavailable",
      message: "Request failed (HTTP 503)",
      details: null,
    });
    expect(parseErrorEnvelope("<html>", 418).code).toBe("error");
  });

  it("fills missing envelope fields", () => {
    expect(parseErrorEnvelope({ error: {} }, 500)).toEqual({
      code: "internal_error",
      message: "Request failed (HTTP 500)",
      details: null,
    });
  });
});

describe("ApiError", () => {
  it("flags 401 as unauthorized", () => {
    expect(new ApiError(401, "unauthorized", "no").isUnauthorized).toBe(true);
    expect(new ApiError(500, "internal_error", "no").isUnauthorized).toBe(false);
  });

  it("toApiError wraps transport failures with status 0", () => {
    const wrapped = toApiError(new TypeError("Failed to fetch"));
    expect(wrapped).toBeInstanceOf(ApiError);
    expect(wrapped.status).toBe(0);
    expect(wrapped.code).toBe("network_error");
    expect(wrapped.message).toBe("Failed to fetch");
  });

  it("toApiError passes ApiError through", () => {
    const original = new ApiError(404, "not_found", "x");
    expect(toApiError(original)).toBe(original);
  });
});
