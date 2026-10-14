"use client";

import MarkdownContent from "@/components/chat/MarkdownContent";
import MetricCard from "@/components/dashboard/MetricCard";
import { Badge, Section } from "@/components/dashboard/Section";
import { CARD_CLASS } from "@/components/shared/styles";
import {
  MODE_LABELS,
  formatElapsed,
  formatScore,
  type AgenticMeta,
  type SearchCitation,
  type SearchResult,
} from "@/lib/search";

const NO_CITATIONS: never[] = [];

function cell(value: number | null | string): string {
  return value === null || value === "" ? "-" : String(value);
}

export function CitationsList({ citations }: { citations: SearchCitation[] }) {
  return (
    <ul className="space-y-2" aria-label="Citations">
      {citations.map((c, index) => (
        <li key={`${c.id}-${index}`} className={`${CARD_CLASS} space-y-1`}>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-mono text-xs text-slate-500">[{cell(c.id)}]</span>
            <span className="break-all font-medium text-slate-900 dark:text-slate-100">{c.source || "(unknown)"}</span>
            <Badge>page {cell(c.page)}</Badge>
            <Badge tone="blue">score {formatScore(c.score)}</Badge>
          </div>
          {c.text && <p className="line-clamp-4 whitespace-pre-wrap text-xs text-slate-600 dark:text-slate-300">{c.text}</p>}
        </li>
      ))}
    </ul>
  );
}

export function AgenticPanel({ meta }: { meta: AgenticMeta }) {
  return (
    <Section title="Agentic search details">
      <div className="grid gap-3 sm:grid-cols-4">
        <MetricCard label="Mode" value={meta.mode ?? "-"} />
        <MetricCard label="LLM tokens" value={meta.total_llm_tokens ?? "-"} />
        <MetricCard label="LLM calls" value={meta.llm_call_count ?? "-"} />
        <MetricCard label="Elapsed" value={formatElapsed(meta.elapsed_ms)} />
      </div>
      {meta.agentic_answer && (
        <details className={CARD_CLASS}>
          <summary className="cursor-pointer text-sm font-medium">Agentic answer</summary>
          <div className="mt-2">
            <MarkdownContent content={meta.agentic_answer} streaming={false} citations={NO_CITATIONS} />
          </div>
        </details>
      )}
      {meta.evidence_files.length > 0 && (
        <details className={CARD_CLASS}>
          <summary className="cursor-pointer text-sm font-medium">Evidence files ({meta.evidence_files.length})</summary>
          <ul className="mt-2 space-y-1">
            {meta.evidence_files.map((file, i) => (
              <li key={`${file}-${i}`} className="break-all font-mono text-xs">
                {file}
              </li>
            ))}
          </ul>
        </details>
      )}
      {meta.telemetry.length > 0 && (
        <details className={CARD_CLASS}>
          <summary className="cursor-pointer text-sm font-medium">Sidecar telemetry</summary>
          <table className="mt-2 w-full text-left text-xs">
            <tbody>
              {meta.telemetry.map((row) => (
                <tr key={row.key} className="border-t border-slate-100 dark:border-slate-700">
                  <th className="py-1 pr-3 font-mono font-medium">{row.key}</th>
                  <td className="py-1 break-all">{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            A zero-LLM path only means this run made no LLM call and did not enter the loop; it does not imply a
            knowledge-cluster reuse hit.
          </p>
        </details>
      )}
    </Section>
  );
}

export default function ResultView({ result }: { result: SearchResult }) {
  if (result.is_error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
      >
        <div className="font-medium">Search failed ({MODE_LABELS[result.mode]})</div>
        <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-xs">{result.error}</pre>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
        <Badge tone="purple">{MODE_LABELS[result.mode]}</Badge>
        <Badge>{formatElapsed(result.elapsed_ms)}</Badge>
        <span>Recorded as a trace (see Query Traces).</span>
      </div>
      <Section title="Result">
        {result.markdown ? (
          <div className={CARD_CLASS}>
            <MarkdownContent content={result.markdown} streaming={false} citations={NO_CITATIONS} />
          </div>
        ) : (
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No results. In Fast/Deep mode make sure the sidecar is running and the search path holds a corpus.
          </p>
        )}
      </Section>
      {result.agentic && <AgenticPanel meta={result.agentic} />}
      {result.citations.length > 0 && (
        <Section title="Citations">
          <CitationsList citations={result.citations} />
        </Section>
      )}
    </div>
  );
}
