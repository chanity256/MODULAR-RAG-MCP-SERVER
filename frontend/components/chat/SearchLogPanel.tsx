"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { SearchLogEntry } from "@/lib/chat/types";

const LEVEL_CLASS: Record<string, string> = {
  error: "text-red-600 dark:text-red-400",
  warning: "text-amber-600 dark:text-amber-400",
  warn: "text-amber-600 dark:text-amber-400",
  success: "text-emerald-600 dark:text-emerald-400",
};

export default function SearchLogPanel({
  logs,
  searchComplete,
  searchError,
}: {
  logs: readonly SearchLogEntry[];
  searchComplete?: string;
  searchError?: string;
}) {
  const [open, setOpen] = useState(false);
  if (logs.length === 0 && !searchError) return null;

  return (
    <section aria-label="Search log" className="mb-2 rounded-lg border border-slate-200 dark:border-slate-700">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-xs text-slate-500 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        Search log ({logs.length})
        {searchError && <span className="ml-2 text-red-600 dark:text-red-400">search failed</span>}
        {!searchError && searchComplete && <span className="ml-2 text-emerald-600 dark:text-emerald-400">done</span>}
      </button>
      {open && (
        <ul className="max-h-48 space-y-0.5 overflow-y-auto border-t border-slate-200 px-3 py-2 font-mono text-[11px] dark:border-slate-700">
          {logs.map((entry, index) => (
            <li key={index} className={LEVEL_CLASS[entry.level.toLowerCase()] ?? "text-slate-600 dark:text-slate-300"}>
              {entry.message}
            </li>
          ))}
          {searchError && <li className="text-red-600 dark:text-red-400">{searchError}</li>}
        </ul>
      )}
    </section>
  );
}
