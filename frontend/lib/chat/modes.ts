import type { ChatCapabilities, RetrievalMode } from "./types";

export interface ModeOption {
  mode: RetrievalMode;
  label: string;
  disabled: boolean;
  /** Why the option is disabled; shown as a tooltip / hint. */
  reason?: string;
}

const LABELS: Record<RetrievalMode, string> = {
  hybrid: "Hybrid",
  agentic_fast: "Agentic (fast)",
  agentic_deep: "Agentic (deep)",
};

/**
 * Selector options for the server's capabilities. Hybrid is always offered; agentic modes are
 * hidden when the sidecar is off, and DEEP stays visible but disabled when `allow_deep` is false.
 * Before capabilities load (or if they fail) only hybrid is offered.
 */
export function buildModeOptions(capabilities: ChatCapabilities | null): ModeOption[] {
  const options: ModeOption[] = [{ mode: "hybrid", label: LABELS.hybrid, disabled: false }];
  if (!capabilities || !capabilities.agentic_enabled) return options;

  const served = new Set(capabilities.retrievals);
  if (served.has("agentic_fast")) {
    options.push({ mode: "agentic_fast", label: LABELS.agentic_fast, disabled: false });
  }
  if (served.has("agentic_deep")) {
    options.push(
      capabilities.allow_deep
        ? { mode: "agentic_deep", label: LABELS.agentic_deep, disabled: false }
        : {
            mode: "agentic_deep",
            label: LABELS.agentic_deep,
            disabled: true,
            reason: "Deep search is disabled on the server (agentic_search.allow_deep = false)",
          },
    );
  }
  return options;
}

/** Selected mode if it is currently usable, else the server default, else hybrid. */
export function resolveMode(selected: RetrievalMode | null, capabilities: ChatCapabilities | null): RetrievalMode {
  const usable = new Set(buildModeOptions(capabilities).filter((o) => !o.disabled).map((o) => o.mode));
  if (selected && usable.has(selected)) return selected;
  if (capabilities && usable.has(capabilities.default_retrieval)) return capabilities.default_retrieval;
  return "hybrid";
}
