"""E1: MCP Server 入口与 Stdio 约束（子进程集成测试）。

验收标准：
- 启动 server 能完成 ``initialize``；
- stderr 有日志但 stdout 不污染（stdout 仅含 JSON-RPC 消息）。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"


def _launch() -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(PROJECT_ROOT),
        text=True,
    )


def _rpc(proc: subprocess.Popen, message: dict) -> str:
    """向 server 写入一条 JSON-RPC 消息并读取一行 stdout 响应。"""
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    return proc.stdout.readline()


@pytest.fixture()
def server():
    proc = _launch()
    yield proc
    if proc.poll() is None:  # pragma: no cover - 兜底清理
        proc.terminate()
        proc.wait(timeout=5)


def test_initialize_handshake(server):
    response = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
    )
    msg = json.loads(response)
    assert msg["jsonrpc"] == "2.0"
    assert msg["id"] == 1
    assert msg["result"]["protocolVersion"] == "2025-06-18"
    assert msg["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert msg["result"]["serverInfo"]["name"] == "modular-rag-mcp-server"
    assert "version" in msg["result"]["serverInfo"]


def test_initialized_notification_has_no_response(server):
    assert server.stdin is not None
    server.stdin.write(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
    )
    server.stdin.flush()
    # 通知不应产生任何响应；下一条 initialize 请求的响应必须仍是首条 stdout 消息。
    response = _rpc(server, {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
    msg = json.loads(response)
    assert msg["id"] == 2


def test_stderr_has_logs_stdout_clean_on_eof(server):
    """stdin 关闭后 server 优雅退出：stderr 有日志，stdout 仅 JSON-RPC 消息。"""
    assert server.stdin is not None
    server.stdin.write(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
    )
    server.stdin.close()
    stdout, stderr = server.communicate(timeout=15)
    assert server.returncode == 0
    # stderr 必须有启动/停止日志。
    assert "MCP server starting" in stderr
    assert "MCP server stopped" in stderr
    # stdout 只包含合法 JSON-RPC 消息（本例仅 initialize 响应一行）。
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    msg = json.loads(lines[0])
    assert msg["id"] == 1
    assert "result" in msg


def test_tools_list_and_unknown_method(server):
    """E2/E3：tools/list 返回 query_knowledge_hub schema，未知方法 -32601。

    期望工具列表按仓库配置动态推导：`agentic_search.enabled=true` 时
    server 会注册第 4 个工具（S1.6 条件注册语义）。
    """
    from core.settings import load_settings

    agentic_enabled = load_settings(
        PROJECT_ROOT / "config" / "settings.yaml"
    ).agentic_search.enabled
    expected_tools = [
        "query_knowledge_hub",
        "list_collections",
        "get_document_summary",
    ] + (["agentic_search"] if agentic_enabled else [])

    response = _rpc(server, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    msg = json.loads(response)
    tools = msg["result"]["tools"]
    assert [t["name"] for t in tools] == expected_tools
    assert tools[1]["inputSchema"] == {"type": "object", "properties": {}}
    schema = tools[0]
    assert "hybrid retrieval" in schema["description"]
    assert schema["inputSchema"]["required"] == ["query"]
    assert set(schema["inputSchema"]["properties"]) == {"query", "top_k", "collection"}
    response = _rpc(server, {"jsonrpc": "2.0", "id": 4, "method": "foo/bar"})
    msg = json.loads(response)
    assert msg["error"]["code"] == -32601


def test_query_knowledge_hub_call(server):
    """E3：tools/call query_knowledge_hub 参数校验路径（懒初始化，无网络依赖）。

    - 缺 query / 空 query / 非法 top_k → -32602（校验先于检索栈构建）；
    - 未知 tool → -32602。
    """
    response = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "query_knowledge_hub", "arguments": {}},
        },
    )
    msg = json.loads(response)
    assert msg["error"]["code"] == -32602

    response = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "query_knowledge_hub", "arguments": {"query": "   "}},
        },
    )
    msg = json.loads(response)
    assert msg["error"]["code"] == -32602

    response = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "query_knowledge_hub",
                "arguments": {"query": "bm25 scoring", "top_k": 0},
            },
        },
    )
    msg = json.loads(response)
    assert msg["error"]["code"] == -32602

    response = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        },
    )
    msg = json.loads(response)
    assert msg["error"]["code"] == -32602


def test_shutdown_request_exits_cleanly(server):
    assert server.stdin is not None
    server.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "initialize", "params": {}}) + "\n")
    server.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 10, "method": "shutdown"}) + "\n")
    server.stdin.close()
    stdout, _stderr = server.communicate(timeout=15)
    assert server.returncode == 0
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    # shutdown 不产生响应，stdout 仅剩 initialize 响应。
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 9


def test_query_knowledge_hub_multimodal_image_return(tmp_path):
    """E6：命中 chunk 含 image_refs 时返回 TextContent + ImageContent（Base64）。

    进程内注入 stdin/stdout 运行 MCPServer（E1 支持流注入），检索栈用
    fake（真实组件需凭据/持久化，超出本用例关注点）。
    """
    import base64

    from core.types import RetrievalResult
    from mcp_server.protocol_handler import ProtocolHandler
    from mcp_server.server import MCPServer
    from mcp_server.tools.query_knowledge_hub import create_query_knowledge_hub_definition

    png = tmp_path / "arch.png"
    png.write_bytes(base64.b64decode("iVBORw0KGgo=") + b"diagram-bytes")

    class FakeSearch:
        def search(self, query, top_k=None, filters=None, trace=None):
            return [
                RetrievalResult(
                    chunk_id="c1",
                    score=0.9,
                    text="system architecture diagram [IMAGE: img1]",
                    metadata={
                        "source_path": "arch.pdf",
                        "page": 5,
                        "images": [{"id": "img1", "path": str(png), "page": 5}],
                    },
                )
            ]

    handler = ProtocolHandler()
    handler.register_tool(
        create_query_knowledge_hub_definition(search=FakeSearch(), rerank_enabled=False)
    )
    stdin = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "query_knowledge_hub", "arguments": {"query": "architecture"}},
            }
        )
        + "\n"
    )
    stdout = io.StringIO()
    exit_code = MCPServer(protocol_handler=handler).serve(stdin=stdin, stdout=stdout)
    assert exit_code == 0

    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    result = json.loads(lines[0])["result"]
    content = result["content"]
    assert content[0]["type"] == "text"
    assert "[1]" in content[0]["text"]
    image = content[1]
    assert image["type"] == "image"
    assert image["mimeType"] == "image/png"
    assert base64.b64decode(image["data"]).endswith(b"diagram-bytes")
    assert result["structuredContent"]["images"] == [
        {"id": "img1", "chunk_id": "c1", "mimeType": "image/png"}
    ]
