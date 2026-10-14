"use client";

import { useState } from "react";
import { PanelLeft } from "lucide-react";
import { ErrorNotice } from "@/components/dashboard/AsyncState";
import { useChat } from "@/hooks/useChat";
import Composer from "./Composer";
import ChatErrorAlert from "./ChatErrorAlert";
import MessageList from "./MessageList";
import ModeSelector from "./ModeSelector";
import SessionList from "./SessionList";

export default function ChatView() {
  const chat = useChat();
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const { state, capabilities, capabilitiesError } = chat;

  return (
    <div className="flex h-full min-h-0">
      <SessionList
        sessions={chat.sessions}
        currentId={state.sessionId}
        loading={chat.sessionsLoading}
        error={chat.sessionsError}
        onNew={() => {
          chat.newChat();
          setSessionsOpen(false);
        }}
        onOpen={(id) => {
          void chat.openSession(id);
          setSessionsOpen(false);
        }}
        onDelete={(id) => void chat.removeSession(id)}
        className={`${sessionsOpen ? "flex" : "hidden"} md:flex`}
      />

      <section className="flex min-w-0 flex-1 flex-col" aria-label="Chat">
        <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2 dark:border-slate-700 dark:bg-slate-800">
          <button
            type="button"
            onClick={() => setSessionsOpen((v) => !v)}
            aria-label="Toggle conversations"
            className="rounded p-1 text-slate-500 hover:bg-slate-100 md:hidden dark:text-slate-400 dark:hover:bg-slate-700"
          >
            <PanelLeft size={18} />
          </button>
          <span className="text-sm font-medium text-slate-700 dark:text-slate-200">Retrieval</span>
          <ModeSelector
            capabilities={capabilities}
            value={chat.mode}
            onChange={chat.setMode}
            disabled={state.busy}
          />
        </header>

        {capabilitiesError?.isUnauthorized && (
          <div className="p-3">
            <ErrorNotice error={capabilitiesError} onRetry={chat.onTokenChanged} />
          </div>
        )}
        {capabilities && !capabilities.enabled && (
          <div role="alert" className="m-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200">
            Chat is disabled in the server configuration (chat.enabled = false).
          </div>
        )}

        <MessageList messages={state.messages} />

        <footer className="space-y-2 border-t border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-800">
          <div className="mx-auto max-w-4xl space-y-2">
            {state.error && (
              <ChatErrorAlert
                error={state.error}
                onDismiss={chat.dismissError}
                onRetry={chat.retry}
                onTokenSaved={chat.onTokenChanged}
              />
            )}
            <Composer busy={state.busy} onSend={chat.send} onStop={chat.stop} />
          </div>
        </footer>
      </section>
    </div>
  );
}
