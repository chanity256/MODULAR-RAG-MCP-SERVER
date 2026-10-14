"use client";

import { useState } from "react";
import { PageHeader, Section } from "@/components/dashboard/Section";
import EvalHistory from "@/components/evaluation/EvalHistory";
import EvalReportViewer from "@/components/evaluation/EvalReportView";
import EvaluationForm from "@/components/evaluation/EvaluationForm";
import TaskStatusView from "@/components/tasks/TaskStatusView";
import { useTaskFollow } from "@/hooks/useTaskFollow";
import { normalizeEvalReport } from "@/lib/evaluation";
import { isTerminalStatus } from "@/lib/tasks";

export default function EvaluationPage() {
  const [refreshSignal, setRefreshSignal] = useState(0);
  const follower = useTaskFollow(() => setRefreshSignal((n) => n + 1));

  const task = follower.task;
  const showStatus = task !== null || follower.following || follower.error !== null;
  const report = task && isTerminalStatus(task.status) ? normalizeEvalReport(task.detail.result) : null;
  const params = task?.detail.params ?? {};

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <PageHeader
        title="Evaluation"
        description="Run a golden test set against retrieval, then compare metrics across runs."
      />

      <Section title="Run an evaluation">
        <EvaluationForm
          onSubmitted={(taskId, submitted) => {
            follower.follow(taskId, submitted);
            setRefreshSignal((n) => n + 1);
          }}
          onFollowExisting={(taskId) => follower.follow(taskId)}
        />
      </Section>

      {showStatus && (
        <Section title="Progress">
          <TaskStatusView
            title="Evaluation"
            task={task}
            transport={follower.transport}
            observeError={follower.error}
            onRetryObserve={follower.retry}
          />
          {task && typeof params.test_set_path === "string" && (
            <p className="break-all text-xs text-slate-500 dark:text-slate-400">
              backend={String(params.backend ?? "-")} · test set={params.test_set_path}
            </p>
          )}
        </Section>
      )}

      {report && <EvalReportViewer key={task?.task_id} report={report} />}

      <Section title="History">
        <EvalHistory
          selectedId={task?.task_id ?? null}
          onSelect={(t) => follower.follow(t.task_id, t)}
          refreshSignal={refreshSignal}
          autoRefresh={follower.following}
        />
      </Section>
    </div>
  );
}
