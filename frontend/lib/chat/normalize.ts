import {
  RETRIEVAL_MODES,
  type ChatCapabilities,
  type ChatMessage,
  type ChatMessageRecord,
  type ChatSessionDetail,
  type ChatSessionSummary,
  type ChatStage,
  type Citation,
  type DownstreamFrame,
  type RetrievalMode,
  type TokenUsage,
} from "./types";

type Rec = Record<string, unknown>;

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const STAGES: readonly ChatStage[] = ["rewriting", "retrieving", "generating", "retrying"];

export function isRetrievalMode(value: unknown): value is RetrievalMode {
  return typeof value === "string" && (RETRIEVAL_MODES as readonly string[]).includes(value);
}

export function normalizeCitation(raw: unknown, fallbackId: number): Citation | null {
  if (!isRecord(raw)) return null;
  const id = finiteNumber(raw.id);
  return {
    id: id !== null && id >= 1 ? Math.floor(id) : fallbackId,
    source: str(raw.source) ?? "",
    page: finiteNumber(raw.page),
    chunk_id: str(raw.chunk_id) ?? "",
    text: str(raw.text) ?? "",
    score: finiteNumber(raw.score),
  };
}

export function normalizeCitations(raw: unknown): Citation[] {
  if (!Array.isArray(raw)) return [];
  const out: Citation[] = [];
  raw.forEach((item, index) => {
    const citation = normalizeCitation(item, index + 1);
    if (citation) out.push(citation);
  });
  return out;
}

export function normalizeUsage(raw: unknown): TokenUsage | undefined {
  if (!isRecord(raw)) return undefined;
  const usage: TokenUsage = {};
  const prompt = finiteNumber(raw.prompt_tokens);
  const completion = finiteNumber(raw.completion_tokens);
  const total = finiteNumber(raw.total_tokens);
  if (prompt !== null) usage.prompt_tokens = prompt;
  if (completion !== null) usage.completion_tokens = completion;
  if (total !== null) usage.total_tokens = total;
  else if (prompt !== null && completion !== null) usage.total_tokens = prompt + completion;
  return Object.keys(usage).length > 0 ? usage : undefined;
}

/**
 * Parse one downstream WS text frame. Returns `null` for invalid JSON, non-objects, unknown
 * frame types and frames missing the fields the reducer depends on, so a newer server can add
 * frame types without breaking the client.
 */
export function parseDownstreamFrame(text: string): DownstreamFrame | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isRecord(raw)) return null;

  switch (raw.type) {
    case "session": {
      const sessionId = str(raw.session_id);
      return sessionId ? { type: "session", session_id: sessionId } : null;
    }
    case "status": {
      const stage = STAGES.find((s) => s === raw.stage);
      return stage ? { type: "status", stage, message: str(raw.message) } : null;
    }
    case "search_log": {
      const timestamp = typeof raw.timestamp === "string" || typeof raw.timestamp === "number" ? raw.timestamp : undefined;
      return {
        type: "search_log",
        task_id: str(raw.task_id),
        level: str(raw.level) ?? "info",
        message: str(raw.message) ?? "",
        timestamp,
      };
    }
    case "search_complete":
      return { type: "search_complete", message: str(raw.message) ?? "" };
    case "search_error":
      return { type: "search_error", message: str(raw.message) ?? "" };
    case "stream": {
      const content = str(raw.content);
      return content === undefined ? null : { type: "stream", content };
    }
    case "sources":
      return { type: "sources", citations: normalizeCitations(raw.citations) };
    case "result":
      return {
        type: "result",
        content: str(raw.content) ?? "",
        session_id: str(raw.session_id) ?? "",
        message_id: str(raw.message_id) ?? null,
        grounded: raw.grounded !== false,
        rewritten_query: str(raw.rewritten_query),
        usage: normalizeUsage(raw.usage),
        trace_id: str(raw.trace_id),
        cancelled: raw.cancelled === true,
      };
    case "error":
      return { type: "error", code: str(raw.code) ?? "INTERNAL", message: str(raw.message) ?? "Chat error" };
    default:
      return null;
  }
}

export function normalizeCapabilities(raw: unknown): ChatCapabilities {
  const r = isRecord(raw) ? raw : {};
  const retrievals = Array.isArray(r.retrievals) ? r.retrievals.filter(isRetrievalMode) : [];
  const defaultRetrieval = isRetrievalMode(r.default_retrieval) ? r.default_retrieval : "hybrid";
  return {
    enabled: r.enabled !== false,
    retrievals: retrievals.length > 0 ? retrievals : ["hybrid"],
    default_retrieval: defaultRetrieval,
    agentic_enabled: r.agentic_enabled === true,
    allow_deep: r.allow_deep === true,
    progress_stream: r.progress_stream === true,
    max_connections: finiteNumber(r.max_connections) ?? 0,
  };
}

export function normalizeSessionSummary(raw: unknown): ChatSessionSummary | null {
  if (!isRecord(raw)) return null;
  const sessionId = str(raw.session_id);
  if (!sessionId) return null;
  return {
    session_id: sessionId,
    title: str(raw.title) ?? "",
    created_at: str(raw.created_at) ?? "",
    updated_at: str(raw.updated_at) ?? "",
    message_count: finiteNumber(raw.message_count) ?? 0,
  };
}

export function normalizeSessionPage(raw: unknown): ChatSessionSummary[] {
  const items = isRecord(raw) && Array.isArray(raw.items) ? raw.items : [];
  return items.map(normalizeSessionSummary).filter((s): s is ChatSessionSummary => s !== null);
}

export function normalizeSessionDetail(raw: unknown): ChatSessionDetail | null {
  const summary = normalizeSessionSummary(raw);
  if (!summary || !isRecord(raw)) return null;
  const messages: ChatMessageRecord[] = [];
  if (Array.isArray(raw.messages)) {
    for (const item of raw.messages) {
      if (!isRecord(item)) continue;
      messages.push({
        id: str(item.id) ?? `${summary.session_id}-${messages.length}`,
        session_id: summary.session_id,
        role: str(item.role) ?? "assistant",
        content: str(item.content) ?? "",
        sources: Array.isArray(item.sources) ? item.sources : [],
        metadata: isRecord(item.metadata) ? item.metadata : {},
        created_at: str(item.created_at) ?? "",
      });
    }
  }
  return { ...summary, messages };
}

/** Convert one persisted message into the view model used for live turns. */
export function messageFromRecord(record: ChatMessageRecord): ChatMessage | null {
  if (record.role !== "user" && record.role !== "assistant") return null;
  if (record.role === "user") {
    return { id: record.id, role: "user", content: record.content, status: "done", citations: [], logs: [] };
  }
  const meta = record.metadata;
  const hasError = isRecord(meta.error);
  const status = meta.cancelled === true ? "cancelled" : hasError && !record.content ? "error" : "done";
  return {
    id: record.id,
    role: "assistant",
    content: record.content,
    status,
    citations: normalizeCitations(record.sources),
    grounded: typeof meta.grounded === "boolean" ? meta.grounded : undefined,
    rewrittenQuery: str(meta.rewritten_query),
    usage: normalizeUsage(meta.usage),
    traceId: str(meta.trace_id),
    retrieval: str(meta.retrieval),
    stage: null,
    logs: [],
  };
}

export function messagesFromSession(detail: ChatSessionDetail): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const record of detail.messages) {
    const message = messageFromRecord(record);
    if (message) out.push(message);
  }
  return out;
}
