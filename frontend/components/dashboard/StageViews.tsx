import { Badge } from "./Section";
import DataTable, { EmptyState, type Column } from "./DataTable";
import {
  formatMs,
  formatPercent,
  formatValue,
  scalarDetails,
  stageElapsedMs,
  stageProvider,
  type AgenticRoutes,
  type Hit,
  type RerankRow,
  type WaterfallRow,
} from "@/lib/traces";
import type { StageRecord } from "@/lib/types";

/** Horizontal bars of per-stage time; no charting dependency. */
export function StageWaterfall({ rows }: { rows: readonly WaterfallRow[] }) {
  if (rows.length === 0) return <EmptyState message="No stage timing recorded." />;
  return (
    <ul
      aria-label="Stage timing"
      className="space-y-2 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800"
    >
      {rows.map((row) => (
        <li key={row.stage} className="grid grid-cols-[10rem_1fr_9rem] items-center gap-3 text-sm">
          <span className="truncate font-mono text-xs text-slate-700 dark:text-slate-200" title={row.stage}>
            {row.stage}
          </span>
          <div className="h-3 rounded bg-slate-100 dark:bg-slate-700">
            <div
              className="h-3 rounded bg-blue-500"
              style={{ width: `${Math.max(row.width, 1)}%` }}
              role="presentation"
            />
          </div>
          <span className="text-right text-xs tabular-nums text-slate-600 dark:text-slate-300">
            {formatMs(row.elapsed_ms)} · {formatPercent(row.percent)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Generic expandable stage list. Works for any stage: `method` / `provider` become badges,
 * primitive details a one-line summary, the full `details` JSON sits behind the toggle.
 */
export function StageList({ stages }: { stages: readonly StageRecord[] }) {
  if (stages.length === 0) return <EmptyState message="No stages recorded." />;
  return (
    <div className="space-y-2">
      {stages.map((stage, index) => {
        const provider = stageProvider(stage);
        const summary = scalarDetails(stage.details);
        const hasDetails = Object.keys(stage.details).length > 0;
        return (
          <details
            key={`${stage.stage}-${index}`}
            className="rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800"
          >
            <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm">
              <span className="font-mono text-xs font-medium text-slate-800 dark:text-slate-100">
                {stage.stage || "(unnamed)"}
              </span>
              {stage.method && <Badge tone="blue">{stage.method}</Badge>}
              {provider && <Badge tone="green">{provider}</Badge>}
              <span className="text-xs tabular-nums text-slate-500 dark:text-slate-400">
                {formatMs(stageElapsedMs(stage))}
              </span>
              {summary.map(([key, value]) => (
                <span key={key} className="text-xs text-slate-500 dark:text-slate-400">
                  {key}={value}
                </span>
              ))}
            </summary>
            <pre className="max-h-96 overflow-auto border-t border-slate-100 p-3 text-xs text-slate-700 dark:border-slate-700 dark:text-slate-200">
              {hasDetails ? JSON.stringify(stage.details, null, 2) : JSON.stringify(stage, null, 2)}
            </pre>
          </details>
        );
      })}
    </div>
  );
}

const HIT_COLUMNS: Column<Hit & { rank: number }>[] = [
  { key: "rank", header: "Rank", render: (r) => r.rank, align: "right" },
  { key: "chunk", header: "Chunk", render: (r) => <span className="font-mono text-xs">{r.chunk_id}</span> },
  { key: "score", header: "Score", render: (r) => formatValue(r.score), align: "right" },
];

export function HitsTable({ hits, emptyMessage = "No hits." }: { hits: readonly Hit[]; emptyMessage?: string }) {
  return (
    <DataTable
      columns={HIT_COLUMNS}
      rows={hits.map((hit, index) => ({ ...hit, rank: index + 1 }))}
      rowKey={(row) => `${row.rank}-${row.chunk_id}`}
      emptyMessage={emptyMessage}
    />
  );
}

const MARKERS = { up: "▲", down: "▼", same: "•", new: "new" } as const;

const RERANK_COLUMNS: Column<RerankRow>[] = [
  { key: "after", header: "After", render: (r) => r.after, align: "right" },
  { key: "before", header: "Before", render: (r) => r.before ?? "-", align: "right" },
  {
    key: "change",
    header: "Change",
    render: (r) => `${MARKERS[r.marker]}${r.delta ? Math.abs(r.delta) : ""}`,
    align: "right",
  },
  { key: "chunk", header: "Chunk", render: (r) => <span className="font-mono text-xs">{r.chunk_id}</span> },
  { key: "score", header: "Score", render: (r) => formatValue(r.score), align: "right" },
];

export function RerankTable({ rows }: { rows: readonly RerankRow[] }) {
  return (
    <DataTable
      columns={RERANK_COLUMNS}
      rows={rows}
      rowKey={(row) => `${row.after}-${row.chunk_id}`}
      emptyMessage="No rerank results."
    />
  );
}

export function AgenticRoutesView({ routes }: { routes: AgenticRoutes }) {
  const triggerEntries = routes.trigger
    ? (["strategy", "triggered", "reason"] as const).map((key) => `${key}=${formatValue(routes.trigger?.[key])}`)
    : [];
  return (
    <div className="space-y-2">
      {routes.rows.length > 0 && (
        <DataTable
          columns={[
            { key: "stage", header: "Stage", render: (r) => r.stage },
            { key: "dense", header: "Dense", render: (r) => formatValue(r.dense), align: "right" },
            { key: "sparse", header: "Sparse", render: (r) => formatValue(r.sparse), align: "right" },
            { key: "agentic", header: "Agentic", render: (r) => formatValue(r.agentic), align: "right" },
          ]}
          rows={routes.rows}
          rowKey={(r) => r.stage}
        />
      )}
      {triggerEntries.length > 0 && (
        <p className="text-xs text-slate-500 dark:text-slate-400">Trigger: {triggerEntries.join(" · ")}</p>
      )}
    </div>
  );
}
