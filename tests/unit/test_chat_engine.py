"""WC6.4: ChatEngine（帧顺序、错误矩阵、重试、降级、取消、每轮一条 trace）。全部离线。"""

from __future__ import annotations

import threading
from typing import Any

import httpx
import pytest

from core.chat.answer_generator import NOT_FOUND_MESSAGE
from core.chat.engine import (
    ChatEngine,
    classify_error,
)
from core.chat.retriever import ChatRetrievalResult, ChatRetriever, DeepDisabledError
from core.chat.session_store import SessionNotFoundError, SessionStore
from core.settings import ChatSettings
from core.types import RetrievalResult
from libs.agentic_search.base_agentic_search import (
    AgenticSearchBackendError,
    AgenticSearchResponseError,
    AgenticSearchTimeoutError,
)
from libs.llm.base_llm import BaseLLM, ChatResponse
from libs.llm.openai_llm import LLMRequestError


# --------------------------------------------------------------------------- fakes

class FakeLLM(BaseLLM):
    """``stream_plans``: 每次 chat_stream 调用消费一个计划；字符串 = 增量，异常实例 = 抛出。"""

    def __init__(self, stream_plans=None, *, relevance="[1]", rewrite="改写后的查询", usage=None):
        self.stream_plans = list(stream_plans or [["答案", " [1]"]])
        self.relevance = relevance
        self.rewrite = rewrite
        self.usage = usage
        self.stream_calls = 0
        self.chat_prompts: list[str] = []
        self.stream_messages: list[list[Any]] = []
        self.last_usage = None

    def chat(self, messages, *, trace=None, **kwargs):
        prompt = messages[-1].content
        self.chat_prompts.append(prompt)
        reply = self.relevance if "Return the numbers" in prompt else self.rewrite
        if isinstance(reply, Exception):
            raise reply
        return ChatResponse(content=reply)

    def chat_stream(self, messages, *, trace=None, **kwargs):
        self.stream_messages.append(list(messages))
        plan = self.stream_plans[min(self.stream_calls, len(self.stream_plans) - 1)]
        self.stream_calls += 1
        self.last_usage = None
        for item in plan:
            if isinstance(item, Exception):
                raise item
            yield item
        self.last_usage = self.usage


class FakeRetriever:
    def __init__(self, plans=None, *, stage: str = "dense_retrieval"):
        self.plans = list(plans if plans is not None else [_hybrid_result()])
        self.stage = stage
        self.calls: list[dict[str, Any]] = []

    def retrieve(self, query, *, collection=None, top_k=None, trace=None):
        self.calls.append({"query": query, "collection": collection, "top_k": top_k, "trace": trace})
        plan = self.plans[min(len(self.calls) - 1, len(self.plans) - 1)]
        if isinstance(plan, Exception):
            raise plan
        if trace is not None:
            trace.record_stage(self.stage, count=len(plan.results))
        return plan


def _rr(i: int, text: str = "文本") -> RetrievalResult:
    return RetrievalResult(chunk_id=f"c{i}", score=0.9 - i / 100, text=f"{text}{i}", metadata={"source_path": f"doc{i}.md", "page": i})


def _hybrid_result(n: int = 2) -> ChatRetrievalResult:
    return ChatRetrievalResult(results=[_rr(i) for i in range(1, n + 1)])


def _agentic_result() -> ChatRetrievalResult:
    return ChatRetrievalResult(
        results=[],
        citations=[{"id": 1, "source": "a.md", "page": None, "chunk_id": "x", "text": "t", "score": 0.5}],
        answer="sidecar 答案 [1]",
    )


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "chat.db")
    yield s
    s.close()


class Harness:
    def __init__(self, store, *, llm=None, retrievers=None, settings=None):
        self.llm = llm or FakeLLM()
        self.retrievers = retrievers if retrievers is not None else {"hybrid": FakeRetriever()}
        self.persisted: list[Any] = []
        self.sleeps: list[float] = []
        self.store = store
        self.engine = ChatEngine(
            retrievers=self.retrievers,
            llm=self.llm,
            session_store=store,
            settings=settings or ChatSettings(),
            persist_trace=self.persisted.append,
            sleep=self.sleeps.append,
        )

    def run(self, message="什么是 RAG？", **kwargs):
        return list(self.engine.run_turn(message, **kwargs))


def types(frames):
    return [f["type"] for f in frames]


def stages(frames):
    return [(f["type"], f.get("stage")) for f in frames if f["type"] == "status"]


def _llm_error(status):
    return LLMRequestError("fake", "boom", status=status)


# --------------------------------------------------------------------------- happy path

def test_hybrid_happy_path_frame_order_and_persistence(store):
    h = Harness(store, llm=FakeLLM([["你好", "世界 [1]"]], usage={"total_tokens": 7}))
    frames = h.run(collection="docs", top_k=4)

    assert types(frames) == [
        "session", "status", "search_complete", "sources", "status", "stream", "stream", "result",
    ]
    assert stages(frames) == [("status", "retrieving"), ("status", "generating")]
    assert [f["content"] for f in frames if f["type"] == "stream"] == ["你好", "世界 [1]"]

    sources = next(f for f in frames if f["type"] == "sources")
    assert [c["id"] for c in sources["citations"]] == [1, 2]
    assert sources["citations"][0]["source"] == "doc1.md"

    result = frames[-1]
    assert result["content"] == "你好世界 [1]"
    assert result["grounded"] is True
    assert result["usage"] == {"total_tokens": 7}
    assert "rewritten_query" not in result
    assert result["session_id"] == frames[0]["session_id"]

    call = h.retrievers["hybrid"].calls[0]
    assert (call["query"], call["collection"], call["top_k"]) == ("什么是 RAG？", "docs", 4)

    session = store.get_session(result["session_id"])
    assert session["title"] == "什么是 RAG？"
    assert [m["role"] for m in session["messages"]] == ["user", "assistant"]
    assistant = session["messages"][1]
    assert assistant["id"] == result["message_id"]
    assert assistant["sources"] == sources["citations"]
    assert assistant["metadata"]["grounded"] is True
    assert assistant["metadata"]["trace_id"] == result["trace_id"]
    assert assistant["metadata"]["retrieval"] == "hybrid"
    assert "cancelled" not in assistant["metadata"]


def test_user_message_persisted_before_generation(store):
    seen: dict[str, Any] = {}
    h = Harness(store)
    original = h.llm.chat_stream

    def spy(messages, **kw):
        sid = next(iter(store.list_sessions()))["session_id"]
        seen["roles"] = [m["role"] for m in store.get_messages(sid)]
        return original(messages, **kw)

    h.llm.chat_stream = spy  # type: ignore[method-assign]
    h.run()
    assert seen["roles"] == ["user"]


def test_existing_session_uses_history_filter_and_rewrite(store):
    h = Harness(store, llm=FakeLLM([["第一轮"], ["第二轮"]], relevance="[1]", rewrite="RAG 的优点是什么"))
    first = h.run("什么是 RAG？")
    sid = first[0]["session_id"]
    second = h.run("它有什么优点？", session_id=sid)

    assert "session" not in types(second)
    assert stages(second)[0] == ("status", "rewriting")
    assert second[-1]["rewritten_query"] == "RAG 的优点是什么"
    assert h.retrievers["hybrid"].calls[1]["query"] == "RAG 的优点是什么"

    history_roles = [m.role for m in h.llm.stream_messages[1]]
    assert history_roles == ["system", "user", "assistant", "user"]
    assert h.llm.stream_messages[1][-1].content == "它有什么优点？"
    assert store.get_session(sid)["message_count"] == 4

    trace = h.persisted[1]
    assert trace.get_stage("chat_history")["messages_after_filter"] == 2
    assert trace.get_stage("chat_rewrite")["rewritten_query"] == "RAG 的优点是什么"
    assert store.get_messages(sid)[-1]["metadata"]["rewritten_query"] == "RAG 的优点是什么"


def test_relevance_filter_can_drop_all_history(store):
    h = Harness(store, llm=FakeLLM([["a"], ["b"]], relevance="[]"))
    sid = h.run("话题 A")[0]["session_id"]
    h.run("话题 B", session_id=sid)
    assert [m.role for m in h.llm.stream_messages[1]] == ["system", "user"]
    assert h.persisted[1].get_stage("chat_history")["turns_dropped"] == 1


def test_filter_and_rewrite_failures_do_not_break_turn(store):
    h = Harness(store, llm=FakeLLM([["a"], ["b"]], relevance=RuntimeError("x"), rewrite=RuntimeError("y")))
    sid = h.run("q1")[0]["session_id"]
    frames = h.run("q2", session_id=sid)
    assert frames[-1]["type"] == "result"
    assert h.retrievers["hybrid"].calls[1]["query"] == "q2"
    assert len(h.llm.stream_messages[1]) == 4
    assert "rewritten_query" not in frames[-1]


def test_no_history_means_no_rewrite_or_filter_llm_calls(store):
    h = Harness(store)
    h.run()
    assert h.llm.chat_prompts == []


def test_settings_switch_off_filter_and_rewrite(store):
    h = Harness(
        store,
        llm=FakeLLM([["a"], ["b"]]),
        settings=ChatSettings(relevance_filter=False, query_rewrite=False),
    )
    sid = h.run("q1")[0]["session_id"]
    frames = h.run("q2", session_id=sid)
    assert h.llm.chat_prompts == []
    assert ("status", "rewriting") not in stages(frames)


def test_history_respects_max_turns(store):
    h = Harness(
        store,
        llm=FakeLLM([["a"]]),
        settings=ChatSettings(history_max_turns=1, relevance_filter=False, query_rewrite=False),
    )
    sid = h.run("q1")[0]["session_id"]
    h.run("q2", session_id=sid)
    h.run("q3", session_id=sid)
    contents = [m.content for m in h.llm.stream_messages[2] if m.role == "user"]
    assert contents[-1] == "q3" and "q1" not in contents


def test_empty_retrieval_short_circuits_without_llm_and_without_stale_usage(store):
    llm = FakeLLM(usage={"total_tokens": 1})
    llm.last_usage = {"stale": True}
    h = Harness(store, llm=llm, retrievers={"hybrid": FakeRetriever([ChatRetrievalResult()])})
    frames = h.run()
    assert llm.stream_calls == 0
    assert [f["content"] for f in frames if f["type"] == "stream"] == [NOT_FOUND_MESSAGE]
    assert frames[-1]["grounded"] is True and "usage" not in frames[-1]
    assert next(f for f in frames if f["type"] == "sources")["citations"] == []


def test_max_context_chunks_limits_sources(store):
    h = Harness(
        store,
        retrievers={"hybrid": FakeRetriever([_hybrid_result(5)])},
        settings=ChatSettings(max_context_chunks=3),
    )
    frames = h.run()
    assert len(next(f for f in frames if f["type"] == "sources")["citations"]) == 3


# --------------------------------------------------------------------------- agentic

def test_agentic_answer_is_passed_through_as_single_stream_frame(store):
    llm = FakeLLM()
    r = FakeRetriever([_agentic_result()], stage="agentic_search")
    h = Harness(store, llm=llm, retrievers={"hybrid": FakeRetriever(), "agentic_fast": r})
    frames = h.run(retrieval="agentic_fast")

    assert types(frames) == ["session", "status", "search_complete", "sources", "status", "stream", "result"]
    assert llm.stream_calls == 0
    assert next(f for f in frames if f["type"] == "stream")["content"] == "sidecar 答案 [1]"
    assert next(f for f in frames if f["type"] == "sources")["citations"][0]["source"] == "a.md"
    assert frames[-1]["content"] == "sidecar 答案 [1]" and frames[-1]["grounded"] is True
    assert "usage" not in frames[-1]

    trace = h.persisted[0]
    assert trace.get_stage("chat_generation")["method"] == "sidecar_passthrough"
    assert trace.metadata["retrieval"] == "agentic_fast"
    assert store.get_messages(frames[0]["session_id"])[1]["metadata"]["retrieval"] == "agentic_fast"


def test_agentic_blank_answer_falls_back_to_answer_generator(store):
    blank = ChatRetrievalResult(results=[_rr(1)], answer="  ")
    h = Harness(store, retrievers={"agentic_fast": FakeRetriever([blank])}, settings=ChatSettings())
    frames = h.run(retrieval="agentic_fast")
    assert h.llm.stream_calls == 1 and frames[-1]["type"] == "result"


def test_default_retrieval_comes_from_settings(store):
    r = FakeRetriever([_agentic_result()])
    h = Harness(
        store,
        retrievers={"hybrid": FakeRetriever(), "agentic_deep": r},
        settings=ChatSettings(default_retrieval="agentic_deep"),
    )
    h.run()
    assert len(r.calls) == 1


# --------------------------------------------------------------------------- errors

@pytest.mark.parametrize(
    "exc,code",
    [
        (_llm_error(401), "AUTH"),
        (_llm_error(403), "AUTH"),
        (_llm_error(429), "RATE_LIMITED"),
        (_llm_error(500), "UPSTREAM"),
        (_llm_error(503), "UPSTREAM"),
        (_llm_error(None), "UPSTREAM"),
        (_llm_error(400), "INTERNAL"),
        (_llm_error(404), "INTERNAL"),
        (httpx.ConnectError("refused"), "UPSTREAM"),
        (httpx.ReadTimeout("slow"), "UPSTREAM"),
        (ConnectionError("reset"), "UPSTREAM"),
        (TimeoutError("t"), "UPSTREAM"),
        (AgenticSearchBackendError("down"), "UPSTREAM"),
        (AgenticSearchTimeoutError("slow"), "UPSTREAM"),
        (AgenticSearchResponseError("bad"), "UPSTREAM"),
        (DeepDisabledError("no"), "DEEP_DISABLED"),
        (ValueError("bug"), "INTERNAL"),
        (KeyError("bug"), "INTERNAL"),
    ],
)
def test_classify_error_matrix(exc, code):
    info = classify_error(exc)
    assert info.code == code
    assert any("\u4e00" <= ch <= "\u9fff" for ch in info.message)


def test_classify_httpx_status_errors():
    def status_error(code):
        req = httpx.Request("GET", "http://x")
        return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))

    assert classify_error(status_error(401)).code == "AUTH"
    assert classify_error(status_error(429)).code == "RATE_LIMITED"
    assert classify_error(status_error(502)).code == "UPSTREAM"
    assert classify_error(status_error(404)).code == "INTERNAL"


def test_internal_error_message_does_not_leak_details():
    assert "secret-path" not in classify_error(RuntimeError("secret-path")).message


@pytest.mark.parametrize(
    "exc,code,transient",
    [
        (_llm_error(401), "AUTH", False),
        (_llm_error(429), "RATE_LIMITED", True),
        (_llm_error(502), "UPSTREAM", True),
        (httpx.ConnectError("x"), "UPSTREAM", True),
        (ValueError("x"), "INTERNAL", False),
    ],
)
def test_retrieval_error_terminal_frame_and_retry_semantics(store, exc, code, transient):
    r = FakeRetriever([exc])
    h = Harness(store, retrievers={"hybrid": r})
    frames = h.run()

    assert frames[-1]["type"] == "error" and frames[-1]["code"] == code
    assert frames[-1]["message"]
    assert len(r.calls) == (2 if transient else 1)
    assert ("status", "retrying") in stages(frames) if transient else ("status", "retrying") not in stages(frames)
    assert h.sleeps == ([2.0] if transient else [])
    assert "stream" not in types(frames) and "result" not in types(frames)

    saved = store.get_messages(next(iter(store.list_sessions()))["session_id"])
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assert saved[1]["content"] == "" and saved[1]["metadata"]["error"]["code"] == code
    assert len(h.persisted) == 1
    assert h.persisted[0].get_stage("chat_generation")["error_code"] == code


def test_retrieval_retry_once_then_success(store):
    r = FakeRetriever([_llm_error(503), _hybrid_result()])
    h = Harness(store, retrievers={"hybrid": r})
    frames = h.run()
    assert frames[-1]["type"] == "result"
    assert types(frames).count("status") == 3
    assert stages(frames) == [("status", "retrieving"), ("status", "retrying"), ("status", "generating")]
    assert len(r.calls) == 2 and h.sleeps == [2.0]
    assert len(h.persisted) == 1
    assert r.calls[0]["trace"] is r.calls[1]["trace"] is h.persisted[0]


def test_retry_budget_follows_settings(store):
    r = FakeRetriever([_llm_error(503)])
    h = Harness(store, retrievers={"hybrid": r}, settings=ChatSettings(pipeline_max_retries=0))
    frames = h.run()
    assert frames[-1]["code"] == "UPSTREAM" and len(r.calls) == 1 and h.sleeps == []

    r2 = FakeRetriever([_llm_error(503)])
    h2 = Harness(store, retrievers={"hybrid": r2}, settings=ChatSettings(pipeline_max_retries=3))
    h2.run()
    assert len(r2.calls) == 4 and h2.sleeps == [2.0, 2.0, 2.0]


def test_deep_disabled_not_retried_not_degraded(store):
    r = FakeRetriever([DeepDisabledError("allow_deep=false")])
    h = Harness(
        store,
        retrievers={"agentic_deep": r},
        settings=ChatSettings(fallback_to_chat_only=True),
    )
    frames = h.run(retrieval="agentic_deep")
    assert frames[-1] == {"type": "error", "code": "DEEP_DISABLED", "message": frames[-1]["message"]}
    assert len(r.calls) == 1 and h.llm.stream_calls == 0 and h.sleeps == []


def test_generation_pre_stream_failure_retried_once(store):
    llm = FakeLLM([[_llm_error(429)], ["恢复了"]])
    h = Harness(store, llm=llm)
    frames = h.run()
    assert frames[-1]["type"] == "result" and frames[-1]["content"] == "恢复了"
    assert ("status", "retrying") in stages(frames)
    # 重试状态帧出现在首个 stream 帧之前
    assert types(frames).index("stream") > [i for i, f in enumerate(frames) if f.get("stage") == "retrying"][0]
    assert llm.stream_calls == 2 and h.sleeps == [2.0]
    assert len(h.retrievers["hybrid"].calls) == 1


def test_generation_pre_stream_failure_exhausted_emits_error(store):
    llm = FakeLLM([[_llm_error(503)]])
    h = Harness(store, llm=llm)
    frames = h.run()
    assert frames[-1]["code"] == "UPSTREAM" and llm.stream_calls == 2
    assert "stream" not in types(frames)


def test_generation_auth_error_not_retried(store):
    llm = FakeLLM([[_llm_error(401)]])
    h = Harness(store, llm=llm)
    frames = h.run()
    assert frames[-1]["code"] == "AUTH" and llm.stream_calls == 1 and h.sleeps == []


def test_no_retry_after_first_stream_frame_and_partial_saved(store):
    llm = FakeLLM([["前半", _llm_error(503)], ["不应出现"]])
    h = Harness(store, llm=llm)
    frames = h.run()

    assert types(frames)[-2:] == ["stream", "error"]
    assert frames[-1]["code"] == "UPSTREAM"
    assert llm.stream_calls == 1 and h.sleeps == []
    assert ("status", "retrying") not in stages(frames)
    assert [f["content"] for f in frames if f["type"] == "stream"] == ["前半"]

    sid = frames[0]["session_id"]
    assistant = store.get_messages(sid)[1]
    assert assistant["content"] == "前半"
    assert assistant["metadata"]["cancelled"] is True
    assert assistant["metadata"]["error"]["code"] == "UPSTREAM"
    assert len(h.persisted) == 1


def test_unexpected_exception_becomes_internal_error_frame(store):
    h = Harness(store, llm=FakeLLM([[RuntimeError("secret")]]))
    frames = h.run()
    assert frames[-1]["code"] == "INTERNAL" and "secret" not in frames[-1]["message"]


# --------------------------------------------------------------------------- fallback (D6)

def test_fallback_disabled_emits_error_and_no_chat_only_answer(store):
    h = Harness(store, retrievers={"hybrid": FakeRetriever([AgenticSearchBackendError("down")])})
    frames = h.run()
    assert frames[-1]["type"] == "error"
    assert h.llm.stream_calls == 0


def test_fallback_enabled_degrades_to_ungrounded_chat(store):
    llm = FakeLLM([["纯聊天回答"]])
    r = FakeRetriever([_llm_error(503)])
    h = Harness(store, llm=llm, retrievers={"hybrid": r}, settings=ChatSettings(fallback_to_chat_only=True))
    frames = h.run()

    assert types(frames) == [
        "session", "status", "status", "search_error", "status", "stream", "result",
    ]
    assert "sources" not in types(frames) and "search_complete" not in types(frames)
    result = frames[-1]
    assert result["grounded"] is False and result["content"] == "纯聊天回答"
    assert len(r.calls) == 2  # 重试耗尽后才降级
    assert llm.stream_messages[0][0].role == "system"
    assert llm.stream_messages[0][-1].content == "什么是 RAG？"

    saved = store.get_messages(result["session_id"])[1]
    assert saved["metadata"]["grounded"] is False and saved["sources"] == []
    trace = h.persisted[0]
    assert trace.get_stage("chat_generation")["fallback"] is True
    assert trace.get_stage("chat_generation")["method"] == "pure_chat"


def test_fallback_pure_chat_failure_still_reports_error(store):
    llm = FakeLLM([[_llm_error(401)]])
    h = Harness(
        store, llm=llm,
        retrievers={"hybrid": FakeRetriever([ValueError("x")])},
        settings=ChatSettings(fallback_to_chat_only=True),
    )
    frames = h.run()
    assert frames[-1]["code"] == "AUTH"


# --------------------------------------------------------------------------- cancellation

def test_closing_generator_mid_stream_saves_partial_with_cancelled_flag(store):
    llm = FakeLLM([["第一段", "第二段", "第三段"]])
    h = Harness(store, llm=llm)
    gen = h.engine.run_turn("你好")
    seen = []
    for frame in gen:
        seen.append(frame)
        if frame["type"] == "stream":
            break
    gen.close()

    sid = seen[0]["session_id"]
    msgs = store.get_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "第一段"
    assert msgs[1]["metadata"]["cancelled"] is True
    assert store.get_session(sid)["message_count"] == 2
    assert len(h.persisted) == 1
    assert h.persisted[0].get_stage("chat_generation")["cancelled"] is True


def test_cancel_event_stops_stream_and_returns_cancelled_result(store):
    llm = FakeLLM([["a", "b", "c"]])
    h = Harness(store, llm=llm)
    event = threading.Event()
    frames = []
    for frame in h.engine.run_turn("hi", cancel_event=event):
        frames.append(frame)
        if frame["type"] == "stream":
            event.set()
    assert [f["content"] for f in frames if f["type"] == "stream"] == ["a"]
    assert frames[-1]["type"] == "result" and frames[-1]["cancelled"] is True
    assert frames[-1]["content"] == "a"
    saved = store.get_messages(frames[0]["session_id"])[1]
    assert saved["content"] == "a" and saved["metadata"]["cancelled"] is True
    assert len(h.persisted) == 1


def test_cancel_before_retrieval_completes_skips_generation(store):
    event = threading.Event()
    r = FakeRetriever()
    orig = r.retrieve

    def retrieve(*a, **kw):
        event.set()
        return orig(*a, **kw)

    r.retrieve = retrieve  # type: ignore[method-assign]
    h = Harness(store, retrievers={"hybrid": r})
    frames = h.run(cancel_event=event)
    assert frames[-1]["type"] == "result" and frames[-1]["cancelled"] is True
    assert h.llm.stream_calls == 0
    assert len(h.persisted) == 1


def test_closing_generator_after_completion_does_not_double_persist(store):
    h = Harness(store)
    gen = h.engine.run_turn("hi")
    list(gen)
    gen.close()
    assert len(h.persisted) == 1
    assert len(store.get_session(next(iter(store.list_sessions()))["session_id"])["messages"]) == 2


# --------------------------------------------------------------------------- trace (D7)

@pytest.mark.parametrize("mode", ["hybrid", "agentic_fast", "agentic_deep"])
def test_exactly_one_trace_per_turn(store, mode):
    plan = _hybrid_result() if mode == "hybrid" else _agentic_result()
    r = FakeRetriever([plan], stage="dense_retrieval" if mode == "hybrid" else "agentic_search")
    h = Harness(store, retrievers={mode: r})
    sid = h.run(retrieval=mode)[0]["session_id"]
    assert len(h.persisted) == 1
    trace = h.persisted[0]
    assert trace.trace_type == "query"
    assert trace.metadata["routes"] == "chat"
    assert trace.metadata["retrieval"] == mode
    assert trace.metadata["session_id"] == sid
    assert trace.metadata["query"] == "什么是 RAG？"
    assert trace.finished_at is not None
    names = [s["stage"] for s in trace.stages]
    assert names[:2] == ["chat_history", "chat_rewrite"]
    assert names[-1] == "chat_generation"
    assert names[2] == ("dense_retrieval" if mode == "hybrid" else "agentic_search")
    assert r.calls[0]["trace"] is trace

    h.run("第二轮", session_id=sid, retrieval=mode)
    assert len(h.persisted) == 2 and h.persisted[0] is not h.persisted[1]


def test_result_trace_id_matches_persisted_trace(store):
    h = Harness(store)
    result = h.run()[-1]
    assert result["trace_id"] == h.persisted[0].trace_id


def test_trace_factory_is_used_and_persist_failure_is_tolerated(store):
    from core.trace.trace_context import TraceContext

    made: list[dict] = []

    def factory(meta):
        made.append(meta)
        return TraceContext(trace_type="query", metadata=meta, trace_id="fixed-id")

    def boom(_trace):
        raise OSError("disk full")

    engine = ChatEngine(
        retrievers={"hybrid": FakeRetriever()},
        llm=FakeLLM(),
        session_store=store,
        trace_factory=factory,
        persist_trace=boom,
    )
    frames = list(engine.run_turn("hi"))
    assert frames[-1]["type"] == "result" and frames[-1]["trace_id"] == "fixed-id"
    assert made[0]["routes"] == "chat"


def test_engine_runs_without_persist_callable(store):
    engine = ChatEngine(retrievers={"hybrid": FakeRetriever()}, llm=FakeLLM(), session_store=store)
    assert list(engine.run_turn("hi"))[-1]["type"] == "result"


# --------------------------------------------------------------------------- validation

def test_run_turn_validates_eagerly(store):
    h = Harness(store)
    with pytest.raises(ValueError):
        h.engine.run_turn("   ")
    with pytest.raises(ValueError):
        h.engine.run_turn("hi", retrieval="nope")
    with pytest.raises(ValueError):
        h.engine.run_turn("hi", retrieval="agentic_fast")  # 未注册
    with pytest.raises(ValueError):
        h.engine.run_turn("hi", top_k=0)
    with pytest.raises(SessionNotFoundError):
        h.engine.run_turn("hi", session_id="ghost")
    assert store.list_sessions() == []


def test_constructor_rejects_unknown_retrieval_keys(store):
    with pytest.raises(ValueError):
        ChatEngine(retrievers={"web": FakeRetriever()}, llm=FakeLLM(), session_store=store)


def test_available_retrievals_and_protocol(store):
    h = Harness(store, retrievers={"agentic_fast": FakeRetriever(), "hybrid": FakeRetriever()})
    assert h.engine.available_retrievals == ("hybrid", "agentic_fast")
    assert isinstance(FakeRetriever(), ChatRetriever)
