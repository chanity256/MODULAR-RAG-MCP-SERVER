"use client";

import { buildModeOptions } from "@/lib/chat/modes";
import type { ChatCapabilities, RetrievalMode } from "@/lib/chat/types";

interface Props {
  capabilities: ChatCapabilities | null;
  value: RetrievalMode;
  onChange: (mode: RetrievalMode) => void;
  disabled?: boolean;
}

/**
 * Selected-state palette per retrieval mode (CHAT_MODE_COLORS_SPEC §3.1). Light mode pairs a pale
 * background with a dark tone of the same hue; dark mode pairs a translucent background over the
 * slate-800 container with a bright tone. Written as full literal class strings — Tailwind's JIT
 * only picks up complete class names, not interpolated ones.
 */
const MODE_TONE: Record<RetrievalMode, string> = {
  hybrid: "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-200",
  agentic_fast: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-200",
  agentic_deep: "bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-200",
};

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
                ? `font-medium shadow-sm ${MODE_TONE[option.mode]}`
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
