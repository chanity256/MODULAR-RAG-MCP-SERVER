"use client";

import { useState } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import DataTable, { type Column } from "@/components/dashboard/DataTable";
import MetricCard from "@/components/dashboard/MetricCard";
import { Badge, PageHeader, RefreshControls, Section } from "@/components/dashboard/Section";
import { StageList, StageWaterfall } from "@/components/dashboard/StageViews";
import TraceDetailLoader from "@/components/dashboard/TraceDetailLoader";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { fetchTraces } from "@/lib/queries";
import {
  INGEST_STAGES,
  buildWaterfall,
  formatMs,
  formatTime,
  ingestionStatus,
} from "@/lib/traces";
import type { TraceDetail, TraceSummary } from "@/lib/types";

const PAGE_SIZE = 500;
const VISIBLE_STEP = 50;

function fileName(path: string | null): string {
  if (!path) return "-";
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

const COLUMNS: Column<TraceSummary>[] = [
  {
    key: "source",
    header: "Source",
    render: (r) => (
      <span title={r.source_path ?? undefined} className="block max-w-[22rem] truncate">
        {r.source_path ? fileName(r.source_path) : r.trace_id}
      </span>
    ),
  },
  { key: "collection", header: "Collection", render: (r) => r.collection ?? "-" },
  {
    key: "status",
    header: "Status",
    render: (r) => {
      const status = ingestionStatus(r);
      return <Badge tone={status === "skipped" ? "amber" : "green"}>{status}</Badge>;
    },
  },
  { key: "chunks", header: "Chunks", render: (r) => r.chunks ?? "-", align: "right" },
  { key: "images", header: "Images", render: (r) => r.images ?? "-", align: "right" },
  { key: "total", header: "Total", render: (r) => formatMs(r.total_elapsed_ms), align: "right" },
  { key: "started", header: "Started", render: (r) => formatTime(r.started_at) },
];

function IngestionDetail({ trace }: { trace: TraceDetail }) {
  const status = ingestionStatus(trace);
  const rows = buildWaterfall(trace.timings, trace.stages, INGEST_STAGES);
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-4">
        <MetricCard label="Status" value={status} />
        <MetricCard label="Chunks" value={trace.chunks ?? "-"} />
        <MetricCard label="Images" value={trace.images ?? "-"} />
        <MetricCard label="Total time" value={formatMs(trace.total_elapsed_ms)} />
      </div>
      <p className="break-all text-xs text-slate-500 dark:text-slate-400">
        collection={trace.collection ?? "-"} · source={trace.source_path ?? "-"} · trace_id={trace.trace_id}
      </p>
      <Section title="Stage timing">
        <StageWaterfall rows={rows} />
      </Section>
      <Section title="Stage details">
        <StageList stages={trace.stages} />
      </Section>
    </div>
  );
}

export default function IngestionTracesPage() {
  const [keyword, setKeyword] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [visible, setVisible] = useState(VISIBLE_STEP);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const debounced = useDebouncedValue(keyword);

  const { data, error, loading, reload } = useAsyncData(
    (signal) => fetchTraces({ traceType: "ingestion", keyword: debounced, limit: PAGE_SIZE }, signal),
    `ingestion:${debounced}`,
    autoRefresh ? 10_000 : 0,
  );

  const items = data?.items ?? [];
  const shown = items.slice(0, visible);
  const selected = items.find((i) => i.trace_id === selectedId) ?? items[0];

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <PageHeader
        title="Ingestion Traces"
        description="Ingestion history with per-stage timing."
        actions={<RefreshControls autoRefresh={autoRefresh} onToggle={setAutoRefresh} onRefresh={reload} />}
      />

      <input
        type="search"
        value={keyword}
        onChange={(e) => {
          setKeyword(e.target.value);
          setVisible(VISIBLE_STEP);
        }}
        placeholder="Search by trace id or source path"
        aria-label="Search ingestion traces"
        className="w-full max-w-md rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
      />

      {error && <ErrorNotice error={error} onRetry={reload} />}
      {!error && loading && <LoadingState />}

      {data && (
        <>
          <DataTable
            columns={COLUMNS}
            rows={shown}
            rowKey={(r) => r.trace_id}
            selectedKey={selected?.trace_id}
            onRowClick={(r) => setSelectedId(r.trace_id)}
            emptyMessage={
              debounced
                ? "No ingestion traces match the search."
                : "No ingestion traces yet. Run an ingestion and come back."
            }
          />
          <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
            <span>
              Showing {shown.length} of {data.total}
            </span>
            {shown.length < items.length && (
              <button
                type="button"
                onClick={() => setVisible((v) => v + VISIBLE_STEP)}
                className="rounded border border-slate-300 px-2.5 py-1 hover:bg-slate-100 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                Show more
              </button>
            )}
          </div>
          {selected && (
            <TraceDetailLoader traceId={selected.trace_id}>
              {(trace) => <IngestionDetail trace={trace} />}
            </TraceDetailLoader>
          )}
        </>
      )}
    </div>
  );
}
