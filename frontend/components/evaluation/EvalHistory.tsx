"use client";

import { useEffect, useMemo, useRef } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import DataTable, { type Column } from "@/components/dashboard/DataTable";
import { BUTTON_SECONDARY } from "@/components/shared/styles";
import { TaskStatusBadge } from "@/components/tasks/TaskStatusView";
import { useAsyncData } from "@/hooks/useAsyncData";
import { collectMetricKeys, formatMetric, toEvaluationRun, type EvaluationRun } from "@/lib/evaluation";
import { fetchTasks, isActiveStatus, type TaskRecord } from "@/lib/tasks";
import { formatTime } from "@/lib/traces";

const REFRESH_MS = 3000;

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
        <DataTable
          columns={columns}
          rows={runs}
          rowKey={(r) => r.taskId}
          selectedKey={selectedId}
          onRowClick={(r) => onSelect(r.task)}
          emptyMessage="No evaluation runs yet. Choose a backend and run an evaluation."
        />
      )}
    </div>
  );
}
