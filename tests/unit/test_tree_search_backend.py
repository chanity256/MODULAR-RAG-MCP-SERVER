"""PI3: PageIndexHttpBackend MockTransport 单测（离线，零网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from libs.tree_search.base_tree_search import (
    TreeEvidence,
    TreeSearchBackendError,
    TreeSearchRequest,
    TreeSearchResponseError,
    TreeSearchTimeoutError,
)
from libs.tree_search.converter import parse_search_data
from libs.tree_search.http_backend import PageIndexHttpBackend

_CONTRACT = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/pageindex/contract_search_response.json").read_text(
        encoding="utf-8"
    )
)


class _Cfg:
    def __init__(self, **overrides) -> None:
        self.enabled = True
        self.backend = "pageindex_http"
        self.base_url = "http://127.0.0.1:8585"
        self.api_token = "tok-pageindex"
        self.timeout_s = 240.0
        self.index_timeout_s = 1800.0
        self.max_nodes = 5
        self.max_loops = 3
        self.max_token_budget = 32000
        self.allowed_roots = ["/repo/data/documents"]
        self.denied_substrings = [".env", ".git", "data/db"]
        for key, value in overrides.items():
            setattr(self, key, value)


def _envelope(data) -> dict:
    return {"success": True, "data": data, "error": None}


def _data(evidences=None, **extra) -> dict:
    body = {
        "evidences": evidences
        if evidences is not None
        else [
            {
                "source_path": "/repo/data/documents/a.md",
                "doc_id": "pi-1",
                "node_id": "0002",
                "text": "章节正文",
                "score": 1.0,
                "section": "A > B",
                "page_start": None,
                "page_end": None,
                "line_start": 3,
                "line_end": 9,
            }
        ],
        "llm_call_count": 2,
        "total_llm_tokens": 512,
        "telemetry": {"invalid_output": 0},
    }
    body.update(extra)
    return body


def _backend(handler, config: _Cfg | None = None) -> PageIndexHttpBackend:
    return PageIndexHttpBackend(config or _Cfg(), transport=httpx.MockTransport(handler))


class TestRequestContract:
    def test_bearer_header_and_payload_defaults(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_envelope(_data()))

        backend = _backend(handler)
        response = backend.search(TreeSearchRequest(query="模块化 RAG 项目"))
        assert seen["url"] == "http://127.0.0.1:8585/api/v1/search"
        assert seen["auth"] == "Bearer tok-pageindex"
        # None 的请求级覆盖回填 config 默认值（3.2 / 3.4 请求契约）。
        assert seen["payload"] == {
            "query": "模块化 RAG 项目",
            "source_paths": None,
            "max_nodes": 5,
            "max_loops": 3,
            "max_token_budget": 32000,
        }
        assert response.llm_call_count == 2
        assert response.total_llm_tokens == 512
        assert response.evidences[0].node_id == "0002"
        assert response.elapsed_ms is not None and response.elapsed_ms >= 0

    def test_request_overrides_and_source_paths_passthrough(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_envelope(_data()))

        backend = _backend(handler)
        backend.search(
            TreeSearchRequest(
                query="q",
                source_paths=["/repo/data/documents/a.md", "/repo/data/documents/b.md"],
                max_nodes=2,
                max_loops=1,
                max_token_budget=8000,
            )
        )
        assert seen["payload"] == {
            "query": "q",
            "source_paths": ["/repo/data/documents/a.md", "/repo/data/documents/b.md"],
            "max_nodes": 2,
            "max_loops": 1,
            "max_token_budget": 8000,
        }

    def test_empty_source_paths_sent_as_fail_closed_empty_list(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_envelope(_data(evidences=[])))

        backend = _backend(handler)
        response = backend.search(TreeSearchRequest(query="q", source_paths=[]))
        assert seen["payload"]["source_paths"] == []
        assert response.evidences == []

    def test_no_auth_header_without_token(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json=_envelope(_data()))

        backend = _backend(handler, _Cfg(api_token=None))
        backend.search(TreeSearchRequest(query="q"))
        assert seen["auth"] is None


class TestErrorMapping:
    def test_timeout_maps_to_timeout_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        backend = _backend(handler)
        with pytest.raises(TreeSearchTimeoutError, match="sidecar timeout"):
            backend.search(TreeSearchRequest(query="q"))

    def test_connection_error_maps_to_backend_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        backend = _backend(handler)
        with pytest.raises(TreeSearchBackendError, match="unreachable"):
            backend.search(TreeSearchRequest(query="q"))

    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    def test_http_error_status_maps_to_backend_error(self, status):
        backend = _backend(
            lambda request: httpx.Response(status, text="boom")
        )
        with pytest.raises(TreeSearchBackendError, match=f"HTTP {status}"):
            backend.search(TreeSearchRequest(query="q"))

    def test_success_false_maps_to_backend_error(self):
        backend = _backend(
            lambda request: httpx.Response(
                200, json={"success": False, "data": None, "error": "render: pandoc missing"}
            )
        )
        with pytest.raises(TreeSearchBackendError, match="render: pandoc missing"):
            backend.search(TreeSearchRequest(query="q"))

    def test_non_json_body_maps_to_response_error(self):
        backend = _backend(lambda request: httpx.Response(200, text="<html>oops</html>"))
        with pytest.raises(TreeSearchResponseError, match="non-JSON"):
            backend.search(TreeSearchRequest(query="q"))

    def test_malformed_data_maps_to_response_error(self):
        for data in [None, "str", {"no_evidences": True}, {"evidences": "x"}]:
            backend = _backend(lambda request, d=data: httpx.Response(200, json=_envelope(d)))
            with pytest.raises(TreeSearchResponseError):
                backend.search(TreeSearchRequest(query="q"))


class TestContractFixture:
    def test_contract_fixture_full_chain(self):
        """契约 fixture 经 MockTransport 解析（PI2 门控真实抓取后替换 fixture 内容）。"""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_CONTRACT)

        backend = _backend(handler)
        response = backend.search(TreeSearchRequest(query="模块化 RAG 项目"))
        md, pdf = response.evidences
        # MD 证据：line_* 有值、page_* 为 None（D8 双形态）。
        assert md.line_start == 210 and md.page_start is None
        # PDF 证据：page_* 有值、line_* 为 None。
        assert pdf.page_start == 14 and pdf.line_start is None
        assert response.telemetry["invalid_output"] == 0
        parsed = parse_search_data(_CONTRACT["data"])
        assert len(parsed.evidences) == 2


def test_import_does_not_pull_pageindex_or_litellm():
    """红线双保险：导入本包不引入 pageindex / litellm（红线脚本之外的断言）。"""
    import sys

    import libs.tree_search  # noqa: F401

    for banned in ("pageindex", "litellm"):
        assert not any(m == banned or m.startswith(banned + ".") for m in sys.modules), banned


def test_fake_backend_records_request_and_is_deterministic():
    from libs.tree_search.fake_backend import FakeTreeSearch

    fake = FakeTreeSearch()
    r1 = fake.search(TreeSearchRequest(query="q"))
    r2 = fake.search(TreeSearchRequest(query="q"))
    assert fake.last_request is not None and fake.last_request.query == "q"
    assert r1.to_dict() == r2.to_dict()
    assert r1.evidences[0].node_id == "0000"


def test_tree_evidence_usable_in_response():
    evidence = TreeEvidence(
        source_path="a.pdf",
        doc_id="d",
        node_id="n",
        text="t",
        score=0.5,
        section="S",
        page_start=2,
        page_end=3,
        line_start=None,
        line_end=None,
    )
    response = parse_search_data({"evidences": [evidence.to_dict()]})
    assert response.evidences == [evidence]
