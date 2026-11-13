import type {
  CollectionStats,
  ComponentCard,
  Overview,
  Page,
  StageRecord,
  StageTiming,
  TraceDetail,
  TraceSummary,
} from "./types";

type Rec = Record<string, unknown>;

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function int(value: unknown): number {
  return num(value) ?? 0;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function normalizeTraceSummary(raw: unknown): TraceSummary {
  const r = isRecord(raw) ? raw : {};
  return {
    trace_id: str(r.trace_id) ?? "",
    trace_type: str(r.trace_type) ?? "",
    started_at: str(r.started_at),
    finished_at: str(r.finished_at),
    total_elapsed_ms: num(r.total_elapsed_ms),
    metadata: isRecord(r.metadata) ? r.metadata : {},
    stage_count: int(r.stage_count),
    query: str(r.query),
    source_path: str(r.source_path),
    collection: str(r.collection),
    routes: str(r.routes),
    source: str(r.source),
    skipped: typeof r.skipped === "boolean" ? r.skipped : null,
    chunks: num(r.chunks),
    images: num(r.images),
  };
}

export function normalizeStage(raw: unknown): StageRecord {
  const r = isRecord(raw) ? raw : {};
  return {
    ...r,
    stage: str(r.stage) ?? "",
    elapsed_ms: num(r.elapsed_ms),
    method: str(r.method),
    details: isRecord(r.details) ? r.details : {},
  };
}

function normalizeTiming(raw: unknown): StageTiming {
  const r = isRecord(raw) ? raw : {};
  return { stage: str(r.stage) ?? "", elapsed_ms: num(r.elapsed_ms) ?? 0 };
}

export function normalizeTraceDetail(raw: unknown): TraceDetail {
  const r = isRecord(raw) ? raw : {};
  return {
    ...normalizeTraceSummary(raw),
    stages: list(r.stages).map(normalizeStage),
    timings: list(r.timings).map(normalizeTiming),
  };
}

export function normalizeTracePage(raw: unknown): Page<TraceSummary> {
  const r = isRecord(raw) ? raw : {};
  const items = list(r.items).map(normalizeTraceSummary);
  return {
    items,
    total: num(r.total) ?? items.length,
    limit: num(r.limit) ?? items.length,
    offset: int(r.offset),
  };
}

function normalizeCollection(raw: unknown): CollectionStats {
  const r = isRecord(raw) ? raw : {};
  return {
    name: str(r.name) ?? "",
    document_count: int(r.document_count),
    chunk_count: int(r.chunk_count),
    image_count: int(r.image_count),
  };
}

function normalizeCard(raw: unknown): ComponentCard {
  const r = isRecord(raw) ? raw : {};
  return {
    name: str(r.name) ?? "",
    provider: str(r.provider) ?? "",
    model: str(r.model),
    headline: str(r.headline) ?? "",
    extras: isRecord(r.extras) ? r.extras : {},
  };
}

export function normalizeOverview(raw: unknown): Overview {
  const r = isRecord(raw) ? raw : {};
  const totals = isRecord(r.totals) ? r.totals : {};
  return {
    components: list(r.components).map(normalizeCard),
    totals: {
      collections: int(totals.collections),
      documents: int(totals.documents),
      chunks: int(totals.chunks),
      images: int(totals.images),
    },
    collections: list(r.collections).map(normalizeCollection),
    latest_ingestion: isRecord(r.latest_ingestion) ? normalizeTraceSummary(r.latest_ingestion) : null,
    latest_query: isRecord(r.latest_query) ? normalizeTraceSummary(r.latest_query) : null,
    warnings: list(r.warnings).filter((w): w is string => typeof w === "string"),
  };
}
