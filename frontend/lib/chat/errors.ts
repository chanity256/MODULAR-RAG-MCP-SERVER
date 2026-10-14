import type { ChatError } from "./types";

export type ChatErrorAction = "set_token" | "retry" | null;

export interface ChatErrorView {
  title: string;
  hint: string;
  action: ChatErrorAction;
}

/** Map an `error` frame code to a title, a remediation hint and an optional UI action. */
export function describeChatError(error: ChatError): ChatErrorView {
  switch (error.code) {
    case "AUTH":
      return {
        title: "Authentication failed",
        hint: "The API token is missing or wrong. Set the token (RAG_API_TOKEN) and send again.",
        action: "set_token",
      };
    case "RATE_LIMITED":
      return {
        title: "Rate limited",
        hint: "The model provider or server is receiving too many requests. Wait a moment and retry.",
        action: "retry",
      };
    case "UPSTREAM":
      return {
        title: "Model provider error",
        hint: "The LLM or retrieval backend failed. Check the provider configuration, then retry.",
        action: "retry",
      };
    case "DEEP_DISABLED":
      return {
        title: "Deep search is disabled",
        hint: "Switch to Hybrid or Agentic (fast), or enable agentic_search.allow_deep on the server.",
        action: null,
      };
    case "UNAVAILABLE":
      return {
        title: "Chat is unavailable",
        hint: "Chat is disabled in the server configuration (chat.enabled).",
        action: null,
      };
    case "BAD_REQUEST":
      return {
        title: "Request rejected",
        hint: "The server rejected the request, for example an unknown session or retrieval mode. Start a new chat.",
        action: null,
      };
    case "CONNECTION":
      return {
        title: "Connection lost",
        hint: "The connection closed before the answer finished. Any partial text was kept; retry to continue.",
        action: "retry",
      };
    default:
      return {
        title: "Chat error",
        hint: "An unexpected server error occurred. Check the server logs and retry.",
        action: "retry",
      };
  }
}

/** Compact token usage, e.g. `1,234 tokens (in 1,000 / out 234)`; null when nothing is known. */
export function formatUsage(usage: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } | undefined): string | null {
  if (!usage) return null;
  const fmt = (n: number) => n.toLocaleString("en-US");
  const total = usage.total_tokens ?? (usage.prompt_tokens !== undefined && usage.completion_tokens !== undefined ? usage.prompt_tokens + usage.completion_tokens : undefined);
  if (total === undefined) return null;
  if (usage.prompt_tokens !== undefined && usage.completion_tokens !== undefined) {
    return `${fmt(total)} tokens (in ${fmt(usage.prompt_tokens)} / out ${fmt(usage.completion_tokens)})`;
  }
  return `${fmt(total)} tokens`;
}
