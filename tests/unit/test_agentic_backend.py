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
