"use client";

import { useState } from "react";
import DataTable, { type Column } from "@/components/dashboard/DataTable";
import MetricCard from "@/components/dashboard/MetricCard";
import { Badge, Section } from "@/components/dashboard/Section";
import { formatMetric, isTopHit, type EvalReportView, type QueryEvalDetail } from "@/lib/evaluation";

function buildColumns(metricKeys: string[]): Column<QueryEvalDetail>[] {
  return [
    {
      key: "query",
      header: "Query",
      render: (r) => (
        <span title={r.query} className="block max-w-[22rem] truncate">
          {r.query}
        </span>
      ),
    },
    ...metricKeys.map(
      (key): Column<QueryEvalDetail> => ({
        key: `metric:${key}`,
        header: key,
        render: (r) => formatMetric(r.metrics[key]),
        align: "right",
      }),
    ),
    {
      key: "expected",
      header: "First expected",
      render: (r) => <span className="font-mono text-xs">{r.expectedChunkIds[0] ?? ""}</span>,
    },
    {
      key: "top1",
      header: "Retrieved top-1",
      render: (r) => (
        <span className="inline-flex items-center gap-1">
          <span className="font-mono text-xs">{r.retrievedIds[0] ?? ""}</span>
          {r.retrievedIds.length > 0 && <Badge tone={isTopHit(r) ? "green" : "slate"}>{isTopHit(r) ? "hit" : "miss"}</Badge>}
        </span>
      ),
    },
    {
      key: "error",
      header: "Error",
      render: (r) => (r.error ? <span className="text-red-700 dark:text-red-300">{r.error}</span> : ""),
    },
  ];
}

/** Aggregate metrics plus per-query details of one evaluation report. */
export default function EvalReportViewer({ report }: { report: EvalReportView }) {
  const [onlyProblems, setOnlyProblems] = useState(false);
  const metricEntries = Object.entries(report.metrics);
  const perQueryKeys = Array.from(new Set(report.perQuery.flatMap((q) => Object.keys(q.metrics))));
  const rows = onlyProblems ? report.perQuery.filter((q) => q.error || !isTopHit(q)) : report.perQuery;

  return (
    <div className="space-y-5">
      <Section title={`Result (${report.evaluated} evaluated / ${report.failed} failed of ${report.total})`}>
        {metricEntries.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">The report contains no aggregate metrics.</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {metricEntries.map(([key, value]) => (
              <MetricCard key={key} label={key} value={formatMetric(value)} />
            ))}
          </div>
        )}
      </Section>

      <Section title="Per-query details">
        {report.perQuery.length > 0 && (
          <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
            <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
            Only misses and errors
          </label>
        )}
        <DataTable
          columns={buildColumns(perQueryKeys)}
          rows={rows}
          rowKey={(r) => String(r.index)}
          emptyMessage={report.perQuery.length === 0 ? "No per-query details." : "No misses or errors."}
        />
      </Section>
    </div>
  );
}
