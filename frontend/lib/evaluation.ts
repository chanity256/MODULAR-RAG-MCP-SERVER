import { requestJson, normalizeTaskSubmitted, type TaskSubmitted } from "./data";
import { normalizeTask, type TaskRecord } from "./tasks";

type Rec = Record<string, unknown>;

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export const EVALUATOR_BACKENDS = ["composite", "custom", "ragas"] as const;
export type EvaluatorBackend = (typeof EVALUATOR_BACKENDS)[number];

export interface EvaluationFormValues {
  backend: EvaluatorBackend;
  testSetPath: string;
  /** Raw text of the Top-K field; empty or `0` means "use the configured default". */
  topK: string;
  collection: string;
}

export interface EvaluationRequestBody {
  backend: EvaluatorBackend;
  test_set_path?: string;
  top_k?: number;
  filters?: { collection: string };
}

/** Returns an error message when the form cannot be submitted. */
export function validateEvaluationForm(values: EvaluationFormValues): string | null {
  const raw = values.topK.trim();
  if (raw !== "" && !/^\d+$/.test(raw)) return "Top-K must be a non-negative integer (0 = configured default).";
  return null;
}

/** Omit empty optional fields so the backend applies its configured defaults. */
export function buildEvaluationRequest(values: EvaluationFormValues): EvaluationRequestBody {
  const body: EvaluationRequestBody = { backend: values.backend };
  const path = values.testSetPath.trim();
  if (path) body.test_set_path = path;
  const topK = Number.parseInt(values.topK.trim(), 10);
  if (Number.isFinite(topK) && topK > 0) body.top_k = topK;
  const collection = values.collection.trim();
  if (collection) body.filters = { collection };
  return body;
}

export async function submitEvaluation(values: EvaluationFormValues): Promise<TaskSubmitted> {
  return normalizeTaskSubmitted(
    await requestJson("POST", "/api/v1/evaluations", null, { json: buildEvaluationRequest(values) }),
  );
}

export interface QueryEvalDetail {
  /** Position in the report; queries are not guaranteed unique. */
  index: number;
  query: string;
  retrievedIds: string[];
  expectedChunkIds: string[];
  metrics: Record<string, number>;
  error: string | null;
}

export interface EvalReportView {
  total: number;
  evaluated: number;
  failed: number;
  metrics: Record<string, number>;
  perQuery: QueryEvalDetail[];
}

function metricMap(value: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (!isRecord(value)) return out;
  for (const [key, metric] of Object.entries(value)) {
    const n = finite(metric);
    if (n !== null) out[key] = n;
  }
  return out;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

/** Normalize `EvalReport.to_dict()` (the `detail.result` of an evaluation task). */
export function normalizeEvalReport(raw: unknown): EvalReportView | null {
  if (!isRecord(raw)) return null;
  const perQuery = (Array.isArray(raw.per_query) ? raw.per_query : []).map((item, index): QueryEvalDetail => {
    const q = isRecord(item) ? item : {};
    return {
      index,
      query: str(q.query) ?? "",
      retrievedIds: stringList(q.retrieved_ids),
      expectedChunkIds: stringList(q.expected_chunk_ids),
      metrics: metricMap(q.metrics),
      error: str(q.error),
    };
  });
  return {
    total: finite(raw.total) ?? perQuery.length,
    evaluated: finite(raw.evaluated) ?? 0,
    failed: finite(raw.failed) ?? 0,
    metrics: metricMap(raw.metrics),
    perQuery,
  };
}

export function formatMetric(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "-";
}

/** `true` when the first retrieved id is one of the expected chunks. */
export function isTopHit(detail: QueryEvalDetail): boolean {
  const first = detail.retrievedIds[0];
  return first !== undefined && detail.expectedChunkIds.includes(first);
}

export interface EvaluationRun {
  task: TaskRecord;
  taskId: string;
  status: TaskRecord["status"];
  createdAt: string | null;
  finishedAt: string | null;
  backend: string;
  testSetPath: string;
  topK: number | null;
  collection: string | null;
  report: EvalReportView | null;
  error: string | null;
}

/** Project a stored evaluation task into a history row. */
export function toEvaluationRun(task: TaskRecord): EvaluationRun {
  const params = task.detail.params;
  const filters = isRecord(params.filters) ? params.filters : null;
  return {
    task,
    taskId: task.task_id,
    status: task.status,
    createdAt: task.created_at,
    finishedAt: task.finished_at,
    backend: str(params.backend) ?? "-",
    testSetPath: str(params.test_set_path) ?? "",
    topK: finite(params.top_k),
    collection: filters ? str(filters.collection) : null,
    report: normalizeEvalReport(task.detail.result),
    error: task.detail.error?.message ?? null,
  };
}

/** Union of metric names across runs, in first-seen order (history columns / trend keys). */
export function collectMetricKeys(reports: readonly (EvalReportView | null)[]): string[] {
  const keys: string[] = [];
  for (const report of reports) {
    if (!report) continue;
    for (const key of Object.keys(report.metrics)) if (!keys.includes(key)) keys.push(key);
  }
  return keys;
}

export interface TrendPoint {
  /** Run position (1-based) in the plotted window. */
  run: number;
  value: number | null;
}

export interface TrendSeries {
  key: string;
  points: TrendPoint[];
}

export interface EvaluationTrend {
  runs: number;
  series: TrendSeries[];
}

const TREND_MAX_RUNS = 10;
const TREND_MAX_METRICS = 3;

/** Point sequences for the history trend chart: the last `maxRuns` succeeded runs in time-ascending
 *  order, first `maxMetrics` keys (`collectMetricKeys` order, matching Streamlit's "前 3 个指标").
 *  `value` is null when a run lacks that metric. Returns null with fewer than 2 runs — a line
 *  needs two points. */
export function trendSeries(
  runs: readonly EvaluationRun[],
  maxRuns: number = TREND_MAX_RUNS,
  maxMetrics: number = TREND_MAX_METRICS,
): EvaluationTrend | null {
  const window = runs
    .filter((run) => run.status === "succeeded" && run.report)
    .sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? ""))
    .slice(-maxRuns);
  if (window.length < 2) return null;
  const keys = collectMetricKeys(window.map((run) => run.report)).slice(0, maxMetrics);
  if (keys.length === 0) return null;
  return {
    runs: window.length,
    series: keys.map((key) => ({
      key,
      points: window.map((run, index) => ({ run: index + 1, value: run.report?.metrics[key] ?? null })),
    })),
  };
}

export function runFromRaw(raw: unknown): EvaluationRun {
  return toEvaluationRun(normalizeTask(raw));
}
