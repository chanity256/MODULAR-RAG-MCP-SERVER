import DataTable from "@/components/dashboard/DataTable";
import MetricCard from "@/components/dashboard/MetricCard";
import { normalizeIngestionResult } from "@/lib/data";
import type { TaskRecord } from "@/lib/tasks";

/** Summary of a finished ingestion task, including per-file errors. */
export default function IngestionResultView({ task }: { task: TaskRecord }) {
  const result = normalizeIngestionResult(task.detail.result);
  if (!result) return null;
  const collection = typeof task.detail.params.collection === "string" ? task.detail.params.collection : null;
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-4">
        <MetricCard label="Files" value={result.files} hint={collection ? `collection: ${collection}` : undefined} />
        <MetricCard label="Chunks" value={result.chunks} />
        <MetricCard label="Images" value={result.images} />
        <MetricCard label="Failed" value={result.failed} />
      </div>
      {result.errors.length > 0 && (
        <div className="space-y-1">
          <div className="text-sm font-medium text-red-700 dark:text-red-300">
            Errors ({result.errors.length})
          </div>
          <DataTable
            columns={[
              { key: "path", header: "File", render: (r) => <span className="font-mono text-xs">{r.path}</span> },
              { key: "message", header: "Error", render: (r) => r.message },
            ]}
            rows={result.errors}
            rowKey={(r) => `${r.path}:${r.message}`}
          />
        </div>
      )}
    </div>
  );
}
