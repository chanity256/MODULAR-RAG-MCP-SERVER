"""E2E：MCP Client 侧调用模拟（阶段 I1）。

以子进程启动真实 MCP server（stdio transport），从 client 视角
完整走一遍：``initialize`` → ``notifications/initialized`` →
``tools/list`` → ``tools/call``（query_knowledge_hub 返回 citations、
list_collections 返回集合）→ ``shutdown``。

离线策略（零网络）：
- 子进程 cwd 为临时 workspace，``config/settings.yaml`` 指向本地
  Chroma/BM25 产物（测试预置真实数据）。
- embedding ``base_url`` 指向本机关闭端口（连接即刻拒绝）→ Dense 路
  按 D5 降级策略退到 Sparse（BM25）路，仍返回真实检索结果与 citations。
- rerank backend=none（跳过精排）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from ingestion.embedding import SparseDocStats
from ingestion.embedding.sparse_encoder import default_tokenize
from ingestion.storage import BM25Indexer
from libs.vector_store.base_vector_store import VectorRecord
from libs.vector_store.chroma_store import ChromaStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"

_COLLECTION = "e2e_docs"

_CORPUS = {
    "doc_bm_0000_a1": (
        "bm25 inverted index scoring with term frequency",
        {"collection": _COLLECTION, "source_path": "bm25_guide.pdf", "page": 1},
    ),
    "doc_az_0000_b2": (
        "azure openai configuration endpoint and api key",
        {"collection": _COLLECTION, "source_path": "azure_setup.pdf", "page": 3},
    ),
    "doc_in_0000_c3": (
        "incremental ingestion file hash sha256 skip",
        {"collection": _COLLECTION, "source_path": "ingest_guide.pdf", "page": 2},
    ),
}

_SETTINGS_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
      base_url: http://127.0.0.1:1   # 关闭端口：Dense 路快速失败 → 降级 Sparse
    vision_llm:
      provider: azure
      model: gpt-4o
    vector_store:
      backend: chroma
      persist_path: ./data/db/chroma
      collection: e2e_docs
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
      top_k_dense: 10
      top_k_sparse: 10
      top_k_final: 5
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    observability:
      enabled: true
      log_file: ./logs/traces.jsonl
    """
)


def _seed_workspace(tmp_path: Path) -> None:
    """预置离线检索产物：Chroma 向量 + BM25 索引 + server 配置。"""
    from core.settings import load_settings

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text(_SETTINGS_YAML, encoding="utf-8")
    settings = load_settings(tmp_path / "config" / "settings.yaml")
    # 测试进程 cwd 是仓库根：persist_path 必须转绝对路径，
    # 否则向量会误写入仓库 data/db/chroma（子进程 cwd=tmp 不受影响）。
    settings = replace(
        settings,
        vector_store=replace(settings.vector_store, persist_path=str(tmp_path / "data" / "db" / "chroma")),
    )

    store = ChromaStore(settings.vector_store)
    records, stats = [], []
    for chunk_id, (text, metadata) in _CORPUS.items():
        # Dense 路会被降级，向量内容不影响 Sparse 命中，仅需可入库。
        vector = [float(len(text) % 97) / 97.0] * 8
        records.append(VectorRecord(id=chunk_id, vector=vector, text=text, metadata=dict(metadata)))
        tokens = default_tokenize(text)
        stats.append(SparseDocStats(chunk_id=chunk_id, term_freqs=dict(Counter(tokens)), length=len(tokens)))
    store.upsert(records)

    indexer = BM25Indexer(index_path=tmp_path / "data" / "db" / "bm25" / "index.json")
    indexer.build(stats)
    indexer.save()


@pytest.fixture()
def server(tmp_path: Path):
    _seed_workspace(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(tmp_path),  # load_settings() 读 cwd 相对 config/settings.yaml
        text=True,
    )
    yield proc
    if proc.poll() is None:  # pragma: no cover - 兜底清理
        proc.terminate()
        proc.wait(timeout=5)


def _rpc(proc: subprocess.Popen, message: dict, *, expect_response: bool = True) -> dict | None:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    if not expect_response:
        return None
    return json.loads(proc.stdout.readline())


def test_mcp_client_full_walkthrough(server):
    """I1 验收：完整走通 query_knowledge_hub 并返回 citations。"""
    # 1) initialize 握手。
    msg = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
    )
    assert msg["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in msg["result"]["capabilities"]

    # 2) initialized 通知（无响应）。
    _rpc(
        server,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        expect_response=False,
    )

    # 3) tools/list：三个 tool 齐全。
    msg = _rpc(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert [t["name"] for t in msg["result"]["tools"]] == [
        "query_knowledge_hub",
        "list_collections",
        "get_document_summary",
    ]

    # 4) tools/call query_knowledge_hub：Dense 降级 Sparse 后仍返回真实结果。
    msg = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "query_knowledge_hub",
                "arguments": {"query": "bm25 scoring term frequency", "top_k": 3},
            },
        },
    )
    assert "error" not in msg, msg
    result = msg["result"]
    assert not result.get("isError", False)
    content = result["content"]
    assert content[0]["type"] == "text"
    assert "[1]" in content[0]["text"]
    citations = result["structuredContent"]["citations"]
    assert citations, "expected citations from sparse-degraded retrieval"
    assert citations[0]["chunk_id"] == "doc_bm_0000_a1"
    assert citations[0]["source"] == "bm25_guide.pdf"

    # 5) tools/call list_collections。
    msg = _rpc(
        server,
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_collections", "arguments": {}}},
    )
    assert "error" not in msg, msg
    collections = msg["result"]["structuredContent"]["collections"]
    assert any(c["name"] == _COLLECTION for c in collections)

    # 6) shutdown 干净退出（无响应；此前响应已逐行读取，剩余 stdout 应为空）。
    _rpc(server, {"jsonrpc": "2.0", "id": 5, "method": "shutdown"}, expect_response=False)
    server.stdin.close()
    stdout, _stderr = server.communicate(timeout=15)
    assert server.returncode == 0
    # shutdown 不产生响应，且无任何日志污染 stdout。
    assert [ln for ln in stdout.splitlines() if ln.strip()] == []


def test_mcp_client_empty_query_returns_32602(server):
    """Client 传非法参数得到结构化 -32602 错误（校验先于检索栈）。"""
    msg = _rpc(
        server,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "query_knowledge_hub", "arguments": {"query": ""}},
        },
    )
    assert msg["error"]["code"] == -32602
