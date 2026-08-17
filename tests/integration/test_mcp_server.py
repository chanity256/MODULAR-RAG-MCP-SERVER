"""E1: MCP Server 入口与 Stdio 约束（子进程集成测试）。

验收标准：
- 启动 server 能完成 ``initialize``；
- stderr 有日志但 stdout 不污染（stdout 仅含 JSON-RPC 消息）。
"""

from __future__ import annotations

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
    assert msg["result"]["capabilities"] == {"tools": {}}
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


def test_e1_placeholder_methods_report_method_not_found(server):
    """E2 前的占位行为：未接入 ProtocolHandler 的方法返回 -32601。"""
    response = _rpc(server, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    msg = json.loads(response)
    assert msg["error"]["code"] == -32601


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
