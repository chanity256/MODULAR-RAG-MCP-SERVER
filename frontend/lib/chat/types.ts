/** Chat protocol and view-model types (WEBUI_CHAT_SPEC section 4.7). */

export type RetrievalMode = "hybrid" | "agentic_fast" | "agentic_deep" | "longdoc";

export const RETRIEVAL_MODES: readonly RetrievalMode[] = ["hybrid", "agentic_fast", "agentic_deep", "longdoc"];

/** What the client may request: the real modes plus "auto" (server-side mode routing). */
export type RequestedRetrieval = RetrievalMode | "auto";

export type ChatStage = "routing" | "rewriting" | "retrieving" | "generating" | "retrying";

/** `mode_routing` info from the engine (MODE_ROUTER_SPEC §3.2); present on auto turns only. */
export interface RoutingInfo {
  requested?: string;
  decided?: string | null;
  candidates?: string[];
  backend?: string;
  model?: string;
  confidence?: number | null;
  probabilities?: Record<string, number> | null;
  fallback_reason?: string | null;
  input_tokens?: number | null;
  elapsed_ms?: number | null;
}

export type ChatErrorCode =
  | "AUTH"
  | "RATE_LIMITED"
  | "UPSTREAM"
  | "DEEP_DISABLED"
  | "INTERNAL"
  | "BAD_REQUEST"
  | "UNAVAILABLE"
  | "CONNECTION";

/**
 * `Citation.to_dict()` from `src/core/response/citation_generator.py`. The optional location
 * fields (PAGEINDEX_TREE_RETRIEVAL_SPEC D8) are only present on longdoc citations; the backend
 * omits the key entirely when the value is null.
 */
export interface Citation {
  id: number;
  source: string;
  page: number | null;
  chunk_id: string;
  text: string;
  score: number | null;
  /** Heading path from the document root, joined with " > " (longdoc only). */
  section?: string;
  /** 1-based inclusive line range in the source Markdown (longdoc only). */
  line_start?: number;
  line_end?: number;
}

export interface TokenUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

/** Upstream (client -> server) frame. Unknown keys are rejected by the backend. */
export interface UserMessagePayload {
  message: string;
  session_id?: string;
  retrieval?: RequestedRetrieval;
  collection?: string;
  top_k?: number;
}

export type UpstreamFrame = ({ type: "user_message" } & UserMessagePayload) | { type: "cancel" };

export type DownstreamFrame =
  | { type: "session"; session_id: string }
  | { type: "status"; stage: ChatStage; message?: string }
  | { type: "search_log"; task_id?: string; level: string; message: string; timestamp?: string | number }
  | { type: "search_complete"; message: string }
  | { type: "search_error"; message: string }
  | { type: "stream"; content: string }
  | { type: "sources"; citations: Citation[] }
  | {
      type: "result";
      content: string;
      session_id: string;
      message_id?: string | null;
      grounded: boolean;
      rewritten_query?: string;
      usage?: TokenUsage;
      trace_id?: string;
      cancelled?: boolean;
      retrieval?: string;
      routing?: RoutingInfo;
    }
  | { type: "error"; code: string; message: string };

export interface ChatCapabilities {
  enabled: boolean;
  retrievals: RetrievalMode[];
  default_retrieval: RequestedRetrieval;
  auto_routing: boolean;
  agentic_enabled: boolean;
  allow_deep: boolean;
  /** `tree_search.enabled` — the Longdoc option exists only when true (PAGEINDEX PI5/PI6). */
  longdoc_enabled: boolean;
  progress_stream: boolean;
  max_connections: number;
}

export interface ChatSessionSummary {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatMessageRecord {
  id: string;
  session_id: string;
  role: string;
  content: string;
  sources: unknown[];
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ChatSessionDetail extends ChatSessionSummary {
  messages: ChatMessageRecord[];
}

export interface ChatError {
  code: ChatErrorCode | string;
  message: string;
}

export interface SearchLogEntry {
  level: string;
  message: string;
  timestamp?: string | number;
}

export type MessageStatus = "streaming" | "done" | "cancelled" | "error";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  citations: Citation[];
  /** `false` only for pure-chat / degraded answers; `undefined` while unknown. */
  grounded?: boolean;
  rewrittenQuery?: string;
  usage?: TokenUsage;
  traceId?: string;
  /** Requested mode on send; the result frame overwrites it with the actually served mode. */
  retrieval?: string;
  routing?: RoutingInfo;
  stage?: ChatStage | null;
  stageMessage?: string;
  logs: SearchLogEntry[];
  searchError?: string;
  searchComplete?: string;
}
