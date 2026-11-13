import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildEvaluationRequest,
  collectMetricKeys,
  formatMetric,
  isTopHit,
  normalizeEvalReport,
  submitEvaluation,
  toEvaluationRun,
  trendSeries,
  validateEvaluationForm,
  type EvaluationFormValues,
} from "./evaluation";
import { normalizeTask } from "./tasks";

const form = (over: Partial<EvaluationFormValues> = {}): EvaluationFormValues => ({
  backend: "composite",
  testSetPath: "",
  topK: "0",
  collection: "",
  ...over,
});

const REPORT = {
  total: 3,
  evaluated: 2,
  failed: 1,
  metrics: { hit_rate: 0.5, mrr: 0.25, weird: "x" },
  per_query: [
    { query: "q1", retrieved_ids: ["c1", "c2"], expected_chunk_ids: ["c1"], metrics: { hit_rate: 1, mrr: 1 }, error: null },
    { query: "q2", retrieved_ids: ["c9"], expected_chunk_ids: ["c3"], metrics: { hit_rate: 0, mrr: 0 }, error: null },
    { query: "q3", retrieved_ids: [], expected_chunk_ids: [], metrics: {}, error: "boom" },
  ],
};

beforeEach(() => vi.stubEnv("NEXT_PUBLIC_API_BASE", ""));
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("buildEvaluationRequest", () => {
  it("omits optional fields so the backend defaults apply", () => {
    expect(buildEvaluationRequest(form())).toEqual({ backend: "composite" });
  });

  it("includes trimmed path, positive top-k and collection filter", () => {
    expect(
      buildEvaluationRequest(form({ backend: "ragas", testSetPath: " tests/g.json ", topK: "5", collection: " kb " })),
    ).toEqual({ backend: "ragas", test_set_path: "tests/g.json", top_k: 5, filters: { collection: "kb" } });
  });

  it("validates Top-K", () => {
    expect(validateEvaluationForm(form({ topK: "" }))).toBeNull();
    expect(validateEvaluationForm(form({ topK: "7" }))).toBeNull();
    expect(validateEvaluationForm(form({ topK: "-1" }))).not.toBeNull();
    expect(validateEvaluationForm(form({ topK: "1.5" }))).not.toBeNull();
  });

  it("submits to the evaluations endpoint", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({ task_id: "e1", status: "pending", task: { task_id: "e1", kind: "evaluation", status: "pending", version: 0, detail: {} } }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const submitted = await submitEvaluation(form({ topK: "3" }));
    expect(submitted.task_id).toBe("e1");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/evaluations");
    expect(JSON.parse(init.body as string)).toEqual({ backend: "composite", top_k: 3 });
  });

  it("surfaces a 409 conflict with the active task", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ success: false, error: { code: "conflict", message: "An evaluation task is already active", details: { task_id: "e0" } } }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    await expect(submitEvaluation(form())).rejects.toMatchObject({ status: 409, details: { task_id: "e0" } });
  });
});

describe("normalizeEvalReport", () => {
  it("maps the EvalReport.to_dict() shape and drops non-numeric metrics", () => {
    const report = normalizeEvalReport(REPORT)!;
    expect(report.total).toBe(3);
    expect(report.metrics).toEqual({ hit_rate: 0.5, mrr: 0.25 });
    expect(report.perQuery).toHaveLength(3);
    expect(report.perQuery[0]).toMatchObject({ index: 0, query: "q1", retrievedIds: ["c1", "c2"], error: null });
    expect(report.perQuery[2].error).toBe("boom");
  });

  it("returns null for non-reports and survives sparse ones", () => {
    expect(normalizeEvalReport(null)).toBeNull();
    expect(normalizeEvalReport([])).toBeNull();
    expect(normalizeEvalReport({})).toEqual({ total: 0, evaluated: 0, failed: 0, metrics: {}, perQuery: [] });
  });

  it("flags top-1 hits", () => {
    const report = normalizeEvalReport(REPORT)!;
    expect(report.perQuery.map(isTopHit)).toEqual([true, false, false]);
  });

  it("formats metrics", () => {
    expect(formatMetric(0.123456)).toBe("0.1235");
    expect(formatMetric(undefined)).toBe("-");
    expect(formatMetric(Number.NaN)).toBe("-");
  });
});

describe("evaluation history", () => {
  const succeeded = normalizeTask({
    task_id: "e1",
    kind: "evaluation",
    status: "succeeded",
    version: 3,
    created_at: "2026-01-01T00:00:00Z",
    detail: {
      params: { backend: "custom", test_set_path: "g.json", top_k: 4, filters: { collection: "kb" } },
      result: REPORT,
    },
  });
  const failed = normalizeTask({
    task_id: "e2",
    kind: "evaluation",
    status: "failed",
    version: 2,
    detail: { params: { backend: "ragas", test_set_path: "g.json", top_k: null, filters: null }, error: { type: "ValueError", message: "no golden set" } },
  });

  it("projects tasks into run rows", () => {
    const ok = toEvaluationRun(succeeded);
    expect(ok).toMatchObject({ taskId: "e1", backend: "custom", topK: 4, collection: "kb", error: null });
    expect(ok.report?.metrics.mrr).toBe(0.25);
    const bad = toEvaluationRun(failed);
    expect(bad).toMatchObject({ status: "failed", topK: null, collection: null, report: null, error: "no golden set" });
  });

  it("collects the union of metric keys in first-seen order", () => {
    const reports = [toEvaluationRun(failed).report, toEvaluationRun(succeeded).report, normalizeEvalReport({ metrics: { faithfulness: 0.9, mrr: 1 } })];
    expect(collectMetricKeys(reports)).toEqual(["hit_rate", "mrr", "faithfulness"]);
  });

  describe("trendSeries", () => {
    const runWith = (taskId: string, createdAt: string, status: "succeeded" | "failed", metrics: Record<string, unknown>) =>
      toEvaluationRun(
        normalizeTask({
          task_id: taskId,
          kind: "evaluation",
          status,
          version: 0,
          created_at: createdAt,
          detail: { params: { backend: "custom", test_set_path: "g.json" }, result: { metrics } },
        }),
      );

    it("plots the last 10 succeeded runs time-ascending, first 3 metrics", () => {
      const runs = [
        runWith("e3", "2026-01-03T00:00:00Z", "succeeded", { hit_rate: 0.6, mrr: 0.3, extra: 1 }),
        runWith("e1", "2026-01-01T00:00:00Z", "succeeded", { hit_rate: 0.4, mrr: 0.2 }),
        runWith("e2", "2026-01-02T00:00:00Z", "failed", { hit_rate: 9 }),
        runWith("e4", "2026-01-04T00:00:00Z", "succeeded", { hit_rate: 0.8, mrr: 0.5, extra: 2 }),
      ];
      const trend = trendSeries(runs)!;
      expect(trend).not.toBeNull();
      expect(trend.runs).toBe(3); // the failed run is excluded
      expect(trend.series.map((s) => s.key)).toEqual(["hit_rate", "mrr", "extra"]);
      expect(trend.series[0].points).toEqual([
        { run: 1, value: 0.4 },
        { run: 2, value: 0.6 },
        { run: 3, value: 0.8 },
      ]);
    });

    it("caps the window at 10 runs and nulls missing metrics", () => {
      const runs = Array.from({ length: 12 }, (_, i) =>
        runWith(`e${i}`, `2026-01-01T00:00:${String(i).padStart(2, "0")}Z`, "succeeded", { hit_rate: i / 12 }),
      );
      const trend = trendSeries(runs)!;
      expect(trend.runs).toBe(10);
      expect(trend.series[0].points[0]).toEqual({ run: 1, value: 2 / 12 }); // runs 3..12 remain
    });

    it("returns null with fewer than two succeeded runs", () => {
      const one = [runWith("e1", "2026-01-01T00:00:00Z", "succeeded", { hit_rate: 1 })];
      expect(trendSeries([])).toBeNull();
      expect(trendSeries(one)).toBeNull();
      expect(trendSeries([...one, runWith("e2", "2026-01-02T00:00:00Z", "failed", { hit_rate: 1 })])).toBeNull();
    });
  });
});
