"""集成测试：树检索 HTTP 全链路（阶段 PI3）。

本地线程 ``http.server`` 模拟 PageIndex sidecar（仅回环，离线可用），真
``PageIndexHttpBackend``（真 httpx 栈）走完整调用链：请求构造 → HTTP →
信封解析 → TreeEvidence → RetrievalResult。另覆盖错误映射（HTTP 500 /
401 / 非 JSON / 连接拒绝）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from libs.tree_search.base_tree_search import (
    TreeSearchBackendError,
    TreeSearchRequest,
)
from libs.tree_search.converter import tree_evidence_to_retrieval_results
from libs.tree_search.http_backend import PageIndexHttpBackend

_CONTRACT = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/pageindex/contract_search_response.json").read_text(
        encoding="utf-8"
    )
)


class _SidecarHandler(BaseHTTPRequestHandler):
    """可编程 sidecar 桩：按 status 返回预置响应。"""

    status_code = 200
    body: object = _CONTRACT

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        type(self).seen_payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen_auth = self.headers.get("Authorization")
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        payload = type(self).body
        if isinstance(payload, bytes):
            self.wfile.write(payload)
        else:
            self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture()
def sidecar():
    server = HTTPServer(("127.0.0.1", 0), _SidecarHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _backend(base_url: str) -> PageIndexHttpBackend:
    config = type(
        "Cfg",
        (),
        {
            "backend": "pageindex_http",
            "base_url": base_url,
            "api_token": "tok-integration",
            "timeout_s": 5.0,
            "max_nodes": 5,
            "max_loops": 3,
            "max_token_budget": 32000,
        },
    )()
    return PageIndexHttpBackend(config)


class TestHttpRoundtrip:
    def test_full_chain_contract_to_retrieval_results(self, sidecar: str):
        response = _backend(sidecar).search(
            TreeSearchRequest(query="模块化 RAG 项目", source_paths=None)
        )
        assert _SidecarHandler.seen_auth == "Bearer tok-integration"
        assert _SidecarHandler.seen_payload["query"] == "模块化 RAG 项目"
        assert _SidecarHandler.seen_payload["max_token_budget"] == 32000
        results = tree_evidence_to_retrieval_results(response)
        assert len(results) == 2
        md = results[0]
        assert md.chunk_id.startswith("pageindex:pi-")
        assert md.metadata["retrieval_route"] == "longdoc"
        # 结构性断言：fixture 将被 PI2 门控真实抓取整体替换，不锚定具体数值。
        assert md.metadata["line_start"] is not None and md.metadata["page"] is None
        pdf = results[1]
        assert (
            pdf.metadata["page"] is not None
            and pdf.metadata["page_end"] >= pdf.metadata["page"]
        )
        assert pdf.metadata["line_start"] is None

    def test_http_500_maps_to_backend_error(self, sidecar: str):
        _SidecarHandler.status_code = 500
        _SidecarHandler.body = "boom"
        try:
            with pytest.raises(TreeSearchBackendError, match="HTTP 500"):
                _backend(sidecar).search(TreeSearchRequest(query="q"))
        finally:
            _SidecarHandler.status_code = 200
            _SidecarHandler.body = _CONTRACT

    def test_http_401_maps_to_backend_error(self, sidecar: str):
        _SidecarHandler.status_code = 401
        _SidecarHandler.body = {"success": False, "data": None, "error": "unauthorized"}
        try:
            with pytest.raises(TreeSearchBackendError, match="HTTP 401"):
                _backend(sidecar).search(TreeSearchRequest(query="q"))
        finally:
            _SidecarHandler.status_code = 200
            _SidecarHandler.body = _CONTRACT

    def test_non_json_body_maps_to_response_error(self, sidecar: str):
        from libs.tree_search.base_tree_search import TreeSearchResponseError

        _SidecarHandler.body = b"<html>gateway error</html>"
        try:
            with pytest.raises(TreeSearchResponseError, match="non-JSON"):
                _backend(sidecar).search(TreeSearchRequest(query="q"))
        finally:
            _SidecarHandler.body = _CONTRACT

    def test_connection_refused_maps_to_backend_error(self):
        # 1/0 端口不可绑定连接，回环立即拒绝（离线确定性）。
        backend = _backend("http://127.0.0.1:1")
        with pytest.raises(TreeSearchBackendError, match="unreachable"):
            backend.search(TreeSearchRequest(query="q"))
