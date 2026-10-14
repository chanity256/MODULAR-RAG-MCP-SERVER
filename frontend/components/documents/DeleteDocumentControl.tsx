"use client";

import { useReducer } from "react";
import { ErrorNotice } from "@/components/dashboard/AsyncState";
import { BUTTON_DANGER, BUTTON_DANGER_OUTLINE, BUTTON_SECONDARY } from "@/components/shared/styles";
import { deleteDocument, type DeleteResult, type DocumentSummary } from "@/lib/data";
import { deleteFlowReducer, type DeleteFlowAction, type DeleteFlowState } from "@/lib/deleteFlow";
import { toApiError } from "@/lib/errors";

type Target = Pick<DocumentSummary, "source_path" | "collection">;

/** Delete button with an explicit confirm step; the request is only sent after confirmation. */
export default function DeleteDocumentControl({
  document,
  onDeleted,
}: {
  document: Target;
  onDeleted: (result: DeleteResult) => void;
}) {
  const [state, dispatch] = useReducer(
    (current: DeleteFlowState<Target>, action: DeleteFlowAction<Target>) => deleteFlowReducer(current, action),
    { phase: "idle" } as DeleteFlowState<Target>,
  );

  const confirm = () => {
    dispatch({ type: "confirm" });
    deleteDocument(document.source_path, document.collection).then(
      (result) => {
        dispatch({ type: "succeeded" });
        onDeleted(result);
      },
      (error: unknown) => dispatch({ type: "failed", error: toApiError(error) }),
    );
  };

  if (state.phase === "idle" || state.phase === "done") {
    return (
      <button type="button" className={BUTTON_DANGER_OUTLINE} onClick={() => dispatch({ type: "request", target: document })}>
        Delete
      </button>
    );
  }

  return (
    <div className="space-y-2" role="alertdialog" aria-label="Confirm delete">
      <p className="text-sm text-slate-700 dark:text-slate-200">
        Delete <span className="font-mono">{document.source_path}</span> from{" "}
        <span className="font-medium">{document.collection}</span>? This removes its chunks, BM25 entries, images and
        ingestion history.
      </p>
      {state.phase === "error" && <ErrorNotice error={state.error} onRetry={confirm} />}
      <div className="flex gap-2">
        <button type="button" className={BUTTON_DANGER} disabled={state.phase === "deleting"} onClick={confirm}>
          {state.phase === "deleting" ? "Deleting..." : state.phase === "error" ? "Retry delete" : "Confirm delete"}
        </button>
        <button
          type="button"
          className={BUTTON_SECONDARY}
          disabled={state.phase === "deleting"}
          onClick={() => dispatch({ type: "cancel" })}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
