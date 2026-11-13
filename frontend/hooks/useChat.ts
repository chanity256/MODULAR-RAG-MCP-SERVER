"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { deleteSession, fetchCapabilities, fetchSession, fetchSessions } from "@/lib/chat/api";
import { openChatTurn, type ChatTurn } from "@/lib/chat/client";
import { resolveMode } from "@/lib/chat/modes";
import { chatReducer, initialChatState } from "@/lib/chat/reducer";
import type { RetrievalMode } from "@/lib/chat/types";
import { toApiError, type ApiError } from "@/lib/errors";
import { useAsyncData } from "@/hooks/useAsyncData";

let idCounter = 0;
function newId(prefix: string): string {
  idCounter += 1;
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${idCounter}`;
  return `${prefix}-${random}`;
}

export function useChat() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [selectedMode, setSelectedMode] = useState<RetrievalMode | null>(null);
  // Bumped when the stored token changes so capability / session requests are re-issued with it.
  const [tokenVersion, setTokenVersion] = useState(0);
  const [sessionError, setSessionError] = useState<ApiError | null>(null);
  const turnRef = useRef<ChatTurn | null>(null);
  const stateRef = useRef(state);

  useEffect(() => {
    stateRef.current = state;
  });

  const capabilities = useAsyncData((signal) => fetchCapabilities(signal), `caps:${tokenVersion}`);
  const sessions = useAsyncData((signal) => fetchSessions(signal), `sessions:${tokenVersion}`);
  const reloadSessions = sessions.reload;

  const mode = resolveMode(selectedMode, capabilities.data ?? null);

  const closeTurn = useCallback(() => {
    turnRef.current?.close();
    turnRef.current = null;
  }, []);

  useEffect(() => closeTurn, [closeTurn]);

  // A finished turn creates or updates a session row.
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !state.busy) reloadSessions();
    wasBusy.current = state.busy;
  }, [state.busy, reloadSessions]);

  const send = useCallback(
    (text: string) => {
      const message = text.trim();
      if (!message || stateRef.current.busy) return false;
      closeTurn();
      dispatch({ type: "send", userId: newId("u"), assistantId: newId("a"), message, retrieval: mode });
      turnRef.current = openChatTurn(
        {
          message,
          retrieval: mode,
          ...(stateRef.current.sessionId ? { session_id: stateRef.current.sessionId } : {}),
        },
        {
          onFrame: (frame) => dispatch({ type: "frame", frame }),
          onClosed: () => {
            turnRef.current = null;
            dispatch({ type: "closed" });
          },
        },
      );
      return true;
    },
    [closeTurn, mode],
  );

  const stop = useCallback(() => {
    turnRef.current?.cancel();
  }, []);

  const retry = useCallback(() => {
    const lastUser = [...stateRef.current.messages].reverse().find((m) => m.role === "user");
    if (lastUser) send(lastUser.content);
  }, [send]);

  const newChat = useCallback(() => {
    closeTurn();
    setSessionError(null);
    dispatch({ type: "new_chat" });
  }, [closeTurn]);

  const openSession = useCallback(
    async (sessionId: string) => {
      closeTurn();
      setSessionError(null);
      try {
        const session = await fetchSession(sessionId);
        dispatch({ type: "load_session", session });
      } catch (error) {
        setSessionError(toApiError(error));
      }
    },
    [closeTurn],
  );

  const removeSession = useCallback(
    async (sessionId: string) => {
      setSessionError(null);
      try {
        await deleteSession(sessionId);
      } catch (error) {
        const apiError = toApiError(error);
        // Already gone: just refresh the list.
        if (apiError.status !== 404) {
          setSessionError(apiError);
          return;
        }
      }
      if (stateRef.current.sessionId === sessionId) {
        closeTurn();
        dispatch({ type: "new_chat" });
      }
      reloadSessions();
    },
    [closeTurn, reloadSessions],
  );

  const dismissError = useCallback(() => dispatch({ type: "dismiss_error" }), []);

  const onTokenChanged = useCallback(() => setTokenVersion((v) => v + 1), []);

  return {
    state,
    mode,
    setMode: setSelectedMode,
    capabilities: capabilities.data ?? null,
    capabilitiesError: capabilities.error,
    sessions: sessions.data ?? [],
    sessionsLoading: sessions.loading,
    sessionsError: sessions.error ?? sessionError,
    send,
    stop,
    retry,
    newChat,
    openSession,
    removeSession,
    dismissError,
    onTokenChanged,
  };
}
