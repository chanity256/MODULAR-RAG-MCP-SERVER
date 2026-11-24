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
  type RequestedRetrieval,
  type RetrievalMode,
  type RoutingInfo,
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

const STAGES: readonly ChatStage[] = ["routing", "rewriting", "retrieving", "generating", "retrying"];

export function isRetrievalMode(value: unknown): value is RetrievalMode {
  return typeof value === "string" && (RETRIEVAL_MODES as readonly string[]).includes(value);
}

/** A requested mode: a real mode or "auto" (only usable when the server reports auto_routing). */
export function isRequestedRetrieval(value: unknown): value is RequestedRetrieval {
  return value === "auto" || isRetrievalMode(value);
}

/** Parse the `mode_routing` object of a result frame; undefined when absent (non-auto turns). */
export function normalizeRouting(raw: unknown): RoutingInfo | undefined {
  if (!isRecord(raw)) return undefined;
  const routing: RoutingInfo = {};
  if (isRequestedRetrieval(raw.requested)) routing.requested = raw.requested;
  if (raw.decided === null || isRetrievalMode(raw.decided)) routing.decided = raw.decided ?? null;
  if (Array.isArray(raw.candidates)) routing.candidates = raw.candidates.filter(isRetrievalMode);
  const backend = str(raw.backend);
  if (backend) routing.backend = backend;
  const model = str(raw.model);
  if (model) routing.model = model;
  const confidence = finiteNumber(raw.confidence);
  if (confidence !== null) routing.confidence = confidence;
  else if (raw.confidence === null) routing.confidence = null;
  if (isRecord(raw.probabilities)) {
    const probabilities: Record<string, number> = {};
    for (const [mode, probability] of Object.entries(raw.probabilities)) {
      const value = finiteNumber(probability);
      if (isRetrievalMode(mode) && value !== null) probabilities[mode] = value;
    }
    routing.probabilities = Object.keys(probabilities).length > 0 ? probabilities : null;
  } else if (raw.probabilities === null) {
    routing.probabilities = null;
  }
  if (typeof raw.fallback_reason === "string") routing.fallback_reason = raw.fallback_reason;
  else if (raw.fallback_reason === null) routing.fallback_reason = null;
  const inputTokens = finiteNumber(raw.input_tokens);
  if (inputTokens !== null) routing.input_tokens = Math.floor(inputTokens);
  else if (raw.input_tokens === null) routing.input_tokens = null;
  const elapsed = finiteNumber(raw.elapsed_ms);
  if (elapsed !== null) routing.elapsed_ms = elapsed;
  return Object.keys(routing).length > 0 ? routing : undefined;
}

export function normalizeCitation(raw: unknown, fallbackId: number): Citation | null {
  if (!isRecord(raw)) return null;
  const id = finiteNumber(raw.id);
  const citation: Citation = {
    id: id !== null && id >= 1 ? Math.floor(id) : fallbackId,
    source: str(raw.source) ?? "",
    page: finiteNumber(raw.page),
    chunk_id: str(raw.chunk_id) ?? "",
    text: str(raw.text) ?? "",
    score: finiteNumber(raw.score),
  };
  // Longdoc location fields (PAGEINDEX PI6): pass through when well-typed, omit the key
  // otherwise so existing citation objects keep their exact shape.
  const section = str(raw.section);
  if (section) citation.section = section;
  const lineStart = finiteNumber(raw.line_start);
  if (lineStart !== null) citation.line_start = Math.floor(lineStart);
  const lineEnd = finiteNumber(raw.line_end);
  if (lineEnd !== null) citation.line_end = Math.floor(lineEnd);
  return citation;
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
        retrieval: str(raw.retrieval),
        routing: normalizeRouting(raw.routing),
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
  const longdocEnabled = r.longdoc_enabled === true;
  // `default_retrieval = "auto"` is honored only when the server actually serves auto routing;
  // otherwise (older server, router disabled) it silently falls back to hybrid. The same guard
  // applies to a `longdoc` default (PAGEINDEX PI6): keep it only when the server reports
  // longdoc_enabled and actually serves longdoc.
  const autoRouting = r.auto_routing === true;
  const servesLongdoc = longdocEnabled && retrievals.includes("longdoc");
  const defaultRetrieval =
    r.default_retrieval === "auto" && autoRouting
      ? "auto"
      : isRetrievalMode(r.default_retrieval) && (r.default_retrieval !== "longdoc" || servesLongdoc)
        ? r.default_retrieval
        : "hybrid";
  return {
    enabled: r.enabled !== false,
    retrievals: retrievals.length > 0 ? retrievals : ["hybrid"],
    default_retrieval: defaultRetrieval,
    auto_routing: autoRouting,
    agentic_enabled: r.agentic_enabled === true,
    allow_deep: r.allow_deep === true,
    longdoc_enabled: longdocEnabled,
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
