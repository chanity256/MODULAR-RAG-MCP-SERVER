import { apiFetch } from "./api";
import { ApiError, parseErrorEnvelope, toApiError } from "./errors";
import { normalizeOverview, normalizeTraceDetail, normalizeTracePage } from "./normalize";
import type { Overview, Page, TraceDetail, TraceSummary, TraceType } from "./types";

type QueryValue = string | number | undefined | null;

/** Build `?a=1&b=2`, skipping empty values. Returns "" when nothing remains. */
export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** GET JSON from the API; every failure surfaces as an `ApiError` with the unified message. */
export async function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await apiFetch(path, { signal, headers: { Accept: "application/json" } });
  } catch (error) {
    throw toApiError(error);
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const { code, message, details } = parseErrorEnvelope(body, response.status);
    throw new ApiError(response.status, code, message, details);
  }
  return body;
}

export async function fetchOverview(signal?: AbortSignal): Promise<Overview> {
  return normalizeOverview(await getJson("/api/v1/overview", signal));
}

export interface TraceListParams {
  traceType?: TraceType;
  keyword?: string;
  /** Server-side source tag filter (`chat` / `agentic` / `default` / ...). */
  routes?: string;
  limit?: number;
  offset?: number;
}

export async function fetchTraces(
  params: TraceListParams = {},
  signal?: AbortSignal,
): Promise<Page<TraceSummary>> {
  const query = buildQuery({
    trace_type: params.traceType,
    keyword: params.keyword?.trim(),
    routes: params.routes?.trim(),
    limit: params.limit,
    offset: params.offset,
  });
  return normalizeTracePage(await getJson(`/api/v1/traces${query}`, signal));
}

export async function fetchTrace(traceId: string, signal?: AbortSignal): Promise<TraceDetail> {
  return normalizeTraceDetail(await getJson(`/api/v1/traces/${encodeURIComponent(traceId)}`, signal));
}
