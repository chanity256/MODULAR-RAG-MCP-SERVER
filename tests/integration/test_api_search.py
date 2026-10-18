"""Query Playground REST + WS：``POST /api/v1/search``、``GET /api/v1/search/capabilities``
与 ``WS /api/v1/search/ws``（WC9.4 后端 / WEBUI_SIDECAR_SPEC WS2）。

全部离线：注入 fake SearchService，或把 fake query/agentic tool 注入真实 SearchService
以验证门控与归一化；零网络、零真实模型。
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from api.deps import ServiceContainer
from core.settings import ApiSettings
from observability.dashboard.services.search_service import (
    ModeAvailability,
    SearchOutcome,
    SearchService,
)

TOKEN = "search-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeSearch:
    """与 SearchService 对外接口同形：记录调用、按配置返回结果。"""

    def __init__(self, *, enabled=True, allow_deep=True, outcome=None):
        self.enabled = enabled
        self.allow_deep = allow_deep
        self.outcome = outcome
        self.calls: list[tuple] = []
        self.stream_logs: list[tuple] = []
        self.gate: threading.Event | None = None  # 非 None 时 run_stream 阻塞到 set

    def availability(self, settings=None):
        deep = self.enabled and self.allow_deep
        return {
            "normal": ModeAvailability(True),
            "fast": ModeAvailability(self.enabled, None if self.enabled else "enable agentic_search"),
            "deep": ModeAvailability(deep, None if deep else "enable allow_deep"),
        }

    def run(self, mode, query, top_k=None, collection=None):
        self.calls.append((mode, query, top_k, collection))
        if self.outcome is not None:
            return self.outcome
        meta = {"mode": mode.upper(), "llm_call_count": 2, "telemetry": {"zero_llm": False}} if mode != "normal" else None
        return SearchOutcome(
            mode=mode,
            markdown=f"answer for {query}",
            citations=[{"id": 1, "source": "a.md", "page": 2, "score": 0.9, "text": "t"}],
            agentic_meta=meta,
            elapsed_ms=12.5,
        )

    def run_stream(self, mode, query, top_k=None, collection=None, *, on_event):
        """SX5 契约同形：deep 走流式回调，其余模式回退 run（结果形态一致）。"""
        self.calls.append(("stream", mode, query, top_k, collection))
        for level, message, ts in self.stream_logs:
            on_event(level, message, ts)
        if self.gate is not None:
            self.gate.wait(5)
        if mode != "deep":
            return self.run(mode, query, top_k=top_k, collection=collection)
        if self.outcome is not None:
            return self.outcome
        return SearchOutcome(
            mode="deep",
            markdown=f"answer for {query}",
            citations=[{"id": 1, "source": "a.md", "page": 2, "score": 0.9, "text": "t"}],
            agentic_meta={"mode": "DEEP"},
            elapsed_ms=8.0,
        )


def make_client(service, api: ApiSettings | None = None) -> TestClient:
    container = ServiceContainer(None, search_service=service)
    return TestClient(create_app(api_settings=api or ApiSettings(), services=container), raise_server_exceptions=False)


def post(client, **body):
    return client.post("/api/v1/search", json=body)


def error_code(response) -> str:
    return response.json()["error"]["code"]


class TestSearch:
    def test_normal(self):
        service = FakeSearch(enabled=False)
        response = post(make_client(service), query="  what is rrf  ", top_k=3, collection=" docs ")
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "mode": "normal",
            "query": "what is rrf",
            "elapsed_ms": 12.5,
            "markdown": "answer for what is rrf",
            "structured": {"citations": [{"id": 1, "source": "a.md", "page": 2, "score": 0.9, "text": "t"}]},
            "is_error": False,
            "error": None,
        }
        assert service.calls == [("normal", "what is rrf", 3, "docs")]

    def test_defaults_to_normal_without_optional_args(self):
        service = FakeSearch()
        assert post(make_client(service), query="q").status_code == 200
        assert service.calls == [("normal", "q", None, None)]

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_agentic_modes_carry_telemetry(self, mode):
        service = FakeSearch()
        body = post(make_client(service), mode=mode, query="q", top_k=5).json()
        assert body["mode"] == mode
        assert body["structured"]["agentic"] == {"mode": mode.upper(), "llm_call_count": 2, "telemetry": {"zero_llm": False}}
        assert body["structured"]["citations"][0]["source"] == "a.md"
        assert service.calls == [(mode, "q", 5, None)]

    def test_tool_failure_is_200_with_is_error(self):
        outcome = SearchOutcome(mode="fast", markdown="sidecar down", is_error=True, error="sidecar down", elapsed_ms=3.0)
        response = post(make_client(FakeSearch(outcome=outcome)), mode="fast", query="q")
        assert response.status_code == 200
        body = response.json()
        assert body["is_error"] is True and body["error"] == "sidecar down"
        assert body["markdown"] == "sidecar down"
        assert body["structured"] == {"citations": []}

    def test_deep_gated_by_allow_deep(self):
        service = FakeSearch(enabled=True, allow_deep=False)
        client = make_client(service)
        response = post(client, mode="deep", query="q")
        assert response.status_code == 403
        assert error_code(response) == "DEEP_DISABLED"
        assert response.json()["success"] is False
        assert service.calls == []
        assert post(client, mode="fast", query="q").status_code == 200

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_agentic_disabled(self, mode):
        service = FakeSearch(enabled=False, allow_deep=True)
        response = post(make_client(service), mode=mode, query="q")
        assert response.status_code == 403
        assert error_code(response) == "AGENTIC_DISABLED"
        assert service.calls == []

    @pytest.mark.parametrize(
        "body",
        [
            {"query": "q", "paths": ["/etc"]},
            {"query": "q", "topk": 3},
            {"query": ""},
            {"query": "   "},
            {},
            {"query": "q", "mode": "FAST"},
            {"query": "q", "mode": "turbo"},
            {"query": "q", "top_k": 0},
            {"query": "q", "top_k": "3"},
            {"query": "q", "top_k": True},
            {"query": 123},
        ],
    )
    def test_invalid_requests_are_422(self, body):
        service = FakeSearch()
        response = post(make_client(service), **body)
        assert response.status_code == 422
        assert error_code(response) == "validation_error"
        assert service.calls == []

    def test_blank_collection_is_none(self):
        service = FakeSearch()
        post(make_client(service), query="q", collection="  ")
        assert service.calls[0][3] is None

    def test_auth_required(self):
        service = FakeSearch()
        client = make_client(service, ApiSettings(api_token=TOKEN))
        assert client.post("/api/v1/search", json={"query": "q"}).status_code == 401
        assert client.get("/api/v1/search/capabilities").status_code == 401
        assert client.post("/api/v1/search", json={"query": "q"}, headers={"Authorization": "Bearer nope"}).status_code == 401
        assert service.calls == []
        assert client.post("/api/v1/search", json={"query": "q"}, headers=AUTH).status_code == 200
        assert client.get("/api/v1/search/capabilities", headers=AUTH).status_code == 200

    def test_openapi_lists_routes(self):
        paths = make_client(FakeSearch()).get("/openapi.json").json()["paths"]
        assert "post" in paths["/api/v1/search"]
        assert "get" in paths["/api/v1/search/capabilities"]


class TestCapabilities:
    def test_agentic_enabled_with_deep(self):
        body = make_client(FakeSearch(enabled=True, allow_deep=True)).get("/api/v1/search/capabilities").json()
        assert body["modes"] == ["normal", "fast", "deep"]
        assert body["default_mode"] == "normal"
        assert body["agentic_enabled"] is True and body["allow_deep"] is True
        assert body["availability"]["deep"] == {"available": True, "reason": None}

    def test_deep_gated(self):
        body = make_client(FakeSearch(enabled=True, allow_deep=False)).get("/api/v1/search/capabilities").json()
        assert body["modes"] == ["normal", "fast"]
        assert body["allow_deep"] is False
        assert body["availability"]["deep"]["reason"] == "enable allow_deep"

    def test_agentic_disabled_hides_allow_deep(self):
        body = make_client(FakeSearch(enabled=False, allow_deep=True)).get("/api/v1/search/capabilities").json()
        assert body["modes"] == ["normal"]
        assert body["agentic_enabled"] is False and body["allow_deep"] is False
        assert body["availability"]["fast"]["available"] is False


class TestWithRealSearchService:
    """真实 SearchService + fake tool：验证与 Streamlit 页同一条归一化链路。"""

    @staticmethod
    def _service(*, enabled, allow_deep, agentic_tool=None):
        settings = SimpleNamespace(agentic_search=SimpleNamespace(enabled=enabled, allow_deep=allow_deep))

        def query_tool(arguments):
            return {
                "content": [{"type": "text", "text": f"md:{arguments['query']}"}],
                "structuredContent": {"citations": [{"id": 1, "source": "x.md"}]},
            }

        return SearchService(settings, query_tool=query_tool, agentic_tool=agentic_tool)

    def test_normal_passes_through_service(self):
        body = post(make_client(self._service(enabled=False, allow_deep=False)), query="hello").json()
        assert body["markdown"] == "md:hello"
        assert body["structured"] == {"citations": [{"id": 1, "source": "x.md"}]}
        assert body["is_error"] is False
        assert body["elapsed_ms"] >= 0

    def test_deep_gating_matches_service(self):
        client = make_client(self._service(enabled=True, allow_deep=False))
        assert error_code(post(client, mode="deep", query="q")) == "DEEP_DISABLED"
        caps = client.get("/api/v1/search/capabilities").json()
        assert caps["modes"] == ["normal", "fast"]

    def test_agentic_tool_error_maps_to_is_error(self):
        def agentic_tool(arguments):
            return {"content": [{"type": "text", "text": "sidecar unavailable"}], "isError": True}

        client = make_client(self._service(enabled=True, allow_deep=True, agentic_tool=agentic_tool))
        body = post(client, mode="fast", query="q").json()
        assert body["is_error"] is True and body["error"] == "sidecar unavailable"


class TestSearchWebsocket:
    """``WS /api/v1/search/ws``（WS2）：门控拒绝、帧序列、cancel、门控预检。"""

    @staticmethod
    def _recv_until_closed(ws) -> list[dict]:
        frames = []
        try:
            while True:
                frames.append(json.loads(ws.receive_text()))
        except WebSocketDisconnect:
            pass
        return frames

    def test_gate_off_rejects_without_touching_service(self):
        """默认 deep_progress_stream=false：error 帧 + 关闭，不进检索链路（SX5.3 门控）。"""
        service = FakeSearch()
        client = make_client(service, ApiSettings(deep_progress_stream=False))
        with client.websocket_connect("/api/v1/search/ws") as ws:
            frames = self._recv_until_closed(ws)
        assert len(frames) == 1
        assert frames[0]["type"] == "error" and frames[0]["code"] == "UNAVAILABLE"
        assert "deep_progress_stream" in frames[0]["message"]
        assert service.calls == []

    def test_capabilities_reports_progress_stream(self):
        off = make_client(FakeSearch()).get("/api/v1/search/capabilities").json()
        on = make_client(FakeSearch(), ApiSettings(deep_progress_stream=True)).get(
            "/api/v1/search/capabilities"
        ).json()
        assert off["progress_stream"] is False and on["progress_stream"] is True

    def test_deep_stream_frames(self):
        """开启后：search_log 逐条（0.5s 去重）→ search_result（对齐 SearchResponse）。"""
        service = FakeSearch()
        service.stream_logs = [("info", "analyzing", 1.5), ("info", "analyzing", 1.6), ("warning", "slow", None)]
        client = make_client(service, ApiSettings(deep_progress_stream=True))
        with client.websocket_connect("/api/v1/search/ws") as ws:
            ws.send_text(json.dumps({"mode": "deep", "query": "q"}))
            frames = self._recv_until_closed(ws)

        assert [f["type"] for f in frames] == ["search_log", "search_log", "search_result"]
        assert frames[0] == {
            "type": "search_log", "task_id": frames[0]["task_id"], "level": "info",
            "message": "analyzing", "timestamp": 1.5,
        }
        assert frames[0]["task_id"] == frames[1]["task_id"]
        assert frames[1]["level"] == "warning" and "timestamp" not in frames[1]
        result = frames[2]
        assert result["mode"] == "deep" and result["query"] == "q"
        assert result["markdown"] == "answer for q" and result["is_error"] is False
        assert result["structured"]["agentic"] == {"mode": "DEEP"}
        assert service.calls == [("stream", "deep", "q", None, None)]

    def test_fast_mode_falls_back_to_run(self):
        service = FakeSearch()
        client = make_client(service, ApiSettings(deep_progress_stream=True))
        with client.websocket_connect("/api/v1/search/ws") as ws:
            ws.send_text(json.dumps({"mode": "fast", "query": "q"}))
            frames = self._recv_until_closed(ws)
        assert [f["type"] for f in frames] == ["search_result"]
        assert frames[0]["mode"] == "fast" and frames[0]["is_error"] is False
        assert service.calls == [("stream", "fast", "q", None, None), ("fast", "q", None, None)]

    @pytest.mark.parametrize(
        ("kwargs", "code"),
        [({"enabled": False}, "AGENTIC_DISABLED"), ({"enabled": True, "allow_deep": False}, "DEEP_DISABLED")],
    )
    def test_mode_gating_rejected_before_service(self, kwargs, code):
        """与 REST 同款门控预检：被拒请求不触达检索（构造不出错误结果）。"""
        service = FakeSearch(**kwargs)
        client = make_client(service, ApiSettings(deep_progress_stream=True))
        with client.websocket_connect("/api/v1/search/ws") as ws:
            ws.send_text(json.dumps({"mode": "deep", "query": "q"}))
            frames = self._recv_until_closed(ws)
        assert frames[0]["type"] == "error" and frames[0]["code"] == code
        assert service.calls == []

    def test_invalid_start_frame_is_bad_request(self):
        client = make_client(FakeSearch(), ApiSettings(deep_progress_stream=True))
        with client.websocket_connect("/api/v1/search/ws") as ws:
            ws.send_text(json.dumps({"query": "   "}))
            frames = self._recv_until_closed(ws)
        assert frames[0]["type"] == "error" and frames[0]["code"] == "BAD_REQUEST"

    def test_cancel_before_search_is_noop(self):
        client = make_client(FakeSearch(), ApiSettings(deep_progress_stream=True))
        with client.websocket_connect("/api/v1/search/ws") as ws:
            ws.send_text(json.dumps({"type": "cancel"}))
            frames = self._recv_until_closed(ws)
        assert frames == []

    def test_cancel_during_search_discards_result(self):
        """cancel 后进度停止、结果帧被丢弃（阻塞中的检索不可中断，只丢弃）。"""
        service = FakeSearch()
        service.stream_logs = [("info", "step1", None)]
        gate = threading.Event()
        service.gate = gate
        client = make_client(service, ApiSettings(deep_progress_stream=True))
        with client.websocket_connect("/api/v1/search/ws") as ws:
            ws.send_text(json.dumps({"mode": "deep", "query": "q"}))
            assert json.loads(ws.receive_text())["message"] == "step1"
            ws.send_text(json.dumps({"type": "cancel"}))
            gate.set()
            frames = self._recv_until_closed(ws)
        assert all(f["type"] != "search_result" for f in frames)
        assert all(f["type"] != "search_log" for f in frames)

    def test_second_start_frame_is_violation(self):
        """轮进行中再收到检索帧 = 协议违例：error 帧 + 不发结果 + 1008 关闭。"""
        service = FakeSearch()
        gate = threading.Event()
        service.gate = gate
        client = make_client(service, ApiSettings(deep_progress_stream=True))
        with client.websocket_connect("/api/v1/search/ws") as ws:
            ws.send_text(json.dumps({"mode": "deep", "query": "q"}))
            ws.send_text(json.dumps({"mode": "deep", "query": "again"}))
            gate.set()
            frames = self._recv_until_closed(ws)
        assert frames[0]["type"] == "error" and frames[0]["code"] == "BAD_REQUEST"
        assert all(f["type"] != "search_result" for f in frames)
