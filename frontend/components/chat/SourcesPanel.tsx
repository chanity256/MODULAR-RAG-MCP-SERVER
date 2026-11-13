"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";
import { citationLabel, formatScore } from "@/lib/chat/citations";
import type { Citation } from "@/lib/chat/types";

const SNIPPET_CHARS = 280;

export function sourceElementId(messageId: string, n: number): string {
  return `src-${messageId}-${n}`;
}

interface Props {
  messageId: string;
  citations: readonly Citation[];
  /** Citation number highlighted by clicking a [n] marker; `nonce` re-triggers the same number. */
  active: { n: number; nonce: number } | null;
}

export default function SourcesPanel({ messageId, citations, active }: Props) {
  const [open, setOpen] = useState(false);
  const [seenNonce, setSeenNonce] = useState(0);
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());

  // A new marker click opens the panel (adjusting state during render, not in an effect).
  if (active && active.nonce !== seenNonce) {
    setSeenNonce(active.nonce);
    setOpen(true);
  }

  useEffect(() => {
    if (!active) return;
    const frame = requestAnimationFrame(() => {
      document.getElementById(sourceElementId(messageId, active.n))?.scrollIntoView?.({
        block: "nearest",
        behavior: "smooth",
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [active, messageId]);

  const isOpen = open;
  if (citations.length === 0) return null;

  return (
    <section aria-label="Sources" className="mt-3 rounded-lg border border-slate-200 dark:border-slate-700">
      <button
        type="button"
        onClick={() => setOpen(!isOpen)}
        aria-expanded={isOpen}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-xs font-medium text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
      >
        {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        Sources ({citations.length})
      </button>
      {isOpen && (
        <ol className="space-y-2 border-t border-slate-200 p-2 dark:border-slate-700">
          {citations.map((citation) => {
            const highlighted = active?.n === citation.id;
            const long = citation.text.length > SNIPPET_CHARS;
            const showAll = expanded.has(citation.id);
            const snippet = long && !showAll ? `${citation.text.slice(0, SNIPPET_CHARS)}...` : citation.text;
            return (
              <li
                key={`${citation.id}-${citation.chunk_id}`}
                id={sourceElementId(messageId, citation.id)}
                data-highlighted={highlighted || undefined}
                className={`rounded-md border p-2 text-xs ${
                  highlighted
                    ? "border-blue-400 bg-blue-50 ring-2 ring-blue-300 dark:border-blue-500 dark:bg-blue-900/20 dark:ring-blue-700"
                    : "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/60"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="rounded bg-blue-100 px-1.5 py-0.5 font-medium text-blue-700 dark:bg-blue-900/50 dark:text-blue-300">
                    [{citation.id}]
                  </span>
                  <FileText size={12} className="shrink-0 text-slate-400" />
                  <span className="truncate font-medium text-slate-800 dark:text-slate-100" title={citation.source}>
                    {citationLabel(citation)}
                  </span>
                  <span className="ml-auto shrink-0 text-slate-500 dark:text-slate-400" title="Relevance score">
                    score {formatScore(citation.score)}
                  </span>
                </div>
                {citation.text && (
                  <p className="mt-1.5 whitespace-pre-wrap break-words text-slate-600 dark:text-slate-300">{snippet}</p>
                )}
                <div className="mt-1 flex items-center gap-3 text-[10px] text-slate-400">
                  {citation.chunk_id && (
                    <span className="truncate font-mono" title={citation.chunk_id}>
                      chunk {citation.chunk_id}
                    </span>
                  )}
                  {long && (
                    <button
                      type="button"
                      className="shrink-0 text-blue-600 hover:underline dark:text-blue-400"
                      onClick={() => {
                        const next = new Set(expanded);
                        if (showAll) next.delete(citation.id);
                        else next.add(citation.id);
                        setExpanded(next);
                      }}
                    >
                      {showAll ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
