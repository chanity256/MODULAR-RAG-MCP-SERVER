"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { Send, Square } from "lucide-react";

interface Props {
  busy: boolean;
  onSend: (text: string) => boolean;
  onStop: () => void;
}

export default function Composer({ busy, onSend, onStop }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  const submit = () => {
    if (busy || !value.trim()) return;
    if (onSend(value)) setValue("");
  };

  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <textarea
        ref={ref}
        value={value}
        rows={1}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(event) => {
          // Enter confirms an IME candidate; only a plain Enter outside composition sends.
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder="Ask your knowledge base (Enter to send, Shift+Enter for a new line)"
        aria-label="Message"
        className="max-h-52 min-h-[2.5rem] flex-1 resize-none rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
      />
      {busy ? (
        <button
          type="button"
          onClick={onStop}
          aria-label="Stop generating"
          className="flex h-10 items-center gap-1.5 rounded-xl bg-red-600 px-3 text-sm text-white hover:bg-red-700"
        >
          <Square size={14} fill="currentColor" />
          Stop
        </button>
      ) : (
        <button
          type="submit"
          disabled={!value.trim()}
          aria-label="Send message"
          className="flex h-10 items-center gap-1.5 rounded-xl bg-blue-600 px-3 text-sm text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Send size={14} />
          Send
        </button>
      )}
    </form>
  );
}
