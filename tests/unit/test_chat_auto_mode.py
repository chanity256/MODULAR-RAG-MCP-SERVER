"""MR3: ChatEngine auto 分支 + 装配注入（MODE_ROUTER_SPEC §4 引擎组与装配组）。全部离线。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api.chat_wiring import ChatServices
from core.chat.engine import ChatEngine
from core.chat.retriever import (
    RETRIEVAL_AGENTIC_DEEP,
    RETRIEVAL_AGENTIC_FAST,
    RETRIEVAL_HYBRID,
    ChatRetrievalResult,
    RoutingDecision,
)
from core.chat.session_store import SessionStore
from core.settings import ChatSettings, load_settings
from core.types import RetrievalResult
from libs.llm.base_llm import ChatResponse
from libs.mode_router import ModeRouterError
from libs.mode_router.system_one_router import CloudflareWorkersAIRouter

_MINIMAL_YAML = """\
llm:
  provider: azure
  model: gpt-4o
embedding:
  provider: openai
  model: text-embedding-3-small
vision_llm:
  provider: azure
  model: gpt-4o
vector_store:
  backend: chroma
retrieval:
  sparse_backend: bm25
  fusion_algorithm: rrf
rerank:
  backend: none
evaluation:
  backends: [custom]
"""

ALL_MODES = (RETRIEVAL_HYBRID, RETRIEVAL_AGENTIC_FAST, RETRIEVAL_AGENTIC_DEEP)


# --------------------------------------------------------------------------- fakes

class FakeLLM:
    def __init__(self):
        self.stream_messages: list[list[Any]] = []
        self.last_usage = None

    def chat(self, messages, *, trace=None, **kwargs):
        prompt = messages[-1].content
        reply = "[1]" if "Return the numbers" in prompt else "改写后的查询"
        return ChatResponse(content=reply)

    def chat_stream(self, messages, *, trace=None, **kwargs):
        self.stream_messages.append(list(messages))
        yield "答案 [1]"


class FakeRetriever:
    def __init__(self, name: str):
        self.name = name
        self.calls: list[dict[str, Any]] = []

    def retrieve(self, query, *, collection=None, top_k=None, trace=None, on_log=None):
        self.calls.append({"query": query})
        rr = RetrievalResult(chunk_id="c1", score=0.9, text="文本 [1]", metadata={"source_path": "d.md", "page": 1})
        return ChatRetrievalResult(results=[rr])


class FakeRouter:
    """按 plan 决策：RoutingDecision / 异常 / callable。"""

    backend_name = "fake"
    model = "fake-model"
    last_input_tokens = 123

    def __init__(self, plan: Any = None):
        self.plan = plan
        self.calls: list[dict[str, Any]] = []

    def decide(self, query: str, candidates: tuple[str, ...]) -> RoutingDecision:
        self.calls.append({"query": query, "candidates": candidates})
        if isinstance(self.plan, Exception):
            raise self.plan
        if callable(self.plan):
            return self.plan(query, candidates)
        return self.plan


def _hybrid_result():
    rr = RetrievalResult(chunk_id="c1", score=0.9, text="文本 [1]", metadata={"source_path": "d.md", "page": 1})
    from core.chat.retriever import ChatRetrievalResult

    return ChatRetrievalResult(results=[rr])


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "chat.db")
    yield s
    s.close()


class Harness:
    def __init__(self, store, *, llm=None, retrievers=None, router=None, candidates=ALL_MODES,
                 fallback=RETRIEVAL_HYBRID, settings=None):
        self.llm = llm or FakeLLM()
        self.retrievers = retrievers if retrievers is not None else {m: FakeRetriever(m) for m in ALL_MODES}
        self.persisted: list[Any] = []
        self.engine = ChatEngine(
            retrievers=self.retrievers,
            llm=self.llm,
            session_store=store,
            settings=settings or ChatSettings(),
            persist_trace=self.persisted.append,
            sleep=lambda _s: None,
            mode_router=router,
            auto_candidates=candidates,
            auto_fallback=fallback,
        )

    def run(self, message="什么是 RAG？", **kwargs):
        return list(self.engine.run_turn(message, **kwargs))


def _stage(trace, name):
    return next(s for s in trace.stages if s["stage"] == name)


# --------------------------------------------------------------------------- engine group

def test_auto_routes_to_selected_retriever(store):
    router = FakeRouter(RoutingDecision(mode=RETRIEVAL_AGENTIC_DEEP, confidence=0.8, probabilities={"agentic_deep": 0.8}))
    h = Harness(store, router=router)
    frames = h.run(retrieval="auto")

    assert h.retrievers[RETRIEVAL_AGENTIC_DEEP].calls, "deep retriever should be used"
    assert not h.retrievers[RETRIEVAL_HYBRID].calls
    assert [(f["type"], f.get("stage")) for f in frames if f["type"] == "status"] == [
        ("status", "routing"), ("status", "retrieving"), ("status", "generating"),
    ]
    result = frames[-1]
    assert result["type"] == "result" and result["retrieval"] == RETRIEVAL_AGENTIC_DEEP
    assert result["routing"]["decided"] == RETRIEVAL_AGENTIC_DEEP
    assert result["routing"]["fallback_reason"] is None

    trace = h.persisted[0]
    stage = _stage(trace, "mode_routing")
    assert stage["requested"] == "auto"
    assert stage["decided"] == RETRIEVAL_AGENTIC_DEEP
    assert stage["candidates"] == list(ALL_MODES)
    assert stage["backend"] == "fake" and stage["model"] == "fake-model"
    assert stage["confidence"] == pytest.approx(0.8)
    assert stage["input_tokens"] == 123
    assert stage["fallback_reason"] is None
    assert "elapsed_ms" in stage
    assert "api_token" not in stage and "account_id" not in stage
    assert trace.metadata["retrieval"] == RETRIEVAL_AGENTIC_DEEP


def test_decision_uses_rewritten_query(store):
    router = FakeRouter(RoutingDecision(mode=RETRIEVAL_HYBRID))
    h = Harness(store, router=router)
    sid = h.run("第一问", retrieval="auto")[0]["session_id"]  # 首轮无历史，不改写
    h.run("第二问", session_id=sid, retrieval="auto")
    assert len(router.calls) == 2
    assert router.calls[0]["query"] == "第一问"
    assert router.calls[1]["query"] == "改写后的查询"


@pytest.mark.parametrize(
    "reason", ["timeout", "unreachable", "invalid_output", "low_confidence", "error"]
)
def test_router_error_falls_back(store, reason):
    router = FakeRouter(ModeRouterError(reason, "boom"))
    h = Harness(store, router=router)
    frames = h.run(retrieval="auto")

    result = frames[-1]
    assert result["retrieval"] == RETRIEVAL_HYBRID
    assert result["routing"]["decided"] is None
    assert result["routing"]["fallback_reason"] == reason
    assert _stage(h.persisted[0], "mode_routing")["fallback_reason"] == reason
    assert h.retrievers[RETRIEVAL_HYBRID].calls, "fallback mode retrieves normally"


def test_unexpected_router_exception_maps_to_error(store):
    router = FakeRouter(RuntimeError("bug"))
    h = Harness(store, router=router)
    frames = h.run(retrieval="auto")
    assert frames[-1]["routing"]["fallback_reason"] == "error"


def test_out_of_candidates_decision_falls_back(store):
    # 候选只给两档，路由器却返回 deep → 引擎复检拦截（不信任实现）。
    router = FakeRouter(RoutingDecision(mode=RETRIEVAL_AGENTIC_DEEP, confidence=0.99))
    h = Harness(store, router=router, candidates=(RETRIEVAL_HYBRID, RETRIEVAL_AGENTIC_FAST))
    frames = h.run(retrieval="auto")
    assert frames[-1]["retrieval"] == RETRIEVAL_HYBRID
    assert frames[-1]["routing"]["fallback_reason"] == "invalid_output"


def test_candidates_passed_to_router_verbatim(store):
    candidates = (RETRIEVAL_HYBRID, RETRIEVAL_AGENTIC_FAST)
    router = FakeRouter(RoutingDecision(mode=RETRIEVAL_HYBRID))
    h = Harness(store, router=router, candidates=candidates)
    h.run(retrieval="auto")
    assert router.calls[0]["candidates"] == candidates


def test_non_auto_turn_has_no_routing_object(store):
    h = Harness(store, router=FakeRouter(RoutingDecision(mode=RETRIEVAL_HYBRID)))
    frames = h.run(retrieval=RETRIEVAL_HYBRID)
    result = frames[-1]
    assert "routing" not in result
    assert result["retrieval"] == RETRIEVAL_HYBRID
    assert all(f.get("stage") != "routing" for f in frames if f["type"] == "status")
    assert all(s["stage"] != "mode_routing" for s in h.persisted[0].stages)


def test_auto_without_router_raises(store):
    h = Harness(store, router=None)
    with pytest.raises(ValueError, match="auto"):
        h.run(retrieval="auto")


def test_default_retrieval_auto_without_router_raises(store):
    h = Harness(store, router=None, settings=ChatSettings(default_retrieval="auto"))
    with pytest.raises(ValueError, match="auto"):
        h.run()


def test_auto_fallback_must_be_in_candidates():
    with pytest.raises(ValueError, match="auto_fallback"):
        ChatEngine(
            retrievers={RETRIEVAL_HYBRID: FakeRetriever(RETRIEVAL_HYBRID)},
            llm=FakeLLM(),
            session_store=SessionStore(":memory:"),
            mode_router=FakeRouter(),
            auto_candidates=(RETRIEVAL_HYBRID,),
            auto_fallback=RETRIEVAL_AGENTIC_FAST,
        )


# --------------------------------------------------------------------------- assembly group

def _services(tmp_path, *, agentic: str, retrievers=None):
    yaml = (
        _MINIMAL_YAML
        + f"agentic_search:\n  enabled: {agentic == 'full' or agentic == 'no_deep'}\n"
        + "  allowed_roots: ['./data/documents']\n"
        + ("  allow_deep: true\n" if agentic == "full" else "")
        + "chat:\n  session_db: "
        + str(tmp_path / "chat.db")
        + "\n"
    )
    settings = load_settings(_write(tmp_path, yaml))
    return ChatServices(
        settings,
        session_store=SessionStore(tmp_path / "chat.db"),
        retrievers=retrievers if retrievers is not None else {m: FakeRetriever(m) for m in ALL_MODES},
        llm_factory=lambda s: FakeLLM(),
    )


def _write(tmp_path: Path, yaml: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml, encoding="utf-8")
    return path


def test_candidates_exclude_deep_when_allow_deep_false(tmp_path):
    services = _services(tmp_path, agentic="no_deep")
    engine = services.new_engine()
    assert engine._auto_candidates == (RETRIEVAL_HYBRID, RETRIEVAL_AGENTIC_FAST)


def test_candidates_include_deep_when_allow_deep_true(tmp_path):
    services = _services(tmp_path, agentic="full")
    engine = services.new_engine()
    assert engine._auto_candidates == ALL_MODES


def test_candidates_hybrid_only_when_agentic_disabled(tmp_path):
    services = _services(tmp_path, agentic="off")
    engine = services.new_engine()
    assert engine._auto_candidates == (RETRIEVAL_HYBRID,)


def test_router_none_when_disabled(tmp_path):
    services = _services(tmp_path, agentic="full")
    engine = services.new_engine()
    assert engine._mode_router is None
    with pytest.raises(ValueError, match="auto"):
        engine.run_turn("q", retrieval="auto")


def test_router_shared_across_engines(tmp_path):
    yaml = (
        _MINIMAL_YAML
        + "agentic_search:\n  enabled: true\n  allowed_roots: ['./data/documents']\n"
        + "mode_router:\n  enabled: true\n  account_id: acct\n  api_token: tok\n"
        + "chat:\n  session_db: " + str(tmp_path / "chat.db") + "\n"
    )
    settings = load_settings(_write(tmp_path, yaml))
    services = ChatServices(
        settings,
        session_store=SessionStore(tmp_path / "chat.db"),
        retrievers={m: FakeRetriever(m) for m in ALL_MODES},
        llm_factory=lambda s: FakeLLM(),
    )
    e1, e2 = services.new_engine(), services.new_engine()
    assert e1._mode_router is not None
    assert e1._mode_router is e2._mode_router  # 跨轮共享，连接复用


# --------------------------------------------------------------------------- capabilities group (MR4)

def test_capabilities_auto_routing_false_when_router_disabled(tmp_path):
    services = _services(tmp_path, agentic="full")
    caps = services.capabilities()
    assert caps["auto_routing"] is False
    assert "auto" not in caps["retrievals"]  # retrievals 只含三档真实模式


def test_capabilities_auto_routing_true_when_router_enabled(tmp_path):
    yaml = (
        _MINIMAL_YAML
        + "agentic_search:\n  enabled: true\n  allowed_roots: ['./data/documents']\n"
        + "mode_router:\n  enabled: true\n  account_id: acct\n  api_token: tok\n"
        + "chat:\n  session_db: " + str(tmp_path / "chat.db") + "\n"
    )
    settings = load_settings(_write(tmp_path, yaml))
    services = ChatServices(
        settings,
        session_store=SessionStore(tmp_path / "chat.db"),
        retrievers={m: FakeRetriever(m) for m in ALL_MODES},
        llm_factory=lambda s: FakeLLM(),
    )
    caps = services.capabilities()
    assert caps["auto_routing"] is True
    assert caps["retrievals"] == list(ALL_MODES)


def test_capabilities_auto_routing_false_when_candidates_empty(tmp_path):
    # 路由器启用但注入的检索器为空 → 候选为空，不上报 auto_routing。
    yaml = (
        _MINIMAL_YAML
        + "mode_router:\n  enabled: true\n  account_id: acct\n  api_token: tok\n"
        + "chat:\n  session_db: " + str(tmp_path / "chat.db") + "\n"
    )
    settings = load_settings(_write(tmp_path, yaml))
    services = ChatServices(
        settings,
        session_store=SessionStore(tmp_path / "chat.db"),
        retrievers={},
        llm_factory=lambda s: FakeLLM(),
    )
    assert services.capabilities()["auto_routing"] is False


def test_compute_capabilities_auto_routing_defaults_off():
    from api.chat_wiring import compute_capabilities

    assert compute_capabilities(None)["auto_routing"] is False


# --------------------------------------------------------------------------- MR5 group: unreachable base_url fallback

def _closed_loopback_port() -> int:
    """占用后立即释放的 loopback 端口：连接被立即拒绝，零真实网络（loopback 白名单内）。"""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _unreachable_router() -> "CloudflareWorkersAIRouter":
    return CloudflareWorkersAIRouter(
        account_id="acct",
        api_token="tok",
        model="clef-flash",
        base_url=f"http://127.0.0.1:{_closed_loopback_port()}",
        timeout_s=1.0,
    )


def test_real_router_unreachable_base_url_raises_unreachable():
    """MR5：真实 httpx 客户端 + 不可达 base_url → ModeRouterError(unreachable)。

    门控真实冒烟（RAG_MODE_ROUTER_INTEGRATION=1）的离线等价：同一后端实现，
    只把端点换成不可达地址，不触外网。
    """
    router = _unreachable_router()
    try:
        with pytest.raises(ModeRouterError) as excinfo:
            router.decide("什么是 RAG？", ALL_MODES)
        assert excinfo.value.reason == "unreachable"
    finally:
        router.close()


def test_unreachable_base_url_auto_turn_falls_back_with_trace(store):
    """MR5：不可达端点的 auto 轮次回退 fallback_retrieval，result 帧与 trace 均记 unreachable。"""
    router = _unreachable_router()
    try:
        h = Harness(store, router=router)
        frames = h.run(retrieval="auto")
    finally:
        router.close()

    result = frames[-1]
    assert result["type"] == "result"
    assert result["retrieval"] == RETRIEVAL_HYBRID
    assert result["routing"]["decided"] is None
    assert result["routing"]["fallback_reason"] == "unreachable"
    assert h.retrievers[RETRIEVAL_HYBRID].calls, "fallback mode retrieves normally"

    stage = _stage(h.persisted[0], "mode_routing")
    assert stage["requested"] == "auto"
    assert stage["fallback_reason"] == "unreachable"
    assert stage["backend"] == "cloudflare_workers_ai"
    assert stage["model"] == "clef-flash"
    assert "api_token" not in stage and "account_id" not in stage
