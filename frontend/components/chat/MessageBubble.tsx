"use client";

import { memo, useCallback, useState } from "react";
import { AlertTriangle, Bot, Loader2, User } from "lucide-react";
import { formatUsage } from "@/lib/chat/errors";
import { stageLabel } from "@/lib/chat/status";
import type { ChatMessage } from "@/lib/chat/types";
import MarkdownContent from "./MarkdownContent";
import SearchLogPanel from "./SearchLogPanel";
import SourcesPanel from "./SourcesPanel";

export const UNGROUNDED_NOTICE =
  "Not based on your knowledge base: this answer was generated without retrieved sources (plain chat or a retrieval fallback). Verify it before relying on it.";

function UserBubble({ message }: { message: ChatMessage }) {
  return (
    <div className="flex justify-end gap-2">
      <div className="max-w-[80%] whitespace-pre-wrap break-words rounded-2xl rounded-tr-sm bg-blue-600 px-4 py-2.5 text-sm text-white">
        {message.content}
      </div>
      <User size={18} className="mt-2 shrink-0 text-slate-400" aria-hidden />
    </div>
  );
}

function AssistantBubble({ message }: { message: ChatMessage }) {
  const streaming = message.status === "streaming";
  const [active, setActive] = useState<{ n: number; nonce: number } | null>(null);
  const onCitationClick = useCallback((n: number) => setActive((prev) => ({ n, nonce: (prev?.nonce ?? 0) + 1 })), []);

  const status = streaming ? stageLabel(message.stage, message.stageMessage) : null;
  const usage = formatUsage(message.usage);
  const showMeta = !streaming && (message.rewrittenQuery || usage);

  return (
    <div className="flex gap-2">
      <Bot size={18} className="mt-2 shrink-0 text-slate-400" aria-hidden />
      <div className="min-w-0 max-w-[85%] flex-1">
        {!streaming && message.grounded === false && (
          <div
            role="note"
            data-testid="ungrounded-banner"
            className="mb-2 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
          >
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <span>{UNGROUNDED_NOTICE}</span>
          </div>
        )}

        <SearchLogPanel logs={message.logs} searchComplete={message.searchComplete} searchError={message.searchError} />

        <div className="rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-800">
          {message.content === "" && streaming ? (
            <div role="status" className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
              <Loader2 size={14} className="animate-spin" />
              {status ?? "Connecting..."}
            </div>
          ) : (
            <MarkdownContent
              content={message.content}
              streaming={streaming}
              citations={message.citations}
              onCitationClick={onCitationClick}
            />
          )}
          {streaming && message.content !== "" && (
            <div role="status" className="mt-2 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              <Loader2 size={12} className="animate-spin" />
              {status ?? "Generating the answer..."}
            </div>
          )}
          {message.status === "cancelled" && (
            <div className="mt-2 text-xs italic text-slate-500 dark:text-slate-400">Stopped. The answer above is partial.</div>
          )}
        </div>

        <SourcesPanel messageId={message.id} citations={message.citations} active={active} />

        {showMeta && (
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 px-1 text-[11px] text-slate-400 dark:text-slate-500">
            {message.rewrittenQuery && (
              <span title="The question as rewritten for retrieval">Searched as: {message.rewrittenQuery}</span>
            )}
            {usage && <span>{usage}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

function MessageBubbleImpl({ message }: { message: ChatMessage }) {
  return message.role === "user" ? <UserBubble message={message} /> : <AssistantBubble message={message} />;
}

const MessageBubble = memo(MessageBubbleImpl);
export default MessageBubble;
