"""WC7.1 / WC7.2 / WC7.3 / WC7.4: Chat WS 端点、会话 REST、capabilities 与端到端 trace（全部离线）。

WS 用 Starlette ``TestClient``；引擎要么是可控的 fake（取消/断开/限流），要么是真实
``ChatEngine`` + fake LLM / fake 检索后端（帧序与 trace 落盘）。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from api.chat_wiring import ChatServices
from api.deps import ServiceContainer
from core.chat import ChatEngine, SessionNotFoundError, SessionStore
from core.settings import ApiSettings, ChatSettings, load_settings
from core.types import RetrievalResult
from libs.agentic_search.base_agentic_search import AgenticEvidence
from libs.agentic_search.fake_backend import FakeAgenticSearch
from libs.llm.base_llm import BaseLLM, ChatResponse
from mcp_server.tools.agentic_search import AgenticSearchTool
from observability.dashboard.services.chat_retrievers import build_chat_retrievers
from observability.dashboard.services.config_service import ConfigService
from observability.dashboard.services.trace_service import TraceService

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
TOKEN = "ws-test-token"
HYBRID_FRAME = {"type": "user_message", "message": "什么是 RAG？"}


# --------------------------------------------------------------------------- fakes
class FakeLLM(BaseLLM):
    def __init__(self, chunks=("答案", " [1]")):
        self.chunks = list(chunks)
        self.last_usage = None

    def chat(self, messages, *, trace=None, **kwargs):
        return ChatResponse(content="[1]")

    def chat_stream(self, messages, *, trace=None, **kwargs):
        self.last_usage = None
        yield from self.chunks
        self.last_usage = {"total_tokens": 3}


class FakeQueryService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def search(self, query, *, collection=None, top_k=None, trace=None):
        self.calls.append({"query": query, "collection": collection, "top_k": top_k})
        if trace is not None:
            trace.record_stage("dense_retrieval", count=1)
            trace.finish()  # 与 HybridSearch.search 一致：会提前 finish 注入的 trace
        return [RetrievalResult("c1", 0.9, "RAG 是检索增强生成", {"source_path": "doc.md", "page": 1})]


class ScriptedEngine:
    """可控 fake 引擎：先发若干帧，再按 ``hold`` 阻塞，直到取消/放行。"""

    def __init__(self, *, hold: bool = False, frames=None):
        self.hold = hold
        self.frames = frames or [{"type": "session", "session_id": "s1"}, {"type": "stream", "content": "a"}]
        self.calls: list[dict[str, Any]] = []
        self.cancel_event: threading.Event | None = None
        self.release = threading.Event()
        self.closed = threading.Event()
        self.saw_cancel = threading.Event()
        self.available_retrievals = ("hybrid",)

    def run_turn(self, message, *, session_id=None, retrieval=None, collection=None, top_k=None, cancel_event=None):
        self.calls.append(
            {"message": message, "session_id": session_id, "retrieval": retrieval, "collection": collection,
             "top_k": top_k}
        )
        self.cancel_event = cancel_event
        return self._gen(cancel_event)

    def _gen(self, cancel_event):
        try:
            yield from self.frames
            if self.hold:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not self.release.is_set():
                    if cancel_event is not None and cancel_event.is_set():
                        self.saw_cancel.set()
                        yield {"type": "result", "content": "a", "session_id": "s1", "cancelled": True}
                        return
                    time.sleep(0.01)
            yield {"type": "result", "content": "a", "session_id": "s1"}
        finally:
            self.closed.set()


class RejectingEngine:
    def __init__(self, exc):
        self.exc = exc

    def run_turn(self, *a, **k):
        raise self.exc


# --------------------------------------------------------------------------- helpers
@pytest.fixture(scope="module")
def base_settings():
    loaded = load_settings(_REPO_CONFIG)
    return replace(loaded, agentic_search=replace(loaded.agentic_search, enabled=False))


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "chat.db")
    yield s
    s.close()


def make_client(settings, chat: ChatServices, *, api: ApiSettings | None = None) -> TestClient:
    container = ServiceContainer(
        settings,
        config_service=ConfigService(settings),
        trace_service=TraceService("/nonexistent/t.jsonl"),
        chat_services=chat,
    )
    return TestClient(create_app(api_settings=api or ApiSettings(), services=container), raise_server_exceptions=False)


def real_engine_services(settings, store, *, llm=None, retrievers=None, chat_settings=None, log_file=None):
    """真实 ChatEngine + fake LLM；``persist_trace`` 指向 tmp 日志，与生产装配同路径。"""
    cs = chat_settings or ChatSettings()
    st = replace(settings, chat=cs)
    if log_file is not None:
        st = replace(st, observability=replace(st.observability, log_file=str(log_file)))
    return ChatServices(
        st,
        session_store=store,
        retrievers=retrievers or {"hybrid": _hybrid()},
        llm_factory=lambda _s: llm or FakeLLM(),
    ), st


def _hybrid():
    from observability.dashboard.services.chat_retrievers import HybridChatRetriever

    return HybridChatRetriever(FakeQueryService())


def receive_until_terminal(ws) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("result", "error"):
            return frames


def assert_closed(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as info:
        ws.receive_json()
    return info.value.code


# --------------------------------------------------------------------------- WC7.1: frames
class TestFrames:
    def test_hybrid_frame_order_session_status_stream_result(self, base_settings, store):
        chat, _ = real_engine_services(base_settings, store)
        client = make_client(base_settings, chat)

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json({**HYBRID_FRAME, "collection": "docs", "top_k": 3})
            frames = receive_until_terminal(ws)
            assert assert_closed(ws) == 1000

        assert [f["type"] for f in frames] == [
            "session", "status", "search_complete", "sources", "status", "stream", "stream", "result",
        ]
        assert [f.get("stage") for f in frames if f["type"] == "status"] == ["retrieving", "generating"]
        sources = next(f for f in frames if f["type"] == "sources")
        assert sources["citations"][0]["id"] == 1 and sources["citations"][0]["source"] == "doc.md"
        result = frames[-1]
        assert result["content"] == "答案 [1]" and result["grounded"] is True
        assert result["session_id"] == frames[0]["session_id"] and result["trace_id"]
        assert result["usage"] == {"total_tokens": 3}

        stored = store.get_session(frames[0]["session_id"])
        assert [m["role"] for m in stored["messages"]] == ["user", "assistant"]

    def test_continuing_an_existing_session(self, base_settings, store):
        session = store.create_session(title="old")
        chat, _ = real_engine_services(base_settings, store)
        client = make_client(base_settings, chat)

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json({**HYBRID_FRAME, "session_id": session["session_id"]})
            frames = receive_until_terminal(ws)

        assert frames[0]["type"] == "status"  # 已有会话不再下发 session 帧
        assert frames[-1]["session_id"] == session["session_id"]

    def test_fake_engine_receives_validated_arguments(self, base_settings):
        engine = ScriptedEngine()
        client = make_client(base_settings, ChatServices(base_settings, engine=engine))

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json(
                {"type": "user_message", "message": "hi", "session_id": "s9", "retrieval": "hybrid",
                 "collection": "c", "top_k": 2}
            )
            receive_until_terminal(ws)

        assert engine.calls == [
            {"message": "hi", "session_id": "s9", "retrieval": "hybrid", "collection": "c", "top_k": 2}
        ]

    @pytest.mark.parametrize(
        "payload",
        [
            {"type": "user_message", "message": "x", "unknown": 1},
            {"type": "user_message", "message": "x", "sessionId": "typo"},
            {"type": "user_message"},
            {"type": "user_message", "message": ""},
            {"type": "user_message", "message": 5},
            {"type": "user_message", "message": "x", "retrieval": "turbo"},
            {"type": "user_message", "message": "x", "top_k": 0},
            {"type": "user_message", "message": "x", "top_k": True},
            {"type": "user_message", "message": "x", "top_k": "3"},
            {"type": "cancel", "extra": 1},
            {"type": "nope"},
            {"message": "no type"},
            [1, 2],
        ],
    )
    def test_invalid_frames_get_bad_request_error_then_close(self, base_settings, payload):
        engine = ScriptedEngine()
        client = make_client(base_settings, ChatServices(base_settings, engine=engine))

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json(payload)
            frame = ws.receive_json()
            assert frame["type"] == "error" and frame["code"] == "BAD_REQUEST"
            assert frame["message"].startswith("Invalid frame")
            assert assert_closed(ws) == 1008
        assert engine.calls == []

    def test_non_json_and_binary_frames_rejected(self, base_settings):
        client = make_client(base_settings, ChatServices(base_settings, engine=ScriptedEngine()))
        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_text("not json")
            assert ws.receive_json()["code"] == "BAD_REQUEST"
        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_bytes(b"\x00\x01")
            assert ws.receive_json()["code"] == "BAD_REQUEST"

    def test_cancel_as_first_frame_just_closes(self, base_settings):
        engine = ScriptedEngine()
        client = make_client(base_settings, ChatServices(base_settings, engine=engine))
        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json({"type": "cancel"})
            assert assert_closed(ws) == 1000
        assert engine.calls == []

    def test_unknown_session_and_unavailable_retrieval_are_bad_request(self, base_settings, store):
        chat, _ = real_engine_services(base_settings, store)
        client = make_client(base_settings, chat)
        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json({**HYBRID_FRAME, "session_id": "ghost"})
            frame = ws.receive_json()
            assert frame["code"] == "BAD_REQUEST" and "ghost" in frame["message"]
            assert assert_closed(ws) == 1000
        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json({**HYBRID_FRAME, "retrieval": "agentic_fast"})
            frame = ws.receive_json()
            assert frame["code"] == "BAD_REQUEST" and "not available" in frame["message"]

    def test_engine_value_error_and_session_error_mapping(self, base_settings):
        for exc, needle in ((ValueError("bad top_k"), "bad top_k"), (SessionNotFoundError("sx"), "sx")):
            client = make_client(base_settings, ChatServices(base_settings, engine=RejectingEngine(exc)))
            with client.websocket_connect("/api/v1/chat") as ws:
                ws.send_json(HYBRID_FRAME)
                frame = ws.receive_json()
                assert frame["code"] == "BAD_REQUEST" and needle in frame["message"]

    def test_engine_build_failure_is_internal_without_details(self, base_settings):
        def boom():
            raise RuntimeError("secret provider config")

        client = make_client(base_settings, ChatServices(base_settings, engine_factory=boom))
        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json(HYBRID_FRAME)
            frame = ws.receive_json()
            assert frame["code"] == "INTERNAL" and "secret" not in frame["message"]

    def test_engine_crash_mid_turn_becomes_internal_error_frame(self, base_settings):
        class Crashing:
            def run_turn(self, *a, **k):
                def gen():
                    yield {"type": "status", "stage": "retrieving"}
                    raise RuntimeError("transport-level surprise")

                return gen()

        client = make_client(base_settings, ChatServices(base_settings, engine=Crashing()))
        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json(HYBRID_FRAME)
            frames = receive_until_terminal(ws)
        assert frames[-1] == {"type": "error", "code": "INTERNAL", "message": "Internal server error"}

    def test_chat_disabled_reports_unavailable(self, base_settings):
        st = replace(base_settings, chat=ChatSettings(enabled=False))
        client = make_client(st, ChatServices(st, engine=ScriptedEngine()))
        with client.websocket_connect("/api/v1/chat") as ws:
            frame = ws.receive_json()
            assert frame["code"] == "UNAVAILABLE"
            assert assert_closed(ws) == 1008


# --------------------------------------------------------------------------- auth / origin
class TestWsAuth:
    def _client(self, settings, token):
        api = ApiSettings(api_token=token)
        return make_client(settings, ChatServices(settings, engine=ScriptedEngine()), api=api)

    def test_open_when_no_token_configured(self, base_settings):
        with self._client(base_settings, None).websocket_connect("/api/v1/chat") as ws:
            ws.send_json(HYBRID_FRAME)
            assert receive_until_terminal(ws)[-1]["type"] == "result"

    @pytest.mark.parametrize("placeholder", ["", "   ", "${RAG_API_TOKEN}"])
    def test_blank_or_unresolved_placeholder_token_means_open(self, base_settings, placeholder):
        with self._client(base_settings, placeholder).websocket_connect("/api/v1/chat") as ws:
            ws.send_json(HYBRID_FRAME)
            assert receive_until_terminal(ws)[-1]["type"] == "result"

    def test_literal_placeholder_is_not_a_valid_credential_when_real_token_set(self, base_settings):
        client = self._client(base_settings, TOKEN)
        with client.websocket_connect("/api/v1/chat?token=%24%7BRAG_API_TOKEN%7D") as ws:
            assert ws.receive_json()["code"] == "AUTH"

    @pytest.mark.parametrize(
        ("url", "headers"),
        [
            ("/api/v1/chat", None),
            ("/api/v1/chat?token=wrong", None),
            ("/api/v1/chat?token=", None),
            ("/api/v1/chat", {"Authorization": "Bearer wrong"}),
            ("/api/v1/chat", {"Authorization": f"Basic {TOKEN}"}),
            ("/api/v1/chat", {"Authorization": TOKEN}),
        ],
    )
    def test_rejected_with_auth_error_frame_and_policy_close(self, base_settings, url, headers):
        client = self._client(base_settings, TOKEN)
        with client.websocket_connect(url, headers=headers or {}) as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error" and frame["code"] == "AUTH"
            assert assert_closed(ws) == 1008

    @pytest.mark.parametrize(
        ("url", "headers"),
        [
            (f"/api/v1/chat?token={TOKEN}", None),
            ("/api/v1/chat", {"Authorization": f"Bearer {TOKEN}"}),
            ("/api/v1/chat", {"authorization": f"bearer {TOKEN}"}),
        ],
    )
    def test_accepted_via_query_or_header(self, base_settings, url, headers):
        client = self._client(base_settings, TOKEN)
        with client.websocket_connect(url, headers=headers or {}) as ws:
            ws.send_json(HYBRID_FRAME)
            assert receive_until_terminal(ws)[-1]["type"] == "result"

    def test_origin_checked_for_browser_clients(self, base_settings):
        client = self._client(base_settings, None)
        with client.websocket_connect("/api/v1/chat", headers={"Origin": "http://evil.example"}) as ws:
            frame = ws.receive_json()
            assert frame["code"] == "BAD_REQUEST" and "Origin" in frame["message"]
        with client.websocket_connect("/api/v1/chat", headers={"Origin": "http://localhost:3000"}) as ws:
            ws.send_json(HYBRID_FRAME)
            assert receive_until_terminal(ws)[-1]["type"] == "result"
        # 同源托管（Origin 与 Host 一致）无需列入 allowed_origins。
        with client.websocket_connect(
            "/api/v1/chat", headers={"Origin": "http://testserver", "Host": "testserver"}
        ) as ws:
            ws.send_json(HYBRID_FRAME)
            assert receive_until_terminal(ws)[-1]["type"] == "result"


# --------------------------------------------------------------------------- cancel / disconnect / limits
class TestCancelAndLimits:
    def test_cancel_frame_mid_turn_yields_cancelled_result(self, base_settings):
        engine = ScriptedEngine(hold=True)
        client = make_client(base_settings, ChatServices(base_settings, engine=engine))

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json(HYBRID_FRAME)
            assert ws.receive_json()["type"] == "session"
            assert ws.receive_json() == {"type": "stream", "content": "a"}
            ws.send_json({"type": "cancel"})
            terminal = ws.receive_json()
            assert terminal["type"] == "result" and terminal["cancelled"] is True
            assert assert_closed(ws) == 1000

        assert engine.saw_cancel.is_set() and engine.cancel_event.is_set()

    def test_client_disconnect_is_cancel_and_closes_the_generator(self, base_settings):
        engine = ScriptedEngine(hold=True)
        client = make_client(base_settings, ChatServices(base_settings, engine=engine))

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json(HYBRID_FRAME)
            assert ws.receive_json()["type"] == "session"
        # 退出 with 即客户端断开。
        assert engine.closed.wait(5), "generator must be closed after client disconnect"
        assert engine.cancel_event.is_set()

    def test_extra_frame_mid_turn_is_a_protocol_violation(self, base_settings):
        engine = ScriptedEngine(hold=True)
        client = make_client(base_settings, ChatServices(base_settings, engine=engine))

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json(HYBRID_FRAME)
            ws.receive_json()
            ws.receive_json()
            ws.send_json(HYBRID_FRAME)
            frame = ws.receive_json()
            assert frame["code"] == "BAD_REQUEST" and "cancel" in frame["message"]
            assert assert_closed(ws) == 1008
        assert engine.closed.wait(5) and engine.cancel_event.is_set()

    def test_max_connections_rejects_excess_and_releases_slots(self, base_settings):
        st = replace(base_settings, chat=ChatSettings(max_connections=1))
        engine = ScriptedEngine()
        client = make_client(st, ChatServices(st, engine=engine))

        with client.websocket_connect("/api/v1/chat") as first:
            # 第一条连接已占位（尚未发首帧）；第二条被拒。
            with client.websocket_connect("/api/v1/chat") as second:
                frame = second.receive_json()
                assert frame == {
                    "type": "error", "code": "RATE_LIMITED", "message": "Too many concurrent chat connections",
                }
                assert assert_closed(second) == 1008
            first.send_json(HYBRID_FRAME)
            assert receive_until_terminal(first)[-1]["type"] == "result"
            assert_closed(first)

        with client.websocket_connect("/api/v1/chat") as again:
            again.send_json(HYBRID_FRAME)
            assert receive_until_terminal(again)[-1]["type"] == "result"

    def test_slot_released_after_validation_failure(self, base_settings):
        st = replace(base_settings, chat=ChatSettings(max_connections=1))
        client = make_client(st, ChatServices(st, engine=ScriptedEngine()))
        for _ in range(3):
            with client.websocket_connect("/api/v1/chat") as ws:
                ws.send_json({"type": "nope"})
                assert ws.receive_json()["code"] == "BAD_REQUEST"


# --------------------------------------------------------------------------- WC7.2 / WC7.3 REST
class TestRest:
    @pytest.fixture
    def client(self, base_settings, store):
        chat, _ = real_engine_services(base_settings, store)
        return make_client(base_settings, chat)

    def test_sessions_list_get_delete(self, client, store):
        a = store.create_session(title="first")
        store.append_message(a["session_id"], "user", "hi")
        store.append_message(a["session_id"], "assistant", "hello", sources=[{"id": 1}], metadata={"grounded": True})
        b = store.create_session(title="second")

        listing = client.get("/api/v1/chat/sessions").json()
        assert listing["total"] == 2 and listing["limit"] == 50 and listing["offset"] == 0
        assert [s["session_id"] for s in listing["items"]] == [b["session_id"], a["session_id"]]
        assert listing["items"][1]["message_count"] == 2
        assert "messages" not in listing["items"][0]

        page = client.get("/api/v1/chat/sessions", params={"limit": 1, "offset": 1}).json()
        assert page["total"] == 2 and [s["title"] for s in page["items"]] == ["first"]

        detail = client.get(f"/api/v1/chat/sessions/{a['session_id']}").json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert detail["messages"][1]["sources"] == [{"id": 1}]
        assert detail["messages"][1]["metadata"] == {"grounded": True}

        deleted = client.delete(f"/api/v1/chat/sessions/{a['session_id']}")
        assert deleted.status_code == 200 and deleted.json() == {"deleted": True, "session_id": a["session_id"]}
        assert client.get(f"/api/v1/chat/sessions/{a['session_id']}").status_code == 404
        assert client.get("/api/v1/chat/sessions").json()["total"] == 1

    def test_unknown_session_unified_404(self, client):
        for response in (client.get("/api/v1/chat/sessions/ghost"), client.delete("/api/v1/chat/sessions/ghost")):
            assert response.status_code == 404
            body = response.json()
            assert body["success"] is False and body["error"]["code"] == "not_found"

    def test_pagination_validation_is_unified_422(self, client):
        response = client.get("/api/v1/chat/sessions", params={"limit": 0})
        assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"

    def test_rest_requires_token_when_configured(self, base_settings, store):
        chat, _ = real_engine_services(base_settings, store)
        client = make_client(base_settings, chat, api=ApiSettings(api_token=TOKEN))
        for method, path in (
            ("get", "/api/v1/chat/sessions"),
            ("get", "/api/v1/chat/sessions/x"),
            ("delete", "/api/v1/chat/sessions/x"),
            ("get", "/api/v1/chat/capabilities"),
        ):
            assert getattr(client, method)(path).status_code == 401
        ok = client.get("/api/v1/chat/sessions", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200

    def test_capabilities_agentic_disabled(self, client):
        body = client.get("/api/v1/chat/capabilities").json()
        assert body == {
            "enabled": True,
            "retrievals": ["hybrid"],
            "default_retrieval": "hybrid",
            "agentic_enabled": False,
            "allow_deep": False,
            "progress_stream": False,
            "max_connections": 100,
        }

    @pytest.mark.parametrize("allow_deep", [False, True])
    def test_capabilities_agentic_enabled(self, base_settings, store, tmp_path, allow_deep):
        agentic = replace(
            base_settings.agentic_search, enabled=True, allow_deep=allow_deep, allowed_roots=[str(tmp_path)]
        )
        st = replace(
            base_settings,
            agentic_search=agentic,
            chat=ChatSettings(default_retrieval="agentic_fast", agentic_progress_stream=True),
        )
        retrievers = build_chat_retrievers(
            st, query_service=FakeQueryService(), agentic_tool=AgenticSearchTool(search=FakeAgenticSearch(), settings=st)
        )
        client = make_client(st, ChatServices(st, session_store=store, retrievers=retrievers))

        body = client.get("/api/v1/chat/capabilities").json()

        assert body["retrievals"] == ["hybrid", "agentic_fast", "agentic_deep"]
        assert body["agentic_enabled"] is True and body["allow_deep"] is allow_deep
        assert body["default_retrieval"] == "agentic_fast" and body["progress_stream"] is True

    def test_capabilities_follow_injected_engine(self, base_settings):
        engine = ScriptedEngine()
        engine.available_retrievals = ("hybrid", "agentic_fast")
        client = make_client(base_settings, ChatServices(base_settings, engine=engine))
        assert client.get("/api/v1/chat/capabilities").json()["retrievals"] == ["hybrid", "agentic_fast"]


# --------------------------------------------------------------------------- WC7.3 / WC7.4 end to end
def _agentic_services(settings, store, tmp_path, log_file, *, chat_settings=None, allow_deep=False, **fake_kwargs):
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "a.md").write_text("hello", encoding="utf-8")
    agentic = replace(settings.agentic_search, enabled=True, allow_deep=allow_deep, allowed_roots=[str(docs)])
    st = replace(
        settings,
        agentic_search=agentic,
        chat=chat_settings or ChatSettings(),
        observability=replace(settings.observability, log_file=str(log_file)),
    )
    fake = FakeAgenticSearch(
        answer="sidecar 答案 [1]",
        evidences=[
            AgenticEvidence(source_path=str(docs / "a.md"), snippet="evidence", score=0.9, char_start=0, char_end=8)
        ],
        source_files=[str(docs / "a.md")],
        **fake_kwargs,
    )
    tool = AgenticSearchTool(search=fake, settings=st)  # settings 非空：若误落盘会多出一条 trace
    retrievers = build_chat_retrievers(st, query_service=FakeQueryService(), agentic_tool=tool)
    chat = ChatServices(st, session_store=store, retrievers=retrievers, llm_factory=lambda _s: FakeLLM())
    return chat, st


def _trace_lines(log_file: Path) -> list[dict[str, Any]]:
    if not log_file.exists():
        return []
    return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestTraceEndToEnd:
    def test_hybrid_turn_writes_exactly_one_chat_trace(self, base_settings, store, tmp_path):
        log_file = tmp_path / "logs" / "traces.jsonl"
        chat, _ = real_engine_services(base_settings, store, log_file=log_file)
        client = make_client(base_settings, chat)

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json(HYBRID_FRAME)
            frames = receive_until_terminal(ws)

        traces = _trace_lines(log_file)
        assert len(traces) == 1
        trace = traces[0]
        assert trace["trace_type"] == "query" and trace["trace_id"] == frames[-1]["trace_id"]
        assert trace["metadata"]["routes"] == "chat" and trace["metadata"]["retrieval"] == "hybrid"
        assert trace["metadata"]["session_id"] == frames[0]["session_id"]
        assert [s["stage"] for s in trace["stages"]] == [
            "chat_history", "chat_rewrite", "dense_retrieval", "chat_generation",
        ]
        # 检索组件提前 finish 过 trace；总耗时仍须在生成阶段之后才结算。
        assert trace["total_elapsed_ms"] is not None and trace["finished_at"]

    @pytest.mark.parametrize("progress", [False, True])
    def test_agentic_turn_writes_exactly_one_chat_trace(self, base_settings, store, tmp_path, progress):
        log_file = tmp_path / "traces.jsonl"
        chat, _ = _agentic_services(
            base_settings, store, tmp_path, log_file, chat_settings=ChatSettings(agentic_progress_stream=progress)
        )
        client = make_client(base_settings, chat)

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json({**HYBRID_FRAME, "retrieval": "agentic_fast"})
            frames = receive_until_terminal(ws)

        types = [f["type"] for f in frames]
        assert types[-1] == "result" and frames[-1]["content"] == "sidecar 答案 [1]"
        assert ("search_log" in types) is progress
        sources = next(f for f in frames if f["type"] == "sources")
        assert sources["citations"][0]["id"] == 1

        traces = _trace_lines(log_file)
        assert len(traces) == 1
        assert traces[0]["metadata"]["routes"] == "chat" and traces[0]["metadata"]["retrieval"] == "agentic_fast"
        stages = [s["stage"] for s in traces[0]["stages"]]
        assert stages == ["chat_history", "chat_rewrite", "agentic_search", "chat_generation"]
        assert traces[0]["stages"][-1]["method"] == "sidecar_passthrough"

    def test_progress_stream_forwards_sidecar_logs_as_search_log_frames(self, base_settings, store, tmp_path):
        chat, _ = _agentic_services(
            base_settings, store, tmp_path, tmp_path / "t.jsonl",
            chat_settings=ChatSettings(agentic_progress_stream=True),
        )
        client = make_client(base_settings, chat)

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json({**HYBRID_FRAME, "retrieval": "agentic_fast"})
            frames = receive_until_terminal(ws)

        logs = [f for f in frames if f["type"] == "search_log"]
        assert [f["message"] for f in logs] == [
            "fake: analyzing query", "fake: searching files", "fake: synthesizing answer",
        ]
        assert all(f["task_id"] == frames[-1]["trace_id"] for f in logs)
        order = [f["type"] for f in frames]
        assert order.index("search_log") < order.index("search_complete")

    def test_deep_disabled_error_frame_still_one_trace(self, base_settings, store, tmp_path):
        log_file = tmp_path / "traces.jsonl"
        chat, _ = _agentic_services(base_settings, store, tmp_path, log_file, allow_deep=False)
        client = make_client(base_settings, chat)

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json({**HYBRID_FRAME, "retrieval": "agentic_deep"})
            frames = receive_until_terminal(ws)

        assert frames[-1]["type"] == "error" and frames[-1]["code"] == "DEEP_DISABLED"
        traces = _trace_lines(log_file)
        assert len(traces) == 1 and traces[0]["metadata"]["routes"] == "chat"

    def test_sidecar_down_maps_to_upstream_error_after_retry(self, base_settings, store, tmp_path):
        from libs.agentic_search.base_agentic_search import AgenticSearchBackendError

        log_file = tmp_path / "traces.jsonl"
        chat, st = _agentic_services(
            base_settings, store, tmp_path, log_file,
            chat_settings=ChatSettings(pipeline_max_retries=0), error=AgenticSearchBackendError("down"),
        )
        client = make_client(base_settings, chat)

        with client.websocket_connect("/api/v1/chat") as ws:
            ws.send_json({**HYBRID_FRAME, "retrieval": "agentic_fast"})
            frames = receive_until_terminal(ws)

        assert frames[-1]["code"] == "UPSTREAM"
        traces = _trace_lines(log_file)
        assert len(traces) == 1
        assert [s["stage"] for s in traces[0]["stages"] if s["stage"] == "agentic_search"] == ["agentic_search"]

    def test_production_wiring_builds_per_turn_engine_with_persist(self, base_settings, store, tmp_path):
        log_file = tmp_path / "traces.jsonl"
        chat, st = real_engine_services(base_settings, store, log_file=log_file)
        engines = [chat.new_engine() for _ in range(3)]  # 持有引用，避免 id 复用
        assert len({id(e) for e in engines}) == 3  # 每轮新引擎（独立 LLM，usage 不串线）
        assert all(isinstance(e, ChatEngine) for e in engines)

        list(engines[0].run_turn("hello"))
        assert len(_trace_lines(log_file)) == 1  # 生产装配自带落盘回调，指向 observability.log_file
