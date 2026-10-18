"use client";

import { useState } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import DataTable, { type Column } from "@/components/dashboard/DataTable";
import MetricCard from "@/components/dashboard/MetricCard";
import { PageHeader, RefreshControls, Section, SourceBadge } from "@/components/dashboard/Section";
import {
  AgenticRoutesView,
  ChatStagesView,
  HitsTable,
  RerankTable,
  StageList,
  StageWaterfall,
} from "@/components/dashboard/StageViews";
import TraceDetailLoader from "@/components/dashboard/TraceDetailLoader";
import Pagination from "@/components/documents/Pagination";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { clampOffset } from "@/lib/data";
import { fetchTraces } from "@/lib/queries";
import {
  ALL_SOURCES,
  DEFAULT_SOURCE,
  QUERY_STAGES,
  agenticRoutes,
  buildWaterfall,
  chatSummary,
  findStage,
  formatMs,
  formatTime,
  formatValue,
  KNOWN_SOURCES,
  rerankComparison,
  routesParam,
  sourceMetadataNotes,
  sourceOptions,
  stageHits,
  traceSource,
} from "@/lib/traces";
import type { TraceDetail, TraceSummary } from "@/lib/types";

const PAGE_SIZE = 50;

const COLUMNS: Column<TraceSummary>[] = [
  {
    key: "query",
    header: "Query",
    render: (r) => (
      <span title={r.query ?? undefined} className="block max-w-[26rem] truncate">
        {r.query || r.trace_id}
      </span>
    ),
  },
  { key: "source", header: "Source", render: (r) => <SourceBadge source={traceSource(r)} /> },
  { key: "collection", header: "Collection", render: (r) => r.collection ?? "-" },
  { key: "stages", header: "Stages", render: (r) => r.stage_count, align: "right" },
  { key: "total", header: "Total", render: (r) => formatMs(r.total_elapsed_ms), align: "right" },
  { key: "started", header: "Started", render: (r) => formatTime(r.started_at) },
];

function AgenticToolSection({ trace }: { trace: TraceDetail }) {
  const stage = findStage(trace.stages, "agentic_search");
  if (!stage) return null;
  const details = stage.details;
  if (details.error) {
    return (
      <Section title="Agentic search">
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
        >
          Agentic search failed: {formatValue(details.error_type)}: {formatValue(details.error)}
        </div>
      </Section>
    );
  }
  const files = Array.isArray(details.evidence_files) ? details.evidence_files.map(String) : [];
  return (
    <Section title="Agentic search">
      <div className="grid gap-3 sm:grid-cols-4">
        <MetricCard label="Mode" value={formatValue(details.mode ?? "-")} />
        <MetricCard label="LLM tokens" value={formatValue(details.total_llm_tokens ?? 0)} />
        <MetricCard label="LLM calls" value={formatValue(details.llm_call_count ?? 0)} />
        <MetricCard label="Evidence files" value={files.length} />
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400">
        backend={formatValue(details.backend)} · evidences={formatValue(details.evidences)} · elapsed_ms=
        {formatValue(details.elapsed_ms)}
      </p>
      {files.length > 0 && (
        <DataTable
          columns={[
            { key: "rank", header: "Rank", render: (r) => r.rank, align: "right" },
            { key: "file", header: "Evidence file", render: (r) => <span className="font-mono text-xs">{r.file}</span> },
          ]}
          rows={files.map((file, index) => ({ rank: index + 1, file }))}
          rowKey={(r) => `${r.rank}`}
        />
      )}
    </Section>
  );
}

function QueryDetail({ trace }: { trace: TraceDetail }) {
  const rows = buildWaterfall(trace.timings, trace.stages, QUERY_STAGES);
  const dense = stageHits(findStage(trace.stages, "dense_retrieval"));
  const sparse = stageHits(findStage(trace.stages, "sparse_retrieval"));
  const rerank = findStage(trace.stages, "rerank");
  const rerankDetails = rerank?.details ?? {};
  const rerankRows = rerankComparison(rerankDetails);
  const routes = agenticRoutes(trace.stages);
  const chat = chatSummary(trace.stages);
  const source = traceSource(trace);
  const sourceNotes = source === DEFAULT_SOURCE ? [] : sourceMetadataNotes(trace.metadata);
  const fusionHits = (() => {
    const fusion = stageHits(findStage(trace.stages, "fusion"));
    return fusion.length > 0 ? fusion : stageHits(findStage(trace.stages, "hybrid"));
  })();

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <div className="flex items-center gap-2 text-sm text-slate-900 dark:text-slate-100">
          <span className="font-semibold">Query</span>
          <span>{trace.query || "(unknown)"}</span>
          <SourceBadge source={traceSource(trace)} />
        </div>
        <p className="break-all text-xs text-slate-500 dark:text-slate-400">
          trace_id={trace.trace_id} · {formatMs(trace.total_elapsed_ms)} · {formatTime(trace.started_at)}
          {sourceNotes.length > 0 && ` · source=${source} · ${sourceNotes.join(" · ")}`}
        </p>
      </div>

      <Section title="Stage timing">
        <StageWaterfall rows={rows} />
      </Section>

      {(dense.length > 0 || sparse.length > 0) && (
        <div className="grid gap-4 md:grid-cols-2">
          <Section title="Dense">
            <HitsTable hits={dense} emptyMessage="No hits." />
          </Section>
          <Section title="Sparse">
            <HitsTable hits={sparse} emptyMessage="No hits." />
          </Section>
        </div>
      )}

      {Object.keys(rerankDetails).length > 0 && (
        <Section title="Rerank before / after">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            backend={formatValue(rerankDetails.backend)} · fallback={formatValue(rerankDetails.fallback)}
          </p>
          <RerankTable rows={rerankRows} />
        </Section>
      )}

      {chat && (
        <Section title="Chat stages">
          <ChatStagesView summary={chat} />
        </Section>
      )}

      <AgenticToolSection trace={trace} />

      {routes && (
        <Section title="Agentic routing">
          <AgenticRoutesView routes={routes} />
        </Section>
      )}

      {fusionHits.length > 0 && (
        <Section title="Final / fused results">
          <HitsTable hits={fusionHits} />
        </Section>
      )}

      <Section title="Stage details">
        <StageList stages={trace.stages} />
      </Section>
    </div>
  );
}

export default function QueryTracesPage() {
  const [keyword, setKeyword] = useState("");
  const [source, setSource] = useState(ALL_SOURCES);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const debounced = useDebouncedValue(keyword);

  const { data, error, loading, reload } = useAsyncData(
    (signal) =>
      fetchTraces(
        { traceType: "query", keyword: debounced, routes: routesParam(source), limit: PAGE_SIZE, offset },
        signal,
      ),
    `query:${debounced}:${source}:${offset}`,
    autoRefresh ? 10_000 : 0,
  );

  const items = data?.items ?? [];
  // Newest-first list: after auto-refresh shrinks `total` (e.g. trace rotation), pull offset back
  // in range. Adjusting state during render (guarded by the last seen value) re-renders before
  // committing, which refetches the clamped page via the cache key.
  const clamped = data ? clampOffset(data.total, PAGE_SIZE, offset) : offset;
  const [lastClamped, setLastClamped] = useState(clamped);
  if (lastClamped !== clamped) {
    setLastClamped(clamped);
    setOffset(clamped);
  }

  const sources = [...new Set([...KNOWN_SOURCES, ...sourceOptions(items), source])].filter(
    (name) => name !== ALL_SOURCES,
  );
  const activeSource = source;
  const selected = items.find((i) => i.trace_id === selectedId) ?? items[0];

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <PageHeader
        title="Query Traces"
        description="Query history, stage timing, dense / sparse comparison and rerank changes."
        actions={<RefreshControls autoRefresh={autoRefresh} onToggle={setAutoRefresh} onRefresh={reload} />}
      />

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="search"
          value={keyword}
          onChange={(e) => {
            setKeyword(e.target.value);
            setOffset(0);
          }}
          placeholder="Search by query text or trace id"
          aria-label="Search query traces"
          className="w-full max-w-md rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
        />
        <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
          Source
          <select
            value={activeSource}
            onChange={(e) => {
              setSource(e.target.value);
              setOffset(0);
            }}
            aria-label="Filter by source"
            className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
          >
            <option value={ALL_SOURCES}>All</option>
            {sources.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <ErrorNotice error={error} onRetry={reload} />}
      {!error && loading && <LoadingState />}

      {data && (
        <>
          <DataTable
            columns={COLUMNS}
            rows={items}
            rowKey={(r) => r.trace_id}
            selectedKey={selected?.trace_id}
            onRowClick={(r) => setSelectedId(r.trace_id)}
            emptyMessage={
              debounced || activeSource !== ALL_SOURCES
                ? "No query traces match the current filters."
                : "No query traces yet. Run a query and come back."
            }
          />
          <Pagination total={data.total} limit={PAGE_SIZE} offset={offset} onOffsetChange={setOffset} />
          {selected && (
            <TraceDetailLoader traceId={selected.trace_id}>
              {(trace) => <QueryDetail trace={trace} />}
            </TraceDetailLoader>
          )}
        </>
      )}
    </div>
  );
}
