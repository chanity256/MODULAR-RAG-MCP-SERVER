import { ErrorNotice } from "@/components/dashboard/AsyncState";
import { Badge } from "@/components/dashboard/Section";
import { CARD_CLASS } from "@/components/shared/styles";
import type { ApiError } from "@/lib/errors";
import { progressPercent, type TaskRecord, type TaskStatus, type TaskTransport } from "@/lib/tasks";

const STATUS_TONE: Record<TaskStatus, "slate" | "blue" | "green" | "amber"> = {
  pending: "slate",
  running: "blue",
  succeeded: "green",
  failed: "amber",
};

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{status}</Badge>;
}

export function ProgressBar({ percent, label }: { percent: number; label: string }) {
  return (
    <div>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label={label}
        className="h-2 overflow-hidden rounded bg-slate-200 dark:bg-slate-700"
      >
        <div className="h-full bg-blue-600 transition-all" style={{ width: `${percent}%` }} />
      </div>
      <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{label}</div>
    </div>
  );
}

/** Live status of the followed task: badge, progress bar, transport and failure message. */
export default function TaskStatusView({
  task,
  transport,
  observeError,
  onRetryObserve,
  title,
}: {
  task: TaskRecord | null;
  transport: TaskTransport | null;
  observeError: ApiError | null;
  onRetryObserve: () => void;
  title: string;
}) {
  if (!task && !observeError) {
    return (
      <div role="status" className={CARD_CLASS}>
        Waiting for task status...
      </div>
    );
  }
  const progress = task?.detail.progress ?? null;
  const percent = task?.status === "succeeded" ? 100 : progressPercent(progress);
  const label = progress
    ? `${progress.stage} ${progress.current}/${progress.total}`
    : task?.status === "pending"
      ? "Queued..."
      : "Starting...";

  return (
    <div className={`${CARD_CLASS} space-y-3`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</span>
        {task && <TaskStatusBadge status={task.status} />}
        {task && (
          <span className="font-mono text-[11px] text-slate-500 dark:text-slate-400" title="task id">
            {task.task_id}
          </span>
        )}
        {transport && (
          <span className="ml-auto text-[11px] text-slate-500 dark:text-slate-400">
            {transport === "websocket" ? "live (WebSocket)" : "polling"}
          </span>
        )}
      </div>
      {task && (task.status === "pending" || task.status === "running" || task.status === "succeeded") && (
        <ProgressBar percent={percent} label={task.status === "succeeded" ? "Completed" : label} />
      )}
      {task?.status === "failed" && task.detail.error && (
        <div role="alert" className="text-sm text-red-700 dark:text-red-300">
          {task.detail.error.message}
          <span className="ml-1 text-xs opacity-70">({task.detail.error.type})</span>
        </div>
      )}
      {observeError && <ErrorNotice error={observeError} onRetry={onRetryObserve} />}
    </div>
  );
}
