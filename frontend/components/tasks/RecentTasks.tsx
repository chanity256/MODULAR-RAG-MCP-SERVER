"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import DataTable, { type Column } from "@/components/dashboard/DataTable";
import { BUTTON_SECONDARY } from "@/components/shared/styles";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchTasks, isActiveStatus, type TaskRecord } from "@/lib/tasks";
import { formatTime } from "@/lib/traces";
import { TaskStatusBadge } from "./TaskStatusView";

const ACTIVE_REFRESH_MS = 3000;

/**
 * Newest-first task list for one `kind`. Refreshes periodically while `autoRefresh` is set
 * and whenever `refreshSignal` changes; clicking a row selects it for detail.
 */
export default function RecentTasks({
  kind,
  describe,
  describeHeader,
  selectedId,
  onSelect,
  refreshSignal = 0,
  autoRefresh = false,
  limit = 10,
}: {
  kind: string;
  describe: (task: TaskRecord) => ReactNode;
  describeHeader: string;
  selectedId: string | null;
  onSelect: (task: TaskRecord) => void;
  refreshSignal?: number;
  /** Poll the list while a task is being followed. */
  autoRefresh?: boolean;
  limit?: number;
}) {
  const { data, error, loading, reload } = useAsyncData(
    (signal) => fetchTasks({ kind, limit }, signal),
    `tasks:${kind}:${limit}`,
    autoRefresh ? ACTIVE_REFRESH_MS : 0,
  );

  const firstSignal = useRef(refreshSignal);
  useEffect(() => {
    if (refreshSignal !== firstSignal.current) reload();
  }, [refreshSignal, reload]);

  const columns: Column<TaskRecord>[] = [
    { key: "status", header: "Status", render: (t) => <TaskStatusBadge status={t.status} /> },
    { key: "describe", header: describeHeader, render: describe },
    { key: "created", header: "Created", render: (t) => formatTime(t.created_at) },
    {
      key: "view",
      header: "",
      render: (t) => (
        <button type="button" className={BUTTON_SECONDARY} onClick={() => onSelect(t)}>
          {isActiveStatus(t.status) ? "Follow" : "View"}
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
      {!error && (loading || !data) && <LoadingState label="Loading tasks..." />}
      {data && !error && (
        <DataTable
          columns={columns}
          rows={data.items}
          rowKey={(t) => t.task_id}
          selectedKey={selectedId}
          onRowClick={onSelect}
          emptyMessage="No tasks yet."
        />
      )}
    </div>
  );
}
