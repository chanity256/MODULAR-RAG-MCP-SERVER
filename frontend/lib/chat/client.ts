import { wsUrl } from "@/lib/api";
import { parseDownstreamFrame } from "./normalize";
import type { DownstreamFrame, UpstreamFrame, UserMessagePayload } from "./types";

export const CHAT_WS_PATH = "/api/v1/chat";

type SocketCtor = new (url: string) => WebSocket;

export interface ChatTurnHandlers {
  onFrame: (frame: DownstreamFrame) => void;
  /** Called once when the socket closes by itself (not after `close()`); `terminal` says a result/error frame was seen. */
  onClosed: (info: { terminal: boolean }) => void;
}

export interface ChatTurnOptions {
  /** Override the endpoint URL (tests); defaults to `wsUrl()` with the stored token. */
  url?: string;
  /** Override the WebSocket constructor (tests). */
  WebSocketImpl?: SocketCtor;
}

export interface ChatTurn {
  /** Ask the server to stop the turn; the partial answer still arrives as `result{cancelled}`. */
  cancel: () => void;
  /** Drop the connection without any further callbacks (server treats disconnect as cancel). */
  close: () => void;
}

const WS_OPEN = 1;
const WS_CONNECTING = 0;

/**
 * One connection per message (spec 4.7): open, send a single `user_message`, relay downstream
 * frames until the server closes. Unparseable and unknown frames are dropped.
 */
export function openChatTurn(
  payload: UserMessagePayload,
  handlers: ChatTurnHandlers,
  options: ChatTurnOptions = {},
): ChatTurn {
  const Ctor: SocketCtor = options.WebSocketImpl ?? (WebSocket as SocketCtor);
  const socket = new Ctor(options.url ?? wsUrl(CHAT_WS_PATH));

  let detached = false;
  let terminal = false;
  let cancelRequested = false;

  const send = (frame: UpstreamFrame) => {
    socket.send(JSON.stringify(frame));
  };

  socket.onopen = () => {
    if (detached) return;
    send({ type: "user_message", ...payload });
    if (cancelRequested) send({ type: "cancel" });
  };

  socket.onmessage = (event: MessageEvent) => {
    if (detached || typeof event.data !== "string") return;
    const frame = parseDownstreamFrame(event.data);
    if (!frame) return;
    if (frame.type === "result" || frame.type === "error") terminal = true;
    handlers.onFrame(frame);
  };

  socket.onclose = () => {
    if (detached) return;
    detached = true;
    handlers.onClosed({ terminal });
  };

  // A failed handshake is always followed by `close`, which reports the outcome.
  socket.onerror = () => undefined;

  return {
    cancel: () => {
      if (detached || cancelRequested) return;
      cancelRequested = true;
      if (socket.readyState === WS_OPEN) send({ type: "cancel" });
    },
    close: () => {
      if (detached) return;
      detached = true;
      if (socket.readyState === WS_OPEN || socket.readyState === WS_CONNECTING) socket.close();
    },
  };
}
