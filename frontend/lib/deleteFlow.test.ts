import { describe, expect, it } from "vitest";
import { ApiError } from "./errors";
import { deleteFlowReducer, type DeleteFlowAction, type DeleteFlowState } from "./deleteFlow";

type Doc = { source_path: string };
const doc: Doc = { source_path: "a.md" };
const idle: DeleteFlowState<Doc> = { phase: "idle" };

function run(actions: DeleteFlowAction<Doc>[], from: DeleteFlowState<Doc> = idle): DeleteFlowState<Doc> {
  return actions.reduce((state, action) => deleteFlowReducer(state, action), from);
}

describe("deleteFlowReducer", () => {
  it("requires an explicit confirmation before deleting", () => {
    expect(run([{ type: "confirm" }])).toEqual(idle);
    expect(run([{ type: "request", target: doc }])).toEqual({ phase: "confirming", target: doc });
    expect(run([{ type: "request", target: doc }, { type: "confirm" }])).toEqual({ phase: "deleting", target: doc });
  });

  it("cancel returns to idle only before the request is in flight", () => {
    expect(run([{ type: "request", target: doc }, { type: "cancel" }])).toEqual(idle);
    const deleting = run([{ type: "request", target: doc }, { type: "confirm" }]);
    expect(run([{ type: "cancel" }], deleting)).toEqual(deleting);
  });

  it("ignores a second request while deleting", () => {
    const deleting = run([{ type: "request", target: doc }, { type: "confirm" }]);
    expect(run([{ type: "request", target: { source_path: "other" } }], deleting)).toEqual(deleting);
  });

  it("completes on success", () => {
    expect(run([{ type: "request", target: doc }, { type: "confirm" }, { type: "succeeded" }])).toEqual({
      phase: "done",
      target: doc,
    });
    expect(run([{ type: "succeeded" }])).toEqual(idle);
  });

  it("keeps the target on failure so the delete can be retried or cancelled", () => {
    const error = new ApiError(404, "not_found", "gone");
    const failed = run([{ type: "request", target: doc }, { type: "confirm" }, { type: "failed", error }]);
    expect(failed).toEqual({ phase: "error", target: doc, error });
    expect(run([{ type: "confirm" }], failed)).toEqual({ phase: "deleting", target: doc });
    expect(run([{ type: "cancel" }], failed)).toEqual(idle);
  });

  it("reset always returns to idle", () => {
    expect(run([{ type: "request", target: doc }, { type: "reset" }])).toEqual(idle);
  });
});
