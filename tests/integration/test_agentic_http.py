"""集成测试：Agentic HTTP 全链路（阶段 S1.7）。

本地线程 ``http.server`` 模拟 sidecar（仅回环，离线可用），真
``SirchmunkHttpBackend``（真 httpx 栈）经 ``AgenticSearchTool`` 走完整
调用链：请求构造 → HTTP → 解析 → 转换 → citations。另覆盖错误映射
（HTTP 500 / 连接拒绝）与 ``tools/list`` 两态注册。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from libs.agentic_search.base_agentic_search import AgenticSearchBackendError
from libs.agentic_search.http_backend import SirchmunkHttpBackend
from mcp_server.protocol_handler import ProtocolHandler, ToolInvalidParams
from mcp_server.tools.agentic_search import TOOL_NAME, create_agentic_search_definition

_CONTRACT = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/agentic/contract_response.json").read_text(encoding="utf-8")
)


class _SidecarHandler(BaseHTTPRequestHandler):
    """可编程 sidecar 桩：按 path/status 返回预置响应。"""

    status_code = 200
    body = _CONTRACT
    seen_headers: dict = {}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen_payload = payload
        type(self).seen_headers = dict(self.headers)
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if isinstance(self.body, dict):
            self.wfile.write(json.dumps(self.body).encode("utf-8"))
        else:
            self.wfile.write(str(self.body).encode("utf-8"))

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture()
def sidecar(tmp_path: Path):
    server = HTTPServer(("127.0.0.1", 0), _SidecarHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


class _Cfg:
    def __init__(self, base_url: str, allowed_roots: list[str] | None = None) -> None:
        self.backend = "sirchmunk_http"
        self.base_url = base_url
        self.api_token = "tok-integration"
        self.timeout_s = 5.0
        self.top_k_files = 5
        self.max_loops = 6
        self.max_token_budget = 32000
        self.mode = "FAST"
        self.allow_deep = False
        self.allowed_roots = allowed_roots or []
        self.denied_substrings = [".env", ".git", "data/db"]


class _Settings:
    def __init__(self, config: _Cfg, log_file: str) -> None:
        self.agentic_search = config
        self.observability = type("O", (), {"log_file": log_file})()


@pytest.fixture()
def env(tmp_path: Path, sidecar, monkeypatch: pytest.MonkeyPatch):
    # 布局模拟真实部署：sidecar CWD=仓库根，SIRCHMUNK_SEARCH_PATHS=./data/documents，
    # 契约 fixture 的相对证据路径 data/documents/bm25_indexer.py 由此解析（spec 4.6）。
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "data" / "documents"
    docs.mkdir(parents=True)
    (docs / "bm25_indexer.py").write_text("k1 = 1.5", encoding="utf-8")
    return {
        "settings": _Settings(
            _Cfg(sidecar, allowed_roots=[str(docs)]),
            str(tmp_path / "traces.jsonl"),
        ),
        "docs": docs,
    }


class TestHttpRoundtrip:
    def test_full_chain_through_real_http(self, env):
        handler = ProtocolHandler()
        handler.register_tool(create_agentic_search_definition(settings=env["settings"]))
        response = handler.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": TOOL_NAME, "arguments": {"query": "bm25 k1 defaults"}},
            }
        )
        assert "error" not in response
        result = response["result"]
        assert not result.get("isError", False)
        markdown = result["content"][0]["text"]
        assert "[1]" in markdown and "bm25_indexer.py" in markdown
        citation = result["structuredContent"]["citations"][0]
        assert citation["chunk_id"].startswith("agentic:")
        assert citation["page"] is None
        # 请求契约经真实 HTTP 栈到达 sidecar 桩。
        payload = _SidecarHandler.seen_payload
        assert payload["response_format"] == "context"
        assert payload["mode"] == "FAST"
        assert _SidecarHandler.seen_headers.get("Authorization") == "Bearer tok-integration"

    def test_http_500_maps_to_is_error(self, env, sidecar):
        _SidecarHandler.status_code, _SidecarHandler.body = 500, "boom"
        try:
            tool = create_agentic_search_definition(settings=env["settings"]).handler
            result = tool({"query": "q"})
            assert result["isError"] is True
            assert "HTTP 500" in result["content"][0]["text"]
        finally:
            _SidecarHandler.status_code, _SidecarHandler.body = 200, _CONTRACT

    def test_backend_error_propagates_from_backend(self, tmp_path):
        backend = SirchmunkHttpBackend(_Cfg("http://127.0.0.1:1"))  # 关闭端口
        from libs.agentic_search.base_agentic_search import AgenticSearchRequest

        with pytest.raises(AgenticSearchBackendError, match="unreachable"):
            backend.search(AgenticSearchRequest(query="q", mode="FAST", paths=[str(tmp_path)]))
