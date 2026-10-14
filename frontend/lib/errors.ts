import type { ErrorBody } from "./types";

/** Error raised for any non-2xx response or transport failure. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

const STATUS_CODES: Record<number, string> = {
  400: "bad_request",
  401: "unauthorized",
  403: "forbidden",
  404: "not_found",
  422: "validation_error",
  500: "internal_error",
  503: "service_unavailable",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Extract `{code, message, details}` from a response body. Understands the unified
 * envelope `{"success": false, "error": {...}}`, a bare FastAPI `{"detail": ...}`, and
 * falls back to a status-derived message for anything else (including non-JSON bodies).
 */
export function parseErrorEnvelope(body: unknown, status: number): ErrorBody {
  const fallbackCode = STATUS_CODES[status] ?? "error";
  const fallbackMessage = status > 0 ? `Request failed (HTTP ${status})` : "Request failed";

  if (isRecord(body)) {
    const error = body.error;
    if (isRecord(error)) {
      return {
        code: typeof error.code === "string" && error.code ? error.code : fallbackCode,
        message: typeof error.message === "string" && error.message ? error.message : fallbackMessage,
        details: error.details ?? null,
      };
    }
    if (typeof body.detail === "string" && body.detail) {
      return { code: fallbackCode, message: body.detail, details: null };
    }
  }
  return { code: fallbackCode, message: fallbackMessage, details: null };
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  const message = error instanceof Error ? error.message : String(error);
  return new ApiError(0, "network_error", message || "Network error");
}
