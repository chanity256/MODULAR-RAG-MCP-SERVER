"""S1.4: SirchmunkHttpBackend 单元测试（httpx.MockTransport 离线）。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from libs.agentic_search.base_agentic_search import (
    AgenticSearchBackendError,
    AgenticSearchRequest,
    AgenticSearchResponseError,
    AgenticSearchTimeoutError,
)
from libs.agentic_search.converter import parse_search_context
from libs.agentic_search.http_backend import SirchmunkHttpBackend


class _Config:
    base_url = "http://127.0.0.1:8584"
    api_token = "tok-1"
    timeout_s = 5.0
    top_k_files = 5
    max_loops = 6
    max_token_budget = 32000


def _backend(handler, config=_Config) -> SirchmunkHttpBackend:
    return SirchmunkHttpBackend(config, transport=httpx.MockTransport(handler))


def _ok_handler(request: httpx.Request) -> httpx.Response:
    body = {
        "success": True,
        "data": {"answer": "ok", "cluster": None, "llm_usages": [{"total_tokens": 9}]},
    }
    return httpx.Response(200, json=body)


def _request() -> AgenticSearchRequest:
    return AgenticSearchRequest(query="q", mode="FAST", paths=["/tmp/x"])


class TestRequestShape:
    def test_payload_contract(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return _ok_handler(request)

        _backend(handler).search(_request())
        assert captured["url"].endswith("/api/v1/search")
        assert captured["headers"].get("authorization") == "Bearer tok-1"
        payload = captured["payload"]
        assert payload["mode"] == "FAST"
        assert payload["response_format"] == "context"
        assert payload["paths"] == ["/tmp/x"]
        assert payload["top_k_files"] == 5 and payload["max_loops"] == 6
        assert payload["max_token_budget"] == 32000

    def test_config_defaults_applied(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return _ok_handler(request)

        request = AgenticSearchRequest(query="q", mode="DEEP", paths=None)
        _backend(handler).search(request)
        assert captured["payload"]["paths"] is None

    def test_elapsed_ms_recorded(self):
        response = _backend(_ok_handler).search(_request())
        assert response.elapsed_ms is not None and response.elapsed_ms >= 0


class TestErrorMapping:
    def test_timeout(self):
        def handler(request):
            raise httpx.ConnectTimeout("timeout")

        with pytest.raises(AgenticSearchTimeoutError, match="timeout"):
            _backend(handler).search(_request())

    def test_connect_error(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        with pytest.raises(AgenticSearchBackendError, match="unreachable"):
            _backend(handler).search(_request())

    def test_http_500(self):
        with pytest.raises(AgenticSearchBackendError, match="HTTP 500"):
            _backend(lambda request: httpx.Response(500, text="boom")).search(_request())

    def test_http_401(self):
        with pytest.raises(AgenticSearchBackendError, match="HTTP 401"):
            _backend(lambda request: httpx.Response(401, text="unauthorized")).search(_request())

    def test_success_false(self):
        body = {"success": False, "error": "Search failed: LLM exploded"}
        with pytest.raises(AgenticSearchBackendError, match="LLM exploded"):
            _backend(lambda request: httpx.Response(200, json=body)).search(_request())

    def test_non_json_body(self):
        with pytest.raises(AgenticSearchResponseError, match="non-JSON"):
            _backend(lambda request: httpx.Response(200, text="<html>")).search(_request())

    def test_malformed_data(self):
        body = {"success": True, "data": {"no_answer": 1}}
        with pytest.raises(AgenticSearchResponseError):
            _backend(lambda request: httpx.Response(200, json=body)).search(_request())


class TestContractFixture:
    def test_contract_fixture_locked(self):
        """契约 fixture（S0 真实响应脱敏投影）锁响应 shape，防上游漂移（spec 4.8）。"""
        fixture_path = Path(__file__).resolve().parents[1] / "fixtures/agentic/contract_response.json"
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        response = parse_search_context(payload["data"], mode="FAST")
        assert response.answer == "BM25 uses k1=1.5 and b=0.75 in bm25_indexer.py."
        assert response.total_llm_tokens == 1024
        assert response.llm_call_count == 2
        assert response.source_files == ["data/documents/bm25_indexer.py"]
        assert [e.source_path for e in response.evidences] == [
            "data/documents/bm25_indexer.py",
            "data/documents/bm25_indexer.py",
        ]
        assert response.evidences[0].char_start == 120 and response.evidences[0].char_end == 180
        assert response.evidences[1].snippet == "plain-string snippet from FAST path"


class TestTelemetryContractFixture:
    def test_http_backend_surfaces_telemetry(self):
        """SX1：telemetry 契约 fixture 经 HTTP 后端端到端解析（MockTransport，离线）。"""
        fixture_path = Path(__file__).resolve().parents[1] / "fixtures/agentic/contract_response_telemetry.json"
        body = json.loads(fixture_path.read_text(encoding="utf-8"))
        response = _backend(lambda request: httpx.Response(200, json=body)).search(_request())
        assert response.telemetry is not None
        assert response.telemetry["loop_count"] == 2
        assert response.telemetry["retrieval_tool_counts"] == {"deep_file_extract": 1, "keyword_search": 2}
        assert response.telemetry["self_correct_entered"] is True
        assert response.elapsed_ms is not None


class _DepthConfig(_Config):
    max_depth = 3
    enable_dir_scan = False


def _capture_payload(config=_Config, request=None) -> dict:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content.decode("utf-8")))
        return _ok_handler(req)

    _backend(handler, config=config).search(request or _request())
    return captured


class TestRequestPassthrough:
    """SX2：max_depth / enable_dir_scan 透传 + llm_fallback 恒 false。"""

    def test_llm_fallback_always_false(self):
        assert _capture_payload()["llm_fallback"] is False
        assert _capture_payload(config=_DepthConfig)["llm_fallback"] is False

    def test_defaults_when_config_lacks_keys(self):
        payload = _capture_payload()
        assert payload["enable_dir_scan"] is True
        assert "max_depth" not in payload  # None 不发送，走上游默认

    def test_config_values_forwarded(self):
        payload = _capture_payload(config=_DepthConfig)
        assert payload["max_depth"] == 3
        assert payload["enable_dir_scan"] is False

    def test_request_overrides_config(self):
        request = AgenticSearchRequest(
            query="q", mode="DEEP", paths=["/tmp/x"], max_depth=7, enable_dir_scan=True
        )
        payload = _capture_payload(config=_DepthConfig, request=request)
        assert payload["max_depth"] == 7
        assert payload["enable_dir_scan"] is True

    def test_upstream_field_names(self):
        """字段名与上游 SearchRequest 一致（FastAPI 静默丢弃未知字段，spec 4.7）。"""
        payload = _capture_payload(config=_DepthConfig)
        assert {"max_depth", "enable_dir_scan", "llm_fallback"} <= set(payload)
        assert not {"depth", "dir_scan", "fallback"} & set(payload)

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"max_depth": 0}, id="max-depth-zero"),
            pytest.param({"max_depth": True}, id="max-depth-bool"),
            pytest.param({"max_depth": "3"}, id="max-depth-str"),
            pytest.param({"enable_dir_scan": 1}, id="dir-scan-int"),
            pytest.param({"enable_dir_scan": "false"}, id="dir-scan-str"),
        ],
    )
    def test_request_validation(self, kwargs):
        with pytest.raises(ValueError):
            AgenticSearchRequest(query="q", **kwargs)
