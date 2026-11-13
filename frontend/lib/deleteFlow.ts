import type { ApiError } from "./errors";

/**
 * Two-step delete: the destructive request is only sent from `confirming`, and a request
 * that is already in flight cannot be re-triggered or cancelled.
 */
export type DeleteFlowState<T> =
  | { phase: "idle" }
  | { phase: "confirming"; target: T }
  | { phase: "deleting"; target: T }
  | { phase: "done"; target: T }
  | { phase: "error"; target: T; error: ApiError };

export type DeleteFlowAction<T> =
  | { type: "request"; target: T }
  | { type: "cancel" }
  | { type: "confirm" }
  | { type: "succeeded" }
  | { type: "failed"; error: ApiError }
  | { type: "reset" };

export const IDLE_DELETE: DeleteFlowState<never> = { phase: "idle" };

export function deleteFlowReducer<T>(state: DeleteFlowState<T>, action: DeleteFlowAction<T>): DeleteFlowState<T> {
  switch (action.type) {
    case "request":
      return state.phase === "deleting" ? state : { phase: "confirming", target: action.target };
    case "cancel":
      return state.phase === "confirming" || state.phase === "error" ? { phase: "idle" } : state;
    case "confirm":
      // `error` allows retrying the same target without asking again.
      return state.phase === "confirming" || state.phase === "error"
        ? { phase: "deleting", target: state.target }
        : state;
    case "succeeded":
      return state.phase === "deleting" ? { phase: "done", target: state.target } : state;
    case "failed":
      return state.phase === "deleting" ? { phase: "error", target: state.target, error: action.error } : state;
    case "reset":
      return { phase: "idle" };
  }
}
