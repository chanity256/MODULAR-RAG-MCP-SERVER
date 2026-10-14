"use client";

import { buildModeOptions } from "@/lib/chat/modes";
import type { ChatCapabilities, RetrievalMode } from "@/lib/chat/types";

interface Props {
  capabilities: ChatCapabilities | null;
  value: RetrievalMode;
  onChange: (mode: RetrievalMode) => void;
  disabled?: boolean;
}

export default function ModeSelector({ capabilities, value, onChange, disabled = false }: Props) {
  const options = buildModeOptions(capabilities);
  if (options.length < 2) {
    return (
      <span className="text-xs text-slate-500 dark:text-slate-400" title="Retrieval mode">
        Hybrid retrieval
      </span>
    );
  }
  return (
    <div role="radiogroup" aria-label="Retrieval mode" className="inline-flex rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800">
      {options.map((option) => {
        const selected = option.mode === value;
        const off = disabled || option.disabled;
        return (
          <button
            key={option.mode}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-disabled={off}
            disabled={off}
            title={option.reason ?? option.label}
            onClick={() => onChange(option.mode)}
            className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
              selected
                ? "bg-white font-medium text-blue-700 shadow-sm dark:bg-slate-600 dark:text-blue-200"
                : "text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
            } ${off ? "cursor-not-allowed opacity-50" : ""}`}
          >
            {option.label}
            {option.disabled && <span className="ml-1 text-[10px] uppercase">off</span>}
          </button>
        );
      })}
    </div>
  );
}
