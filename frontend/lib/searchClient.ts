/*
 * WS /api/v1/search/ws client（WEBUI_SIDECAR_SPEC WS3）。对齐 lib/chat/client.ts 的
 * 「一连接一请求」模式：发送一个起始帧，转发 search_log / search_result / error
 * 帧直到服务端关闭。不可解析与未知帧一律丢弃。
 */
import { wsUrl } from "@/lib/api";
import type { SearchLogEntry } from "@/lib/chat/types";
import { normalizeSearchResponse, type SearchRequestBody, type SearchResult } from "./search";

export const SEARCH_WS_PATH = "/api/v1/search/ws";

type SocketCtor = new (url: string) => WebSocket;

export interface SearchStreamHandlers {
  onLog: (entry: SearchLogEntry) => void;
  onResult: (result: SearchResult) => void;
  onError: (code: string, message: string) => void;
  /** Socket 自行关闭时调用一次（close() 之后不再回调）；gotResult 表示已收到结果帧。 */
  onClosed: (info: { gotResult: boolean }) => void;
}

export interface SearchStreamOptions {
  /** Override the endpoint URL (tests); defaults to wsUrl() with the stored token. */
  url?: string;
  /** Override the WebSocket constructor (tests). */
  WebSocketImpl?: SocketCtor;
}

export interface SearchStream {
  /** Ask the server to stop the search; the result frame is discarded server-side. */
  cancel: () => void;
  /** Drop the connection without any further callbacks. */
  close: () => void;
}

const WS_OPEN = 1;
const WS_CONNECTING = 0;

function parseFrame(raw: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function openSearchStream(
  body: SearchRequestBody,
  handlers: SearchStreamHandlers,
  options: SearchStreamOptions = {},
): SearchStream {
  const Ctor: SocketCtor = options.WebSocketImpl ?? (WebSocket as SocketCtor);
  const socket = new Ctor(options.url ?? wsUrl(SEARCH_WS_PATH));

  let detached = false;
  let gotResult = false;
  let cancelRequested = false;

  socket.onopen = () => {
    if (detached) return;
    socket.send(JSON.stringify(body));
    if (cancelRequested) socket.send(JSON.stringify({ type: "cancel" }));
  };

  socket.onmessage = (event: MessageEvent) => {
    if (detached || typeof event.data !== "string") return;
    const frame = parseFrame(event.data);
    if (!frame) return;
    if (frame.type === "search_log" && typeof frame.message === "string") {
      handlers.onLog({
        level: typeof frame.level === "string" ? frame.level : "info",
        message: frame.message,
        timestamp:
          typeof frame.timestamp === "string" || typeof frame.timestamp === "number"
            ? frame.timestamp
            : undefined,
      });
    } else if (frame.type === "search_result") {
      const payload = { ...frame };
      delete payload.type;
      gotResult = true;
      handlers.onResult(normalizeSearchResponse(payload));
    } else if (frame.type === "error" && typeof frame.code === "string") {
      handlers.onError(frame.code, typeof frame.message === "string" ? frame.message : "");
    }
  };

  socket.onclose = () => {
    if (detached) return;
    detached = true;
    handlers.onClosed({ gotResult });
  };

  // A failed handshake is always followed by `close`, which reports the outcome.
  socket.onerror = () => undefined;

  return {
    cancel: () => {
      if (detached || cancelRequested) return;
      cancelRequested = true;
      if (socket.readyState === WS_OPEN) socket.send(JSON.stringify({ type: "cancel" }));
    },
    close: () => {
      if (detached) return;
      detached = true;
      if (socket.readyState === WS_OPEN || socket.readyState === WS_CONNECTING) socket.close();
    },
  };
}
