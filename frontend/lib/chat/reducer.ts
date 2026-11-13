import { messagesFromSession } from "./normalize";
import type { ChatError, ChatMessage, ChatSessionDetail, DownstreamFrame } from "./types";

export interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  /** True from `send` until a terminal frame (or the connection closing) ends the turn. */
  busy: boolean;
  error: ChatError | null;
}

export type ChatAction =
  | { type: "send"; userId: string; assistantId: string; message: string; retrieval?: string }
  | { type: "frame"; frame: DownstreamFrame }
  | { type: "closed" }
  | { type: "load_session"; session: ChatSessionDetail }
  | { type: "new_chat" }
  | { type: "dismiss_error" };

export const initialChatState: ChatState = { sessionId: null, messages: [], busy: false, error: null };

const MAX_LOG_ENTRIES = 200;

function lastMessage(state: ChatState): ChatMessage | undefined {
  return state.messages[state.messages.length - 1];
}

function isLiveAssistant(message: ChatMessage | undefined): message is ChatMessage {
  return message !== undefined && message.role === "assistant" && message.status === "streaming";
}

function patchLast(state: ChatState, patch: (message: ChatMessage) => ChatMessage): ChatMessage[] {
  const last = lastMessage(state);
  if (!isLiveAssistant(last)) return state.messages;
  return [...state.messages.slice(0, -1), patch(last)];
}

/** Settle the live assistant message; an empty one is dropped so no blank bubble is left behind. */
function finishLast(
  state: ChatState,
  patch: (message: ChatMessage) => ChatMessage,
  dropIfEmpty: boolean,
): ChatMessage[] {
  const last = lastMessage(state);
  if (!isLiveAssistant(last)) return state.messages;
  const next = patch(last);
  if (dropIfEmpty && next.content === "") return state.messages.slice(0, -1);
  return [...state.messages.slice(0, -1), next];
}

function reduceFrame(state: ChatState, frame: DownstreamFrame): ChatState {
  switch (frame.type) {
    case "session":
      return { ...state, sessionId: frame.session_id };

    case "status":
      return {
        ...state,
        messages: patchLast(state, (m) => ({ ...m, stage: frame.stage, stageMessage: frame.message })),
      };

    case "search_log":
      return {
        ...state,
        messages: patchLast(state, (m) => ({
          ...m,
          logs: [...m.logs, { level: frame.level, message: frame.message, timestamp: frame.timestamp }].slice(
            -MAX_LOG_ENTRIES,
          ),
        })),
      };

    case "search_complete":
      return { ...state, messages: patchLast(state, (m) => ({ ...m, searchComplete: frame.message })) };

    case "search_error":
      return { ...state, messages: patchLast(state, (m) => ({ ...m, searchError: frame.message })) };

    case "stream":
      return { ...state, messages: patchLast(state, (m) => ({ ...m, content: m.content + frame.content })) };

    case "sources":
      return { ...state, messages: patchLast(state, (m) => ({ ...m, citations: frame.citations })) };

    case "result": {
      if (!isLiveAssistant(lastMessage(state))) return state;
      return {
        ...state,
        sessionId: frame.session_id || state.sessionId,
        busy: false,
        error: null,
        messages: finishLast(
          state,
          (m) => ({
            ...m,
            // The terminal frame carries the authoritative text; keep streamed text if it is empty.
            content: frame.content !== "" ? frame.content : m.content,
            status: frame.cancelled ? "cancelled" : "done",
            grounded: frame.grounded,
            rewrittenQuery: frame.rewritten_query ?? m.rewrittenQuery,
            usage: frame.usage ?? m.usage,
            traceId: frame.trace_id ?? m.traceId,
            stage: null,
            stageMessage: undefined,
          }),
          false,
        ),
      };
    }

    case "error":
      return {
        ...state,
        busy: false,
        error: { code: frame.code, message: frame.message },
        messages: finishLast(
          state,
          (m) => ({ ...m, status: m.content === "" ? "error" : "cancelled", stage: null, stageMessage: undefined }),
          true,
        ),
      };

    default:
      return state;
  }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "send":
      return {
        ...state,
        busy: true,
        error: null,
        messages: [
          ...state.messages,
          { id: action.userId, role: "user", content: action.message, status: "done", citations: [], logs: [] },
          {
            id: action.assistantId,
            role: "assistant",
            content: "",
            status: "streaming",
            citations: [],
            retrieval: action.retrieval,
            stage: null,
            logs: [],
          },
        ],
      };

    case "frame":
      return reduceFrame(state, action.frame);

    case "closed": {
      if (!state.busy) return state;
      return {
        ...state,
        busy: false,
        error: { code: "CONNECTION", message: "Connection closed before the answer finished" },
        messages: finishLast(
          state,
          (m) => ({ ...m, status: m.content === "" ? "error" : "cancelled", stage: null, stageMessage: undefined }),
          true,
        ),
      };
    }

    case "load_session":
      return {
        sessionId: action.session.session_id,
        messages: messagesFromSession(action.session),
        busy: false,
        error: null,
      };

    case "new_chat":
      return initialChatState;

    case "dismiss_error":
      return state.error ? { ...state, error: null } : state;

    default:
      return state;
  }
}
