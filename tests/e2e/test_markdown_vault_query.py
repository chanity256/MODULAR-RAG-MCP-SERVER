"""IR7.5：Markdown vault E2E —— 摄取 → ``scripts/query.py`` 命中 → MCP tools/call 引用笔记路径。

离线策略（对齐 test_mcp_client / test_data_ingestion 先例）：
- 摄取在测试进程内以真实组件执行：真 MarkdownLoader（pipeline 扩展名路由）、
  真 ChromaStore（嵌入式 PersistentClient）/ BM25Indexer / ImageStorage /
  SQLiteIntegrityChecker，全部落 ``tmp_path``；唯一注入确定性 HashEmbedding
  （零网络）。
- 检索以真子进程运行 ``scripts/query.py``（cwd=临时 workspace，``--config``
  指向 workspace 配置）：embedding ``base_url`` 指向关闭端口 → Dense 路 D5
  降级 Sparse（BM25），关键词命中真实笔记。
- MCP server 以子进程启动（``PYTHONPATH=src python -m mcp_server.server``），
  同一 workspace 配置；``tools/call query_knowledge_hub`` 返回 citations，
  ``source`` 为笔记路径（citation 口径 = metadata.source_path）。
- workspace 配置对子进程同样指向不可达 embedding 端口、代理变量也指向它：
  任何意外出网都会失败而不是真的请求。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from ingestion.pipeline import IngestionPipeline
from ingestion.storage import BM25Indexer, ImageStorage, VectorUpserter
from libs.embedding.base_embedding import BaseEmbedding
from libs.loader.file_integrity import SQLiteIntegrityChecker
from libs.vector_store.vector_store_factory import VectorStoreFactory

pytestmark = pytest.mark.e2e

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"
_VAULT = _REPO_ROOT / "tests" / "fixtures" / "vault"
_INGEST_SCRIPT = _REPO_ROOT / "scripts" / "ingest.py"
_QUERY_SCRIPT = _REPO_ROOT / "scripts" / "query.py"
_UNREACHABLE = "http://127.0.0.1:1"

_COLLECTION = "vault"


class HashEmbedding(BaseEmbedding):
    """确定性哈希向量（同文本同向量，dim=16），零网络。"""

    def __init__(self, dimension: int = 16) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts, *, trace=None, **kwargs):
        return [
            [
                round(
                    int(hashlib.sha256(t.encode()).hexdigest()[i * 2 : i * 2 + 2], 16) % 97 / 97.0,
                    6,
                )
                for i in range(self._dimension)
            ]
            for t in texts
        ]


def _write_settings(workspace: Path) -> Path:
    """workspace 级配置：Chroma 落 workspace、embedding 指向关闭端口。

    MCP server 按 cwd 相对 ``config/settings.yaml`` 读取；``scripts/query.py``
    经 ``--config`` 显式传入同一份。
    """
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = config_dir / "settings.yaml"
    config.write_text(
        dedent(
            f"""\
            llm:
              provider: azure
              model: gpt-4o
            embedding:
              provider: openai
              model: text-embedding-3-small
              base_url: {_UNREACHABLE}   # Dense 路快速失败 → D5 降级 Sparse
            vision_llm:
              provider: azure
              model: gpt-4o
            vector_store:
              backend: chroma
              persist_path: {(workspace / "data" / "db" / "chroma").as_posix()}
            splitter:
              provider: recursive
              chunk_size: 200
              chunk_overlap: 40
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
            ingestion:
              chunk_refiner:
                use_llm: false
              metadata_enricher:
                use_llm: false
              image_captioner:
                enabled: false
              batch_size: 4
            observability:
              enabled: false
            """
        ),
        encoding="utf-8",
    )
    return config


def _ingest_vault(workspace: Path, vault: Path) -> None:
    """真组件（真 MarkdownLoader + 真 Chroma/BM25）摄取 vault，唯一注入 embedding。"""
    settings = load_settings(workspace / "config" / "settings.yaml")
    store = VectorStoreFactory.create(settings)
    embedding = HashEmbedding()
    bm25 = BM25Indexer(settings, index_path=workspace / "data" / "db" / "bm25" / "index.json")
    integrity = SQLiteIntegrityChecker(
        db_path=workspace / "data" / "db" / "ingestion_history.db"
    )
    image_storage = ImageStorage(
        images_root=str(workspace / "data" / "images"),
        db_path=str(workspace / "data" / "db" / "image_index.db"),
    )
    pipeline = IngestionPipeline(
        settings,
        integrity_checker=integrity,
        dense_encoder=DenseEncoder(settings, embedding),
        sparse_encoder=SparseEncoder(settings),
        batch_processor=BatchProcessor(settings),
        bm25_indexer=bm25,
        vector_upserter=VectorUpserter(settings, store=store),
        image_storage=image_storage,
    )
    lines: list[str] = []
    code = _cli().main(
        ["--path", str(vault), "--collection", _COLLECTION],
        pipeline=pipeline,
        out=lines.append,
    )
    assert code == 0, "\n".join(lines)
    joined = "\n".join(lines)
    assert "6 total | 6 ingested | 0 skipped | 0 failed" in joined
    assert "filtered: 2 (excluded by source_filters)" in joined


def _cli():
    name = "scripts_ingest_ir75_vault_e2e"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _INGEST_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("ZAI_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"):
        env.pop(key, None)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[key] = _UNREACHABLE
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    env["ANONYMIZED_TELEMETRY"] = "False"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_query_cli(workspace: Path, query: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_QUERY_SCRIPT),
            "--query",
            query,
            "--no-rerank",
            "--config",
            str(workspace / "config" / "settings.yaml"),
        ],
        cwd=str(workspace),  # BM25 默认 data/db/bm25/index.json 相对 cwd 命中
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


@pytest.fixture(scope="module")
def vault_workspace(tmp_path_factory):
    """摄取一次的 workspace：vault 副本 + 真实 Chroma/BM25 产物 + 配置。"""
    workspace = tmp_path_factory.mktemp("vault_e2e")
    vault = workspace / "vault"
    shutil.copytree(_VAULT, vault)
    _write_settings(workspace)
    _ingest_vault(workspace, vault)
    return workspace


# ---------------------------------------------------------------------------
# E2E-1：vault 摄取 → scripts/query.py 检索命中预期笔记
# ---------------------------------------------------------------------------


def test_query_cli_hits_note_by_keyword(vault_workspace):
    """英文关键词命中唯一笔记，引用 source 为笔记路径（Dense 降级 Sparse 路）。"""
    result = _run_query_cli(vault_workspace, "hello")
    assert result.returncode == 0, result.stderr
    sources = [line for line in result.stdout.splitlines() if "source=" in line]
    assert sources, result.stdout
    inline_hits = [s for s in sources if s.endswith("inline-tags.md")]
    assert inline_hits, result.stdout  # ``#hello`` 只出现在 inline-tags.md
    assert all("source=" in s for s in sources)
    # 命中来源均为笔记路径（.md），非模板/非 .obsidian
    assert all("/.obsidian/" not in s and "/templates/" not in s for s in sources)

    # 中文关键词（CJK 逐字分词）命中标题/正文包含该词的笔记
    result2 = _run_query_cli(vault_workspace, "fallback")
    assert result2.returncode == 0, result2.stderr
    fallback_hits = [
        line for line in result2.stdout.splitlines() if "source=" in line
    ]
    assert any(s.endswith("bad-frontmatter.md") for s in fallback_hits), result2.stdout


# ---------------------------------------------------------------------------
# E2E-2：子进程 MCP server tools/call 引用笔记路径
# ---------------------------------------------------------------------------


def _rpc(proc: subprocess.Popen, message: dict, *, expect_response: bool = True) -> dict | None:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    if not expect_response:
        return None
    return json.loads(proc.stdout.readline())


def test_mcp_query_knowledge_hub_cites_note_path(vault_workspace):
    """子进程 MCP server：query_knowledge_hub 的 citations.source 为笔记路径。"""
    workspace = vault_workspace
    env = _env()
    env["PYTHONPATH"] = str(_SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(workspace),  # load_settings() 读 cwd 相对 config/settings.yaml
        text=True,
    )
    try:
        # initialize 握手。
        msg = _rpc(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            },
        )
        assert msg["result"]["protocolVersion"] == "2025-06-18"
        _rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)

        # tools/call query_knowledge_hub：Sparse 降级路命中笔记，source 为笔记路径。
        msg = _rpc(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "query_knowledge_hub",
                    "arguments": {"query": "hello", "top_k": 3},
                },
            },
        )
        assert "error" not in msg, msg
        result = msg["result"]
        assert not result.get("isError", False)
        citations = result["structuredContent"]["citations"]
        assert citations, "expected citations from sparse-degraded retrieval"
        assert citations[0]["source"].endswith("notes/inline-tags.md")
        assert all(citation["source"].endswith(".md") for citation in citations)
        assert all("/.obsidian/" not in citation["source"] for citation in citations)
        assert all("/templates/" not in citation["source"] for citation in citations)
        assert "[1]" in result["content"][0]["text"]

        # shutdown：干净退出，stdout 无日志污染。
        _rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "shutdown"}, expect_response=False)
        proc.stdin.close()
        stdout, _stderr = proc.communicate(timeout=15)
        assert proc.returncode == 0
        assert [ln for ln in stdout.splitlines() if ln.strip()] == []
    finally:
        if proc.poll() is None:  # pragma: no cover - 失败路径兜底清理
            proc.terminate()
            proc.wait(timeout=5)
