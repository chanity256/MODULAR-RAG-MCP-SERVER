"use client";

import { useEffect, useMemo, useRef } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import DataTable, { type Column } from "@/components/dashboard/DataTable";
import { BUTTON_SECONDARY } from "@/components/shared/styles";
import { TaskStatusBadge } from "@/components/tasks/TaskStatusView";
import { useAsyncData } from "@/hooks/useAsyncData";
import { collectMetricKeys, formatMetric, toEvaluationRun, trendSeries, type EvaluationTrend, type EvaluationRun } from "@/lib/evaluation";
import { fetchTasks, isActiveStatus, type TaskRecord } from "@/lib/tasks";
import { formatTime } from "@/lib/traces";

const REFRESH_MS = 3000;

const TREND_COLORS = ["#2563eb", "#16a34a", "#dc2626"];
const TREND_WIDTH = 600;
const TREND_HEIGHT = 160;
const TREND_PAD = 24;

/** Inline-SVG line chart of recent runs (no chart library); one polyline per metric. */
function TrendChart({ trend }: { trend: EvaluationTrend }) {
  const maxValue = Math.max(1, ...trend.series.flatMap((s) => s.points.map((p) => p.value ?? 0)));
  const x = (run: number) => TREND_PAD + ((run - 1) / (trend.runs - 1)) * (TREND_WIDTH - 2 * TREND_PAD);
  const y = (value: number) => TREND_HEIGHT - TREND_PAD - (value / maxValue) * (TREND_HEIGHT - 2 * TREND_PAD);

  return (
    <figure className="space-y-1">
      <svg
        role="img"
        aria-label="Metric trend across recent runs"
        viewBox={`0 0 ${TREND_WIDTH} ${TREND_HEIGHT}`}
        className="w-full rounded border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900"
        data-testid="trend-chart"
      >
        {trend.series.map((series, si) => {
          const color = TREND_COLORS[si % TREND_COLORS.length];
          const line = series.points
            .map((p) => (p.value === null ? null : `${x(p.run)},${y(p.value)}`))
            .filter(Boolean)
            .join(" ");
          return (
            <g key={series.key}>
              <polyline fill="none" stroke={color} strokeWidth="2" points={line} />
              {series.points.map(
                (p, pi) =>
                  p.value !== null && (
                    <circle key={pi} cx={x(p.run)} cy={y(p.value)} r="3" fill={color} data-run={p.run}>
                      <title>{`run ${p.run} · ${series.key}: ${formatMetric(p.value)}`}</title>
                    </circle>
                  ),
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="flex gap-4 text-xs text-slate-500 dark:text-slate-400">
        {trend.series.map((series, si) => (
          <span key={series.key} className="inline-flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: TREND_COLORS[si % TREND_COLORS.length] }}
            />
            {series.key}
          </span>
        ))}
        <span className="ml-auto">last {trend.runs} runs (oldest → newest)</span>
      </figcaption>
    </figure>
  );
}

/** Past evaluation runs (tasks of kind `evaluation`) with their aggregate metrics side by side. */
export default function EvalHistory({
  selectedId,
  onSelect,
  refreshSignal,
  autoRefresh,
}: {
  selectedId: string | null;
  onSelect: (task: TaskRecord) => void;
  refreshSignal: number;
  autoRefresh: boolean;
}) {
  const { data, error, loading, reload } = useAsyncData(
    (signal) => fetchTasks({ kind: "evaluation", limit: 20 }, signal),
    "eval-history",
    autoRefresh ? REFRESH_MS : 0,
  );

  const firstSignal = useRef(refreshSignal);
  useEffect(() => {
    if (refreshSignal !== firstSignal.current) reload();
  }, [refreshSignal, reload]);

  const runs = useMemo(() => (data?.items ?? []).map(toEvaluationRun), [data]);
  const metricKeys = useMemo(() => collectMetricKeys(runs.map((r) => r.report)), [runs]);
  const trend = useMemo(() => trendSeries(runs), [runs]);

  const columns: Column<EvaluationRun>[] = [
    { key: "status", header: "Status", render: (r) => <TaskStatusBadge status={r.status} /> },
    { key: "created", header: "Started", render: (r) => formatTime(r.createdAt) },
    { key: "backend", header: "Backend", render: (r) => r.backend },
    {
      key: "scope",
      header: "Top-K / collection",
      render: (r) => `${r.topK ?? "default"} / ${r.collection ?? "all"}`,
    },
    {
      key: "counts",
      header: "Evaluated / failed",
      render: (r) => (r.report ? `${r.report.evaluated} / ${r.report.failed}` : "-"),
      align: "right",
    },
    ...metricKeys.map(
      (key): Column<EvaluationRun> => ({
        key: `metric:${key}`,
        header: key,
        render: (r) => formatMetric(r.report?.metrics[key]),
        align: "right",
      }),
    ),
    {
      key: "view",
      header: "",
      render: (r) => (
        <button type="button" className={BUTTON_SECONDARY} onClick={() => onSelect(r.task)}>
          {isActiveStatus(r.status) ? "Follow" : "View"}
        </button>
      ),
    },
  ];

  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <button type="button" className={BUTTON_SECONDARY} onClick={reload}>
          Refresh
        </button>
      </div>
      {error && <ErrorNotice error={error} onRetry={reload} />}
      {!error && (loading || !data) && <LoadingState label="Loading history..." />}
      {data && !error && (
        <>
          <DataTable
            columns={columns}
            rows={runs}
            rowKey={(r) => r.taskId}
            selectedKey={selectedId}
            onRowClick={(r) => onSelect(r.task)}
            emptyMessage="No evaluation runs yet. Choose a backend and run an evaluation."
          />
          {trend ? (
            <TrendChart trend={trend} />
          ) : (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              The trend chart appears once at least two succeeded runs are recorded.
            </p>
          )}
        </>
      )}
    </div>
  );
}
