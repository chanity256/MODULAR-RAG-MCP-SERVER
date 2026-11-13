"use client";

import { useState } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import DataTable, { EmptyState, type Column } from "@/components/dashboard/DataTable";
import MetricCard from "@/components/dashboard/MetricCard";
import { PageHeader, RefreshControls, Section, SourceBadge } from "@/components/dashboard/Section";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchOverview } from "@/lib/queries";
import { formatMs, formatTime, formatValue, traceSource } from "@/lib/traces";
import type { CollectionStats, ComponentCard, TraceSummary } from "@/lib/types";

const COLLECTION_COLUMNS: Column<CollectionStats>[] = [
  { key: "name", header: "Collection", render: (r) => r.name },
  { key: "docs", header: "Documents", render: (r) => r.document_count, align: "right" },
  { key: "chunks", header: "Chunks", render: (r) => r.chunk_count, align: "right" },
  { key: "images", header: "Images", render: (r) => r.image_count, align: "right" },
];

function ComponentCardView({ card }: { card: ComponentCard }) {
  const extras = Object.entries(card.extras).filter(([, value]) => value !== null && value !== undefined);
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {card.name}
      </div>
      <div className="mt-1 text-sm font-semibold text-slate-900 dark:text-slate-100">{card.headline}</div>
      {extras.length > 0 && (
        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          {extras.map(([key, value]) => `${key}=${formatValue(value)}`).join(" · ")}
        </div>
      )}
    </div>
  );
}

function LatestTrace({ title, trace }: { title: string; trace: TraceSummary | null }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
        {title}
        {trace && trace.trace_type === "query" && <SourceBadge source={traceSource(trace)} />}
      </div>
      {trace ? (
        <div className="mt-2 space-y-1 text-xs text-slate-500 dark:text-slate-400">
          <div>
            trace_id: <span className="font-mono text-slate-700 dark:text-slate-200">{trace.trace_id}</span>
          </div>
          <div>
            started {formatTime(trace.started_at)} · {formatMs(trace.total_elapsed_ms)}
          </div>
        </div>
      ) : (
        <div className="mt-2 text-xs text-slate-500 dark:text-slate-400">No records yet.</div>
      )}
    </div>
  );
}

export default function OverviewPage() {
  const [autoRefresh, setAutoRefresh] = useState(false);
  const { data, error, loading, reload } = useAsyncData(
    (signal) => fetchOverview(signal),
    "overview",
    autoRefresh ? 10_000 : 0,
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <PageHeader
        title="Overview"
        description="Configured pluggable components and knowledge-base assets."
        actions={<RefreshControls autoRefresh={autoRefresh} onToggle={setAutoRefresh} onRefresh={reload} />}
      />

      {error && <ErrorNotice error={error} onRetry={reload} />}
      {!error && (loading || !data) && <LoadingState />}

      {data && (
        <>
          {data.warnings.length > 0 && (
            <div
              role="status"
              className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
            >
              {data.warnings.join("; ")}
            </div>
          )}

          <Section title="Components">
            {data.components.length === 0 ? (
              <EmptyState message="No component configuration available." />
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {data.components.map((card) => (
                  <ComponentCardView key={card.name} card={card} />
                ))}
              </div>
            )}
          </Section>

          <Section title="Data assets">
            {data.collections.length === 0 ? (
              <EmptyState message="No documents ingested yet. Run scripts/ingest.py or use the Ingestion Manager." />
            ) : (
              <>
                <div className="grid gap-3 sm:grid-cols-4">
                  <MetricCard label="Collections" value={data.totals.collections} />
                  <MetricCard label="Documents" value={data.totals.documents} />
                  <MetricCard label="Chunks" value={data.totals.chunks} />
                  <MetricCard label="Images" value={data.totals.images} />
                </div>
                <DataTable columns={COLLECTION_COLUMNS} rows={data.collections} rowKey={(r) => r.name} />
              </>
            )}
          </Section>

          <Section title="System health">
            <div className="grid gap-3 sm:grid-cols-2">
              <LatestTrace title="Latest ingestion" trace={data.latest_ingestion} />
              <LatestTrace title="Latest query" trace={data.latest_query} />
            </div>
          </Section>
        </>
      )}
    </div>
  );
}
