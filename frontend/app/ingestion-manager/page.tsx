"use client";

import { useState } from "react";
import { PageHeader, Section } from "@/components/dashboard/Section";
import IngestionForm from "@/components/ingestion/IngestionForm";
import IngestionResultView from "@/components/ingestion/IngestionResultView";
import ManagedDocuments from "@/components/ingestion/ManagedDocuments";
import RecentTasks from "@/components/tasks/RecentTasks";
import TaskStatusView from "@/components/tasks/TaskStatusView";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useTaskFollow } from "@/hooks/useTaskFollow";
import { fetchCollections } from "@/lib/data";
import { isTerminalStatus, type TaskRecord } from "@/lib/tasks";

function describeIngestion(task: TaskRecord): string {
  const files = Array.isArray(task.detail.params.files) ? task.detail.params.files.map(String) : [];
  const collection = typeof task.detail.params.collection === "string" ? task.detail.params.collection : "-";
  const head = files.slice(0, 2).join(", ");
  const more = files.length > 2 ? ` +${files.length - 2} more` : "";
  return `${head || "-"}${more} → ${collection}`;
}

export default function IngestionManagerPage() {
  const [refreshSignal, setRefreshSignal] = useState(0);
  const collections = useAsyncData((signal) => fetchCollections(signal), "collections");
  const follower = useTaskFollow(() => {
    setRefreshSignal((n) => n + 1);
    collections.reload();
  });

  const followById = (taskId: string, initial?: TaskRecord) => follower.follow(taskId, initial);

  const task = follower.task;
  const showStatus = task !== null || follower.following || follower.error !== null;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <PageHeader
        title="Ingestion Manager"
        description="Upload files, run ingestion with live progress, and delete ingested documents."
      />

      <Section title="New ingestion">
        <IngestionForm
          collections={(collections.data ?? []).map((c) => c.name)}
          onSubmitted={(taskId, submitted) => {
            followById(taskId, submitted);
            setRefreshSignal((n) => n + 1);
          }}
          onFollowExisting={(taskId) => followById(taskId)}
        />
      </Section>

      {showStatus && (
        <Section title="Progress">
          <TaskStatusView
            title="Ingestion"
            task={task}
            transport={follower.transport}
            observeError={follower.error}
            onRetryObserve={follower.retry}
          />
          {task && isTerminalStatus(task.status) && <IngestionResultView task={task} />}
        </Section>
      )}

      <Section title="Recent ingestions">
        <RecentTasks
          kind="ingestion"
          describeHeader="Files → collection"
          describe={describeIngestion}
          selectedId={task?.task_id ?? null}
          onSelect={(t) => followById(t.task_id, t)}
          refreshSignal={refreshSignal}
          autoRefresh={follower.following}
        />
      </Section>

      <Section title="Ingested documents">
        <ManagedDocuments refreshSignal={refreshSignal} onChanged={() => collections.reload()} />
      </Section>
    </div>
  );
}
