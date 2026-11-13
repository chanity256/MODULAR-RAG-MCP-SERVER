"use client";

import { useRef, useState } from "react";
import { ErrorNotice } from "@/components/dashboard/AsyncState";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, CARD_CLASS, INPUT_CLASS } from "@/components/shared/styles";
import {
  NEW_COLLECTION,
  conflictTaskId,
  formatBytes,
  resolveCollection,
  submitIngestion,
  uploadFiles,
} from "@/lib/data";
import { toApiError, type ApiError } from "@/lib/errors";
import type { TaskRecord } from "@/lib/tasks";

type Phase = "idle" | "uploading" | "submitting";

export default function IngestionForm({
  collections,
  onSubmitted,
  onFollowExisting,
}: {
  collections: readonly string[];
  onSubmitted: (taskId: string, task: TaskRecord) => void;
  onFollowExisting: (taskId: string) => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [choice, setChoice] = useState<string>("");
  const [newName, setNewName] = useState("default");
  const [force, setForce] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<ApiError | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const effectiveChoice = choice || (collections[0] ?? NEW_COLLECTION);
  const busy = phase !== "idle";

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    setFiles((current) => {
      const byName = new Map(current.map((f) => [f.name, f]));
      for (const file of Array.from(incoming)) byName.set(file.name, file);
      return Array.from(byName.values());
    });
    if (inputRef.current) inputRef.current.value = "";
  };

  const submit = async () => {
    setError(null);
    setHint(null);
    if (files.length === 0) {
      setHint("Select at least one file first.");
      return;
    }
    try {
      setPhase("uploading");
      const uploaded = await uploadFiles(files);
      setPhase("submitting");
      const submitted = await submitIngestion({
        files: uploaded.map((f) => f.name),
        collection: resolveCollection(effectiveChoice, newName),
        force,
      });
      setFiles([]);
      onSubmitted(submitted.task_id, submitted.task);
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setPhase("idle");
    }
  };

  const existingTask = error ? conflictTaskId(error) : null;

  return (
    <form
      className={`${CARD_CLASS} space-y-4`}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className="space-y-2">
        <div className="text-sm font-medium text-slate-700 dark:text-slate-200">Files</div>
        <input
          ref={inputRef}
          type="file"
          multiple
          aria-label="Files to ingest"
          disabled={busy}
          onChange={(e) => addFiles(e.target.files)}
          className="block text-sm text-slate-600 dark:text-slate-300"
        />
        {files.length > 0 && (
          <ul className="space-y-1 text-xs text-slate-600 dark:text-slate-300">
            {files.map((file) => (
              <li key={file.name} className="flex items-center gap-2">
                <span className="truncate font-mono">{file.name}</span>
                <span className="text-slate-400">{formatBytes(file.size)}</span>
                <button
                  type="button"
                  className={BUTTON_SECONDARY}
                  disabled={busy}
                  onClick={() => setFiles((current) => current.filter((f) => f.name !== file.name))}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
          Target collection
          <select
            aria-label="Target collection"
            className={INPUT_CLASS}
            value={effectiveChoice}
            disabled={busy}
            onChange={(e) => setChoice(e.target.value)}
          >
            {collections.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
            <option value={NEW_COLLECTION}>New collection...</option>
          </select>
        </label>
        {effectiveChoice === NEW_COLLECTION && (
          <input
            type="text"
            aria-label="New collection name"
            className={`${INPUT_CLASS} w-48`}
            value={newName}
            disabled={busy}
            onChange={(e) => setNewName(e.target.value)}
          />
        )}
        <label className="flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300">
          <input type="checkbox" checked={force} disabled={busy} onChange={(e) => setForce(e.target.checked)} />
          Force re-ingest (--force)
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button type="submit" className={BUTTON_PRIMARY} disabled={busy}>
          {phase === "uploading" ? "Uploading..." : phase === "submitting" ? "Submitting..." : "Start ingestion"}
        </button>
        {hint && (
          <span role="status" className="text-xs text-amber-700 dark:text-amber-300">
            {hint}
          </span>
        )}
      </div>

      {error && (
        <div className="space-y-2">
          <ErrorNotice error={error} onRetry={() => void submit()} />
          {existingTask && (
            <button type="button" className={BUTTON_SECONDARY} onClick={() => onFollowExisting(existingTask)}>
              Follow the active task
            </button>
          )}
        </div>
      )}
    </form>
  );
}
