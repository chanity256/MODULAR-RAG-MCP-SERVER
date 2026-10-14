import { apiFetch } from "@/lib/api";
import { ApiError, parseErrorEnvelope, toApiError } from "@/lib/errors";
import { getJson } from "@/lib/queries";
import { normalizeCapabilities, normalizeSessionDetail, normalizeSessionPage } from "./normalize";
import type { ChatCapabilities, ChatSessionDetail, ChatSessionSummary } from "./types";

export async function fetchCapabilities(signal?: AbortSignal): Promise<ChatCapabilities> {
  return normalizeCapabilities(await getJson("/api/v1/chat/capabilities", signal));
}

export async function fetchSessions(signal?: AbortSignal, limit = 100): Promise<ChatSessionSummary[]> {
  return normalizeSessionPage(await getJson(`/api/v1/chat/sessions?limit=${limit}`, signal));
}

export async function fetchSession(sessionId: string, signal?: AbortSignal): Promise<ChatSessionDetail> {
  const detail = normalizeSessionDetail(
    await getJson(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`, signal),
  );
  if (!detail) throw new ApiError(0, "bad_response", "Malformed chat session response");
  return detail;
}

export async function deleteSession(sessionId: string): Promise<void> {
  let response: Response;
  try {
    response = await apiFetch(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    throw toApiError(error);
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    const { code, message, details } = parseErrorEnvelope(body, response.status);
    throw new ApiError(response.status, code, message, details);
  }
}
