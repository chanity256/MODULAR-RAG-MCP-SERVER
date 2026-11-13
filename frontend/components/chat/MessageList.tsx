"use client";

import { useEffect, useRef } from "react";
import { MessagesSquare } from "lucide-react";
import type { ChatMessage } from "@/lib/chat/types";
import MessageBubble from "./MessageBubble";

const STICK_THRESHOLD_PX = 80;

export default function MessageList({ messages }: { messages: readonly ChatMessage[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  const last = messages[messages.length - 1];
  const tail = `${messages.length}:${last?.content.length ?? 0}:${last?.status ?? ""}:${last?.logs.length ?? 0}`;

  // Follow new output unless the user scrolled up to read earlier text.
  useEffect(() => {
    const el = containerRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [tail]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
        <MessagesSquare size={32} className="text-slate-300 dark:text-slate-600" />
        <h1 className="text-lg font-semibold text-slate-800 dark:text-slate-100">Ask your knowledge base</h1>
        <p className="max-w-md text-sm text-slate-500 dark:text-slate-400">
          Answers are generated from retrieved documents and cite their sources as [1], [2]. Pick a retrieval mode
          above, then type a question below.
        </p>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={(event) => {
        const el = event.currentTarget;
        stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX;
      }}
      className="min-h-0 flex-1 overflow-y-auto"
      aria-live="off"
    >
      <div className="mx-auto max-w-4xl space-y-5 px-4 py-6">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
      </div>
    </div>
  );
}
