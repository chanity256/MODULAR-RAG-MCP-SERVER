"use client";

import { useState } from "react";
import { getApiToken, setApiToken } from "@/lib/api";
import type { ApiError } from "@/lib/errors";

export function LoadingState({ label = "Loading..." }: { label?: string }) {
  return (
    <div role="status" className="p-6 text-center text-sm text-slate-500 dark:text-slate-400">
      {label}
    </div>
  );
}

/** Lets the user store the `RAG_API_TOKEN` used for the `Authorization: Bearer` header. */
export function TokenControl({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState(() => getApiToken());

  return (
    <form
      className="mt-3 flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        setApiToken(value.trim());
        onSaved();
      }}
    >
      <input
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="API token (RAG_API_TOKEN)"
        aria-label="API token"
        autoComplete="off"
        className="w-64 rounded border border-slate-300 bg-white px-2 py-1 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
      />
      <button
        type="submit"
        className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700"
      >
        Save and retry
      </button>
    </form>
  );
}

export function ErrorNotice({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
    >
      <div className="font-medium">{error.message}</div>
      <div className="mt-0.5 text-xs opacity-80">
        {error.code}
        {error.status > 0 && ` (HTTP ${error.status})`}
      </div>
      {error.isUnauthorized && (
        <>
          <p className="mt-2">The API requires a token. Enter the value of RAG_API_TOKEN to continue.</p>
          <TokenControl onSaved={onRetry} />
        </>
      )}
      {!error.isUnauthorized && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded border border-red-300 px-2.5 py-1 text-xs hover:bg-red-100 dark:border-red-800 dark:hover:bg-red-900/40"
        >
          Retry
        </button>
      )}
    </div>
  );
}
