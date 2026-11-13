"use client";

import { X } from "lucide-react";
import { describeChatError } from "@/lib/chat/errors";
import type { ChatError } from "@/lib/chat/types";
import { TokenControl } from "@/components/dashboard/AsyncState";

interface Props {
  error: ChatError;
  onDismiss: () => void;
  onRetry: () => void;
  /** Called after a new token is stored. */
  onTokenSaved: () => void;
}

export default function ChatErrorAlert({ error, onDismiss, onRetry, onTokenSaved }: Props) {
  const view = describeChatError(error);
  return (
    <div
      role="alert"
      className="relative rounded-lg border border-red-200 bg-red-50 p-3 pr-9 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
    >
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss error"
        className="absolute right-2 top-2 rounded p-0.5 hover:bg-red-100 dark:hover:bg-red-900/40"
      >
        <X size={14} />
      </button>
      <div className="font-medium">{view.title}</div>
      <div className="mt-0.5 break-words text-xs">
        <span className="font-mono">{error.code}</span>: {error.message}
      </div>
      <p className="mt-1.5 text-xs opacity-90">{view.hint}</p>
      {view.action === "set_token" && (
        <TokenControl
          onSaved={() => {
            onTokenSaved();
            onDismiss();
          }}
        />
      )}
      {view.action === "retry" && (
        <button
          type="button"
          onClick={() => {
            onDismiss();
            onRetry();
          }}
          className="mt-2 rounded border border-red-300 px-2.5 py-1 text-xs hover:bg-red-100 dark:border-red-800 dark:hover:bg-red-900/40"
        >
          Retry last message
        </button>
      )}
    </div>
  );
}
