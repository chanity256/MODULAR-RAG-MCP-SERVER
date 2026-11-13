"use client";

import { useState } from "react";
import { ErrorNotice } from "@/components/dashboard/AsyncState";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, CARD_CLASS, INPUT_CLASS } from "@/components/shared/styles";
import { conflictTaskId } from "@/lib/data";
import { toApiError, type ApiError } from "@/lib/errors";
import {
  EVALUATOR_BACKENDS,
  submitEvaluation,
  validateEvaluationForm,
  type EvaluationFormValues,
} from "@/lib/evaluation";
import type { TaskRecord } from "@/lib/tasks";

export default function EvaluationForm({
  onSubmitted,
  onFollowExisting,
}: {
  onSubmitted: (taskId: string, task: TaskRecord) => void;
  onFollowExisting: (taskId: string) => void;
}) {
  const [values, setValues] = useState<EvaluationFormValues>({
    backend: "composite",
    testSetPath: "",
    topK: "0",
    collection: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [validation, setValidation] = useState<string | null>(null);

  const set = <K extends keyof EvaluationFormValues>(key: K, value: EvaluationFormValues[K]) =>
    setValues((current) => ({ ...current, [key]: value }));

  const submit = async () => {
    setError(null);
    const problem = validateEvaluationForm(values);
    setValidation(problem);
    if (problem) return;
    setSubmitting(true);
    try {
      const submitted = await submitEvaluation(values);
      onSubmitted(submitted.task_id, submitted.task);
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setSubmitting(false);
    }
  };

  const existingTask = error ? conflictTaskId(error) : null;
  const label = "flex flex-col gap-1 text-xs font-medium text-slate-600 dark:text-slate-300";

  return (
    <form
      className={`${CARD_CLASS} space-y-4`}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <label className={label}>
          Evaluator backend
          <select
            className={INPUT_CLASS}
            value={values.backend}
            disabled={submitting}
            onChange={(e) => set("backend", e.target.value as EvaluationFormValues["backend"])}
          >
            {EVALUATOR_BACKENDS.map((backend) => (
              <option key={backend} value={backend}>
                {backend}
              </option>
            ))}
          </select>
        </label>
        <label className={label}>
          Golden test set path
          <input
            type="text"
            className={INPUT_CLASS}
            value={values.testSetPath}
            placeholder="Default: evaluation.golden_test_set"
            disabled={submitting}
            onChange={(e) => set("testSetPath", e.target.value)}
          />
        </label>
        <label className={label}>
          Top-K (0 = configured default)
          <input
            type="number"
            min={0}
            step={1}
            className={INPUT_CLASS}
            value={values.topK}
            disabled={submitting}
            onChange={(e) => set("topK", e.target.value)}
          />
        </label>
        <label className={label}>
          Restrict to collection (optional)
          <input
            type="text"
            className={INPUT_CLASS}
            value={values.collection}
            disabled={submitting}
            onChange={(e) => set("collection", e.target.value)}
          />
        </label>
      </div>
      <div className="flex items-center gap-3">
        <button type="submit" className={BUTTON_PRIMARY} disabled={submitting}>
          {submitting ? "Submitting..." : "Run evaluation"}
        </button>
        {validation && (
          <span role="alert" className="text-xs text-red-700 dark:text-red-300">
            {validation}
          </span>
        )}
      </div>
      {error && (
        <div className="space-y-2">
          <ErrorNotice error={error} onRetry={() => void submit()} />
          {existingTask && (
            <button type="button" className={BUTTON_SECONDARY} onClick={() => onFollowExisting(existingTask)}>
              Follow the active run
            </button>
          )}
        </div>
      )}
    </form>
  );
}
