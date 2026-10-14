"use client";

import { useState } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import { PageHeader } from "@/components/dashboard/Section";
import { BUTTON_PRIMARY, CARD_CLASS, INPUT_CLASS } from "@/components/shared/styles";
import { useAsyncData } from "@/hooks/useAsyncData";
import { toApiError, type ApiError } from "@/lib/errors";
import {
  MODE_LABELS,
  SEARCH_MODES,
  fetchSearchCapabilities,
  initialMode,
  isModeAvailable,
  runSearch,
  validateSearchForm,
  type SearchMode,
  type SearchResult,
} from "@/lib/search";
import ResultView from "./ResultView";

export default function PlaygroundView() {
  const capabilities = useAsyncData((signal) => fetchSearchCapabilities(signal), "capabilities");
  const caps = capabilities.data;

  const [pickedMode, setPickedMode] = useState<SearchMode | null>(null);
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState("5");
  const [collection, setCollection] = useState("");
  const [running, setRunning] = useState<SearchMode | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  // Last outcome per mode so switching modes compares runs instead of discarding them.
  const [results, setResults] = useState<Partial<Record<SearchMode, SearchResult>>>({});
  const [errors, setErrors] = useState<Partial<Record<SearchMode, ApiError>>>({});

  const mode: SearchMode =
    pickedMode && isModeAvailable(caps, pickedMode) ? pickedMode : caps ? initialMode(caps) : "normal";
  const result = results[mode];
  const error = errors[mode];
  const busy = running !== null;

  const submit = async () => {
    const problem = validateSearchForm({ mode, query, topK, collection });
    setValidation(problem);
    if (problem) return;
    const target = mode;
    setRunning(target);
    setErrors((current) => ({ ...current, [target]: undefined }));
    try {
      const next = await runSearch({ mode: target, query, topK, collection });
      setResults((current) => ({ ...current, [target]: next }));
    } catch (e) {
      setErrors((current) => ({ ...current, [target]: toApiError(e) }));
    } finally {
      setRunning(null);
    }
  };

  const label = "flex flex-col gap-1 text-xs font-medium text-slate-600 dark:text-slate-300";
  const modeReason = caps?.availability[mode].reason;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <PageHeader
        title="Query Playground"
        description="Run a query in Normal (hybrid), Fast or Deep (agentic sidecar) mode. Each run is recorded as a trace."
      />

      {capabilities.error && <ErrorNotice error={capabilities.error} onRetry={capabilities.reload} />}
      {!capabilities.error && capabilities.loading && <LoadingState label="Loading capabilities..." />}

      <form
        className={`${CARD_CLASS} space-y-4`}
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div role="group" aria-label="Search mode" className="flex flex-wrap gap-2">
          {SEARCH_MODES.map((m) => {
            const status = caps?.availability[m];
            const available = isModeAvailable(caps, m);
            const selected = m === mode;
            return (
              <button
                key={m}
                type="button"
                aria-pressed={selected}
                disabled={!available || busy}
                title={status?.reason ?? undefined}
                onClick={() => setPickedMode(m)}
                className={`rounded border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${
                  selected
                    ? "border-blue-600 bg-blue-600 text-white"
                    : "border-slate-300 text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-700"
                }`}
              >
                {MODE_LABELS[m]}
                {results[m] && <span aria-label="has result"> •</span>}
              </button>
            );
          })}
        </div>
        {caps &&
          SEARCH_MODES.filter((m) => !caps.availability[m].available).map((m) => (
            <p key={m} className="text-xs text-slate-500 dark:text-slate-400">
              {MODE_LABELS[m]} unavailable: {caps.availability[m].reason ?? "disabled by configuration"}
            </p>
          ))}

        <label className={label}>
          Query
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask something about the knowledge base"
            className={`${INPUT_CLASS} w-full`}
          />
        </label>
        <div className="flex flex-wrap gap-4">
          <label className={label}>
            Top-K
            <input
              type="number"
              min={1}
              max={100}
              value={topK}
              onChange={(e) => setTopK(e.target.value)}
              className={`${INPUT_CLASS} w-24`}
            />
          </label>
          {mode === "normal" && (
            <label className={label}>
              Collection (optional)
              <input
                type="text"
                value={collection}
                onChange={(e) => setCollection(e.target.value)}
                placeholder="default"
                className={`${INPUT_CLASS} w-56`}
              />
            </label>
          )}
        </div>
        {validation && (
          <p role="alert" className="text-xs text-red-600 dark:text-red-300">
            {validation}
          </p>
        )}
        <div className="flex items-center gap-3">
          <button type="submit" disabled={busy || (caps !== undefined && !isModeAvailable(caps, mode))} className={BUTTON_PRIMARY}>
            {busy ? "Searching..." : "Search"}
          </button>
          {modeReason && <span className="text-xs text-amber-600 dark:text-amber-300">{modeReason}</span>}
        </div>
      </form>

      {running === mode && <LoadingState label={`Running ${MODE_LABELS[mode]} search...`} />}
      {error && <SearchError error={error} onRetry={() => void submit()} />}
      {result && !error && running !== mode && <ResultView result={result} />}
    </div>
  );
}

function SearchError({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  if (error.code === "AGENTIC_DISABLED" || error.code === "DEEP_DISABLED") {
    return (
      <div
        role="alert"
        className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
      >
        <div className="font-medium">{error.code === "DEEP_DISABLED" ? "Deep mode is disabled" : "Agentic search is disabled"}</div>
        <div className="mt-0.5 text-xs">{error.message}</div>
      </div>
    );
  }
  return <ErrorNotice error={error} onRetry={onRetry} />;
}
