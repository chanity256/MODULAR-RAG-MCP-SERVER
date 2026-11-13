"""E2E：agentic_search 工具经子进程 MCP server 全链路（阶段 S1.7）。

验证（SIRCHMUNK_INTEGRATION_SPEC S1.7 验收）：
- ``tools/list`` 在 ``agentic_search.enabled=true`` 时出现第 4 个工具；
- sidecar 不可达时 ``tools/call`` 返回友好 ``isError``（而非 -32603）；
- **stdout 纯净断言**：agentic 调用后每行 stdout 均为合法 JSON-RPC
  （spec 4.2 零容忍——上游 DEEP 路径存在无条件 print，sidecar 方案下
  本进程不得被污染）；
- 越权 paths 得到结构化 -32602。

离线策略：base_url 指向本机关闭端口（连接即刻拒绝，无需真实 sidecar）；
真实 sidecar 冒烟走 ``RAG_AGENTIC_INTEGRATION=1`` 门控（默认跳过）。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from textwrap import dedent

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"

_SETTINGS_YAML = dedent(
    """\
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
      persist_path: ./data/db/chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    agentic_search:
      enabled: true
      base_url: http://127.0.0.1:1   # 关闭端口：sidecar 不可达（isError 路径）
      allowed_roots:
        - ./data/documents           # 相对子进程 cwd（tmp workspace）
    """
)


# Note: 为何 stdout 走 pump 线程 + 超时、stderr 写文件、通知不等响应 — 见 .agents/notes/implemented/testing/2026-10-05-e2e-stdio-rpc-hang-guard.md
_RPC_TIMEOUT_S = 15.0


@pytest.fixture()
def server(tmp_path: Path):
    (tmp_path / "config").mkdir()
    (tmp_path / "data" / "documents").mkdir(parents=True)
    (tmp_path / "config" / "settings.yaml").write_text(_SETTINGS_YAML, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    # stderr 写文件而非 PIPE：无人读取的管道写满后子进程会阻塞，进而让 stdout 读取永久挂起。
    stderr_file = (tmp_path / "server.stderr.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        env=env,
        cwd=str(tmp_path),
        text=True,
    )
    lines: queue.Queue[str] = queue.Queue()

    def _pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)

    threading.Thread(target=_pump, daemon=True).start()
    proc.stdout_lines = lines  # type: ignore[attr-defined]
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)
    stderr_file.close()


def _rpc(proc: subprocess.Popen, message: dict, *, expect_response: bool = True) -> dict | None:
    """发送一条 JSON-RPC；通知（无 id）必须传 ``expect_response=False``，否则会等不到响应。"""
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    if not expect_response:
        return None
    try:
        line = proc.stdout_lines.get(timeout=_RPC_TIMEOUT_S)  # type: ignore[attr-defined]
    except queue.Empty:
        pytest.fail(f"no JSON-RPC response within {_RPC_TIMEOUT_S}s for {message.get('method')!r}")
    return json.loads(line)


def _drain_stdout(proc: subprocess.Popen) -> list[str]:
    proc.wait(timeout=_RPC_TIMEOUT_S)
    out: list[str] = []
    while True:
        try:
            out.append(proc.stdout_lines.get(timeout=1))  # type: ignore[attr-defined]
        except queue.Empty:
            return out


def test_agentic_tool_listed_and_unavailable_is_error(server):
    """enabled 时注册第 4 个工具；sidecar 不可达返回友好 isError。"""
    msg = _rpc(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert msg["result"]["protocolVersion"] == "2025-06-18"
    _rpc(server, {"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)

    msg = _rpc(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in msg["result"]["tools"]]
    assert names == [
        "query_knowledge_hub",
        "list_collections",
        "get_document_summary",
        "agentic_search",
    ]

    msg = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "agentic_search", "arguments": {"query": "bm25 scoring"}},
        },
    )
    assert "error" not in msg
    result = msg["result"]
    assert result.get("isError") is True
    assert "unavailable" in result["content"][0]["text"]
    assert result["structuredContent"]["error_type"] == "AgenticSearchBackendError"


def test_out_of_allowlist_paths_structured_32602(server):
    _rpc(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    msg = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "agentic_search",
                "arguments": {"query": "q", "paths": ["/etc"]},
            },
        },
    )
    assert msg["error"]["code"] == -32602
    assert "allowlist" in msg["error"]["message"]


def test_stdout_purity_after_agentic_calls(server):
    """S1.7 验收：agentic 调用后 stdout 仅含 JSON-RPC 行（spec 4.2 零容忍）。"""
    _rpc(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    _rpc(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "agentic_search", "arguments": {"query": "q"}}})
    _rpc(server, {"jsonrpc": "2.0", "id": 3, "method": "shutdown"}, expect_response=False)
    server.stdin.close()
    remaining = _drain_stdout(server)
    assert server.returncode == 0
    for line in remaining:
        if line.strip():
            json.loads(line)  # 每行均为合法 JSON —— 无 print 泄漏


@pytest.mark.skipif(
    os.environ.get("RAG_AGENTIC_INTEGRATION") != "1",
    reason="需真实 sirchmunk sidecar 与 LLM API 费用：设置 RAG_AGENTIC_INTEGRATION=1 启用",
)
def test_real_sidecar_smoke(tmp_path: Path):
    """真实 sidecar 冒烟（门控）：沿用 S0.1 runbook 启动后运行。"""
    base_url = os.environ.get("SIRCHMUNK_BASE_URL", "http://127.0.0.1:8584")
    import httpx

    resp = httpx.get(f"{base_url}/api/v1/search/status", timeout=5)
    assert resp.status_code == 200
