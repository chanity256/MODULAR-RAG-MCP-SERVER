import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState, type ChatAction, type ChatState } from "./reducer";
import type { Citation, DownstreamFrame } from "./types";

const CITATIONS: Citation[] = [
  { id: 1, source: "docs/a.pdf", page: 2, chunk_id: "c1", text: "alpha", score: 0.91 },
  { id: 2, source: "docs/b.md", page: null, chunk_id: "c2", text: "beta", score: 0.5 },
];

function run(actions: ChatAction[], from: ChatState = initialChatState): ChatState {
  return actions.reduce(chatReducer, from);
}

const send = (message = "hi"): ChatAction => ({ type: "send", userId: "u1", assistantId: "a1", message, retrieval: "hybrid" });
const frame = (f: DownstreamFrame): ChatAction => ({ type: "frame", frame: f });

const result = (over: Partial<Extract<DownstreamFrame, { type: "result" }>> = {}): DownstreamFrame => ({
  type: "result",
  content: "final answer",
  session_id: "s1",
  message_id: "m1",
  grounded: true,
  trace_id: "t1",
  ...over,
});

describe("chatReducer: send", () => {
  it("appends the user message and a streaming assistant placeholder", () => {
    const state = run([send("what is rag?")]);
    expect(state.busy).toBe(true);
    expect(state.messages.map((m) => [m.id, m.role, m.status])).toEqual([
      ["u1", "user", "done"],
      ["a1", "assistant", "streaming"],
    ]);
    expect(state.messages[0].content).toBe("what is rag?");
  });

  it("clears a previous error", () => {
    const failed = run([send(), frame({ type: "error", code: "UPSTREAM", message: "boom" })]);
    expect(failed.error).not.toBeNull();
    expect(chatReducer(failed, { type: "send", userId: "u2", assistantId: "a2", message: "again" }).error).toBeNull();
  });
});

describe("chatReducer: a normal turn (spec 4.7 sequence)", () => {
  it("session -> status -> search_complete -> sources -> stream* -> result", () => {
    const state = run([
      send(),
      frame({ type: "session", session_id: "s1" }),
      frame({ type: "status", stage: "rewriting", message: "rewriting" }),
      frame({ type: "status", stage: "retrieving" }),
      frame({ type: "search_log", task_id: "t", level: "info", message: "scanning" }),
      frame({ type: "search_complete", message: "found 2" }),
      frame({ type: "sources", citations: CITATIONS }),
      frame({ type: "status", stage: "generating" }),
      frame({ type: "stream", content: "Hello " }),
      frame({ type: "stream", content: "world [1]" }),
    ]);

    const assistant = state.messages[1];
    expect(state.sessionId).toBe("s1");
    expect(state.busy).toBe(true);
    expect(assistant.status).toBe("streaming");
    expect(assistant.content).toBe("Hello world [1]");
    expect(assistant.stage).toBe("generating");
    expect(assistant.citations).toEqual(CITATIONS);
    expect(assistant.logs).toEqual([{ level: "info", message: "scanning", timestamp: undefined }]);
    expect(assistant.searchComplete).toBe("found 2");

    const done = chatReducer(
      state,
      frame(result({ content: "Hello world [1]", rewritten_query: "rag definition", usage: { total_tokens: 12 } })),
    );
    const final = done.messages[1];
    expect(done.busy).toBe(false);
    expect(done.error).toBeNull();
    expect(final.status).toBe("done");
    expect(final.grounded).toBe(true);
    expect(final.rewrittenQuery).toBe("rag definition");
    expect(final.usage).toEqual({ total_tokens: 12 });
    expect(final.traceId).toBe("t1");
    expect(final.stage).toBeNull();
  });

  it("the result frame text is authoritative over streamed text", () => {
    const state = run([send(), frame({ type: "stream", content: "partial" }), frame(result({ content: "complete text" }))]);
    expect(state.messages[1].content).toBe("complete text");
  });

  it("keeps streamed text when the result content is empty", () => {
    const state = run([send(), frame({ type: "stream", content: "kept" }), frame(result({ content: "" }))]);
    expect(state.messages[1].content).toBe("kept");
  });

  it("search_error is recorded without ending the turn", () => {
    const state = run([send(), frame({ type: "search_error", message: "sidecar down" })]);
    expect(state.messages[1].searchError).toBe("sidecar down");
    expect(state.busy).toBe(true);
    expect(state.messages[1].status).toBe("streaming");
  });
});

describe("chatReducer: retry status", () => {
  it("shows retrying and returns to generating", () => {
    const retrying = run([send(), frame({ type: "status", stage: "retrying", message: "retrying generation" })]);
    expect(retrying.messages[1].stage).toBe("retrying");
    expect(retrying.messages[1].stageMessage).toBe("retrying generation");
    const next = chatReducer(retrying, frame({ type: "status", stage: "generating" }));
    expect(next.messages[1].stage).toBe("generating");
    expect(next.messages[1].stageMessage).toBeUndefined();
  });
});

describe("chatReducer: ungrounded answers", () => {
  it("records grounded=false from the result frame", () => {
    const state = run([send(), frame({ type: "stream", content: "chat only" }), frame(result({ content: "chat only", grounded: false }))]);
    expect(state.messages[1].grounded).toBe(false);
    expect(state.messages[1].status).toBe("done");
  });
});

describe("chatReducer: cancel", () => {
  it("marks a cancelled result as cancelled and keeps the partial content", () => {
    const state = run([
      send(),
      frame({ type: "stream", content: "par" }),
      frame(result({ content: "par", cancelled: true })),
    ]);
    expect(state.busy).toBe(false);
    expect(state.error).toBeNull();
    expect(state.messages[1].status).toBe("cancelled");
    expect(state.messages[1].content).toBe("par");
  });
});

describe("chatReducer: errors", () => {
  it("error frame is terminal and surfaces the error", () => {
    const state = run([send(), frame({ type: "error", code: "AUTH", message: "bad token" })]);
    expect(state.busy).toBe(false);
    expect(state.error).toEqual({ code: "AUTH", message: "bad token" });
  });

  it("drops the empty assistant placeholder on error", () => {
    const state = run([send(), frame({ type: "error", code: "UPSTREAM", message: "x" })]);
    expect(state.messages.map((m) => m.role)).toEqual(["user"]);
  });

  it("keeps partial content when the error arrives mid-stream", () => {
    const state = run([
      send(),
      frame({ type: "stream", content: "half an ans" }),
      frame({ type: "error", code: "UPSTREAM", message: "stream broke" }),
    ]);
    expect(state.messages).toHaveLength(2);
    expect(state.messages[1].content).toBe("half an ans");
    expect(state.messages[1].status).toBe("cancelled");
    expect(state.error?.code).toBe("UPSTREAM");
  });

  it("dismiss_error clears the alert only", () => {
    const failed = run([send(), frame({ type: "error", code: "UPSTREAM", message: "x" })]);
    const cleared = chatReducer(failed, { type: "dismiss_error" });
    expect(cleared.error).toBeNull();
    expect(cleared.messages).toBe(failed.messages);
  });
});

describe("chatReducer: connection close", () => {
  it("is a no-op after a terminal frame", () => {
    const done = run([send(), frame(result())]);
    expect(chatReducer(done, { type: "closed" })).toBe(done);
  });

  it("without a terminal frame ends the turn with a connection error and keeps partial text", () => {
    const state = run([send(), frame({ type: "stream", content: "so far" }), { type: "closed" }]);
    expect(state.busy).toBe(false);
    expect(state.error?.code).toBe("CONNECTION");
    expect(state.messages[1].content).toBe("so far");
    expect(state.messages[1].status).toBe("cancelled");
  });

  it("drops an empty placeholder when nothing arrived", () => {
    const state = run([send(), { type: "closed" }]);
    expect(state.messages).toHaveLength(1);
    expect(state.error?.code).toBe("CONNECTION");
  });
});

describe("chatReducer: robustness", () => {
  it("ignores unknown frame types", () => {
    const before = run([send()]);
    const unknown = { type: "future_frame", payload: 1 } as unknown as DownstreamFrame;
    expect(chatReducer(before, frame(unknown))).toBe(before);
  });

  it("ignores content frames when no turn is live", () => {
    const idle = run([send(), frame(result())]);
    const after = chatReducer(idle, frame({ type: "stream", content: "late" }));
    expect(after.messages[1].content).toBe("final answer");
    expect(chatReducer(idle, frame(result({ content: "dup" }))).messages[1].content).toBe("final answer");
  });

  it("caps the retained search log", () => {
    let state = run([send()]);
    for (let i = 0; i < 250; i++) {
      state = chatReducer(state, frame({ type: "search_log", level: "info", message: `m${i}` }));
    }
    const logs = state.messages[1].logs;
    expect(logs).toHaveLength(200);
    expect(logs[logs.length - 1].message).toBe("m249");
  });
});

describe("chatReducer: sessions", () => {
  it("load_session restores persisted messages with sources and metadata", () => {
    const state = chatReducer(initialChatState, {
      type: "load_session",
      session: {
        session_id: "s9",
        title: "t",
        created_at: "",
        updated_at: "",
        message_count: 2,
        messages: [
          { id: "m1", session_id: "s9", role: "user", content: "q", sources: [], metadata: {}, created_at: "" },
          {
            id: "m2",
            session_id: "s9",
            role: "assistant",
            content: "a [1]",
            sources: CITATIONS,
            metadata: { grounded: false, rewritten_query: "rq", usage: { prompt_tokens: 3, completion_tokens: 4 }, cancelled: true },
            created_at: "",
          },
          { id: "m3", session_id: "s9", role: "system", content: "ignored", sources: [], metadata: {}, created_at: "" },
        ],
      },
    });
    expect(state.sessionId).toBe("s9");
    expect(state.messages).toHaveLength(2);
    const assistant = state.messages[1];
    expect(assistant.citations).toEqual(CITATIONS);
    expect(assistant.grounded).toBe(false);
    expect(assistant.rewrittenQuery).toBe("rq");
    expect(assistant.usage).toEqual({ prompt_tokens: 3, completion_tokens: 4, total_tokens: 7 });
    expect(assistant.status).toBe("cancelled");
  });

  it("new_chat resets everything", () => {
    const state = run([send(), frame({ type: "session", session_id: "s1" }), { type: "new_chat" }]);
    expect(state).toEqual(initialChatState);
  });
});
