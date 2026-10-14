"use client";

import { MessageSquarePlus, Trash2 } from "lucide-react";
import type { ChatSessionSummary } from "@/lib/chat/types";
import type { ApiError } from "@/lib/errors";

interface Props {
  sessions: readonly ChatSessionSummary[];
  currentId: string | null;
  loading: boolean;
  error: ApiError | undefined | null;
  onNew: () => void;
  onOpen: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
  className?: string;
}

export default function SessionList({ sessions, currentId, loading, error, onNew, onOpen, onDelete, className = "" }: Props) {
  return (
    <aside
      aria-label="Chat sessions"
      className={`w-64 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800 ${className}`}
    >
      <div className="p-3">
        <button
          type="button"
          onClick={onNew}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-700"
        >
          <MessageSquarePlus size={16} />
          New chat
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {error && (
          <p role="alert" className="px-2 py-1 text-xs text-red-600 dark:text-red-400">
            {error.message}
          </p>
        )}
        {!error && loading && <p className="px-2 py-1 text-xs text-slate-400">Loading...</p>}
        {!error && !loading && sessions.length === 0 && (
          <p className="px-2 py-1 text-xs text-slate-400">No conversations yet.</p>
        )}
        <ul className="space-y-0.5">
          {sessions.map((session) => {
            const active = session.session_id === currentId;
            return (
              <li key={session.session_id} className="group relative">
                <button
                  type="button"
                  onClick={() => onOpen(session.session_id)}
                  aria-current={active ? "true" : undefined}
                  className={`block w-full truncate rounded-lg px-3 py-2 pr-9 text-left text-sm ${
                    active
                      ? "bg-blue-50 font-medium text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
                      : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700"
                  }`}
                  title={session.title || "Untitled chat"}
                >
                  {session.title || "Untitled chat"}
                </button>
                <button
                  type="button"
                  aria-label={`Delete chat ${session.title || session.session_id}`}
                  onClick={() => {
                    if (window.confirm("Delete this conversation? This cannot be undone.")) onDelete(session.session_id);
                  }}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 opacity-0 hover:bg-slate-200 hover:text-red-600 focus:opacity-100 group-hover:opacity-100 dark:hover:bg-slate-600"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </aside>
  );
}
