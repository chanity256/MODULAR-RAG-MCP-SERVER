import type { StageRecord, StageTiming, TraceSummary } from "./types";

/** Waterfall stage order, kept in step with the backend's `_INGEST_STAGES` / `_QUERY_STAGES`. */
export const INGEST_STAGES = ["load", "split", "transform", "embed", "store", "upsert"] as const;
export const QUERY_STAGES = [
  "query_processing",
  "dense_retrieval",
  "sparse_retrieval",
  "fusion",
  "hybrid",
  "rerank",
] as const;

/** Source shown for a query trace that carries no `metadata.routes` (plain hybrid retrieval). */
export const DEFAULT_SOURCE = "hybrid";
export const ALL_SOURCES = "all";

type Rec = Record<string, unknown>;

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Source tag of a trace: `metadata.routes` ("chat" / "agentic" / ...), default "hybrid". */
export function traceSource(trace: Pick<TraceSummary, "routes" | "metadata">): string {
  const fromSummary = typeof trace.routes === "string" ? trace.routes : null;
  const fromMetadata = typeof trace.metadata?.routes === "string" ? trace.metadata.routes : null;
  const value = (fromSummary ?? fromMetadata ?? "").trim().toLowerCase();
  return value || DEFAULT_SOURCE;
}

/** Value for the server `routes` param: "all" sends nothing, the client "hybrid" label is the server's `default`. */
export function routesParam(source: string): string | undefined {
  if (!source || source === ALL_SOURCES) return undefined;
  return source === DEFAULT_SOURCE ? "default" : source;
}

/** Sources always offered in the filter, since a server-filtered list only contains the selected one. */
export const KNOWN_SOURCES = [DEFAULT_SOURCE, "chat", "agentic"] as const;

/** Distinct sources present in `items`: default source first, the rest alphabetical. */
export function sourceOptions(items: readonly Pick<TraceSummary, "routes" | "metadata">[]): string[] {
  const found = new Set(items.map(traceSource));
  const rest = [...found].filter((source) => source !== DEFAULT_SOURCE).sort();
  return found.has(DEFAULT_SOURCE) ? [DEFAULT_SOURCE, ...rest] : rest;
}

export function filterBySource<T extends Pick<TraceSummary, "routes" | "metadata">>(
  items: readonly T[],
  source: string,
): T[] {
  if (!source || source === ALL_SOURCES) return [...items];
  return items.filter((item) => traceSource(item) === source);
}

/** Last stage with the given name (a re-run stage supersedes earlier ones). */
export function findStage(stages: readonly StageRecord[], name: string): StageRecord | undefined {
  for (let i = stages.length - 1; i >= 0; i--) {
    if (stages[i].stage === name) return stages[i];
  }
  return undefined;
}

/** `elapsed_ms`, falling back to `details.elapsed_ms`, else 0 (same rule as the backend). */
export function stageElapsedMs(stage: StageRecord): number {
  const own = typeof stage.elapsed_ms === "number" ? stage.elapsed_ms : 0;
  if (own) return own;
  const nested = stage.details?.elapsed_ms;
  return typeof nested === "number" && Number.isFinite(nested) ? nested : 0;
}

export interface WaterfallRow {
  stage: string;
  elapsed_ms: number;
  /** Share of the summed stage time, 0-100. */
  percent: number;
  /** Bar width relative to the slowest stage, 0-100. */
  width: number;
}

/**
 * Rows for the stage timing chart.
 *
 * The backend `timings` only cover the well-known stages of the trace type, so stages it
 * does not know (`chat_*`, `agentic_search`, anything new) are appended from the raw stage
 * list; same-named stages are summed. Zero-duration stages are dropped.
 */
export function buildWaterfall(
  timings: readonly StageTiming[],
  stages: readonly StageRecord[],
  knownStages: readonly string[] = [],
): WaterfallRow[] {
  const totals = new Map<string, number>();
  const add = (name: string, ms: number) => {
    if (ms > 0) totals.set(name, (totals.get(name) ?? 0) + ms);
  };

  for (const timing of timings) add(timing.stage, timing.elapsed_ms);

  // With backend timings present, known stages are already covered by them.
  const fromStages = new Map<string, number>();
  for (const stage of stages) {
    if (!stage.stage) continue;
    if (timings.length > 0 && knownStages.includes(stage.stage)) continue;
    fromStages.set(stage.stage, (fromStages.get(stage.stage) ?? 0) + stageElapsedMs(stage));
  }
  for (const [name, ms] of fromStages) if (!totals.has(name)) add(name, ms);

  const entries = [...totals.entries()];
  const sum = entries.reduce((acc, [, ms]) => acc + ms, 0);
  const max = entries.reduce((acc, [, ms]) => Math.max(acc, ms), 0);
  return entries.map(([stage, ms]) => ({
    stage,
    elapsed_ms: ms,
    percent: sum > 0 ? (ms / sum) * 100 : 0,
    width: max > 0 ? (ms / max) * 100 : 0,
  }));
}

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  if (value >= 100) return `${Math.round(value)} ms`;
  if (value >= 1) return `${value.toFixed(1)} ms`;
  return `${value.toFixed(2)} ms`;
}

export function formatPercent(value: number): string {
  return `${value < 10 ? value.toFixed(1) : Math.round(value)}%`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4);
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

export interface Hit {
  chunk_id: string;
  score: number | null;
}

function toHit(raw: unknown): Hit {
  const r = isRecord(raw) ? raw : {};
  return {
    chunk_id: r.chunk_id === undefined || r.chunk_id === null ? "-" : String(r.chunk_id),
    score: typeof r.score === "number" ? r.score : null,
  };
}

/** Ranked hits of a stage: `details.preview`, else `details.after`. */
export function stageHits(stage: StageRecord | undefined): Hit[] {
  const details = stage?.details ?? {};
  const raw = Array.isArray(details.preview) && details.preview.length > 0 ? details.preview : details.after;
  return Array.isArray(raw) ? raw.map(toHit) : [];
}

export interface RerankRow {
  after: number;
  before: number | null;
  /** Positive = moved up. Null when the chunk was not in the pre-rerank list. */
  delta: number | null;
  marker: "up" | "down" | "same" | "new";
  chunk_id: string;
  score: number | null;
}

export function rerankComparison(details: Rec): RerankRow[] {
  const before = new Map<string, number>();
  (Array.isArray(details.before) ? details.before : []).forEach((item, index) => {
    const hit = toHit(item);
    if (!before.has(hit.chunk_id)) before.set(hit.chunk_id, index + 1);
  });
  return (Array.isArray(details.after) ? details.after : []).map((item, index) => {
    const hit = toHit(item);
    const previous = before.get(hit.chunk_id) ?? null;
    const delta = previous === null ? null : previous - (index + 1);
    const marker = delta === null ? "new" : delta > 0 ? "up" : delta < 0 ? "down" : "same";
    return { after: index + 1, before: previous, delta, marker, chunk_id: hit.chunk_id, score: hit.score };
  });
}

export interface AgenticRouteRow {
  stage: string;
  dense: unknown;
  sparse: unknown;
  agentic: unknown;
}

export interface AgenticRoutes {
  rows: AgenticRouteRow[];
  trigger: Rec | null;
}

/** Agentic routing info of hybrid / fusion stages; null when the trace has none. */
export function agenticRoutes(stages: readonly StageRecord[]): AgenticRoutes | null {
  const hybrid = findStage(stages, "hybrid")?.details ?? {};
  const fusion = findStage(stages, "fusion")?.details ?? {};
  const trigger = isRecord(hybrid.agentic_trigger) ? hybrid.agentic_trigger : null;
  const rows: AgenticRouteRow[] = [];
  for (const [name, details] of [["hybrid", hybrid], ["fusion", fusion]] as const) {
    const routes = details.routes;
    if (isRecord(routes) && "agentic" in routes) {
      rows.push({ stage: name, dense: routes.dense, sparse: routes.sparse, agentic: routes.agentic });
    }
  }
  return trigger || rows.length > 0 ? { rows, trigger } : null;
}

/** Top-level primitive details as `key=value` pairs, for one-line stage summaries. */
export function scalarDetails(details: Rec, max = 4): [string, string][] {
  const out: [string, string][] = [];
  for (const [key, value] of Object.entries(details)) {
    if (out.length >= max) break;
    if (value === null || value === undefined) continue;
    if (typeof value === "string" && value.length > 60) continue;
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      out.push([key, formatValue(value)]);
    }
  }
  return out;
}

/** `provider` of a stage: a top-level extra field, else `details.provider`. */
export function stageProvider(stage: StageRecord): string | null {
  const own = stage.provider;
  if (typeof own === "string" && own) return own;
  const nested = stage.details?.provider;
  return typeof nested === "string" && nested ? nested : null;
}

export function ingestionStatus(trace: Pick<TraceSummary, "skipped">): "skipped" | "success" {
  return trace.skipped ? "skipped" : "success";
}
