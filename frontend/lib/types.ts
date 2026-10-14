/** JSON shapes of the read-only dashboard endpoints (mirrors `src/api/schemas`). */

export interface ErrorBody {
  code: string;
  message: string;
  details: unknown;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface CollectionStats {
  name: string;
  document_count: number;
  chunk_count: number;
  image_count: number;
}

export interface ComponentCard {
  name: string;
  provider: string;
  model: string | null;
  headline: string;
  extras: Record<string, unknown>;
}

export interface OverviewTotals {
  collections: number;
  documents: number;
  chunks: number;
  images: number;
}

export type TraceType = "ingestion" | "query";

export interface TraceSummary {
  trace_id: string;
  trace_type: string;
  started_at: string | null;
  finished_at: string | null;
  total_elapsed_ms: number | null;
  metadata: Record<string, unknown>;
  stage_count: number;
  query: string | null;
  source_path: string | null;
  collection: string | null;
  routes: string | null;
  skipped: boolean | null;
  chunks: number | null;
  images: number | null;
}

/** One trace stage; stages carry unmodelled extra fields (e.g. `provider`), kept as-is. */
export interface StageRecord {
  stage: string;
  elapsed_ms: number | null;
  method: string | null;
  details: Record<string, unknown>;
  [extra: string]: unknown;
}

export interface StageTiming {
  stage: string;
  elapsed_ms: number;
}

export interface TraceDetail extends TraceSummary {
  stages: StageRecord[];
  timings: StageTiming[];
}

export interface Overview {
  components: ComponentCard[];
  totals: OverviewTotals;
  collections: CollectionStats[];
  latest_ingestion: TraceSummary | null;
  latest_query: TraceSummary | null;
  warnings: string[];
}
