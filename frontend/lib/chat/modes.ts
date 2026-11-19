import type { ChatCapabilities, RequestedRetrieval } from "./types";

export interface ModeOption {
  mode: RequestedRetrieval;
  label: string;
  disabled: boolean;
  /** Why the option is disabled; shown as a tooltip / hint. */
  reason?: string;
}

const LABELS: Record<RequestedRetrieval, string> = {
  auto: "Auto",
  hybrid: "Hybrid",
  agentic_fast: "Agentic (fast)",
  agentic_deep: "Agentic (deep)",
};

/** English label for a mode value coming from a frame or trace. */
export function modeLabel(mode: string | null | undefined): string {
  const key = mode as RequestedRetrieval | undefined;
  return key && LABELS[key] ? LABELS[key] : (mode ?? "");
}

/** Why an auto turn fell back to `fallback_retrieval` (MODE_ROUTER_SPEC §3.2). */
export const FALLBACK_REASON_LABELS: Record<string, string> = {
  timeout: "router timed out",
  unreachable: "router unreachable",
  invalid_output: "invalid router output",
  low_confidence: "low confidence",
  error: "router error",
};

/**
 * Selector options for the server's capabilities. The Auto option appears when the server reports
 * `auto_routing`; hybrid is always offered; agentic modes are hidden when the sidecar is off, and
 * DEEP stays visible but disabled when `allow_deep` is false. Before capabilities load (or if they
 * fail) only hybrid is offered.
 */
export function buildModeOptions(capabilities: ChatCapabilities | null): ModeOption[] {
  const options: ModeOption[] = [];
  if (capabilities?.auto_routing) {
    options.push({ mode: "auto", label: LABELS.auto, disabled: false });
  }
  options.push({ mode: "hybrid", label: LABELS.hybrid, disabled: false });
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
export function resolveMode(
  selected: RequestedRetrieval | null,
  capabilities: ChatCapabilities | null,
): RequestedRetrieval {
  const usable = new Set(buildModeOptions(capabilities).filter((o) => !o.disabled).map((o) => o.mode));
  if (selected && usable.has(selected)) return selected;
  if (capabilities && usable.has(capabilities.default_retrieval)) return capabilities.default_retrieval;
  return "hybrid";
}
