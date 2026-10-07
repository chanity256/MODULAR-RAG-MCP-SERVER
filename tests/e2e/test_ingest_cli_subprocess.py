"""``scripts/ingest.py`` 真子进程冒烟（IR3.2 同步模式 E2E）。

以 ``python scripts/ingest.py ...`` 真实子进程运行 CLI，cwd 设为 ``tmp_path``
并显式传入 tmp 配置文件：历史表 / 图片索引等以 CWD 相对路径落盘的存储
全部落在 tmp 下，Chroma ``persist_path`` 也指向 tmp，仓库 ``data/`` 零触碰。

离线保证：dry-run 只计算哈希并读取已有存储，不构建 embedding/LLM；
配置中的 embedding ``base_url`` 指向不可达的本地端口，代理变量也指向它，
任何意外的网络调用都会失败而不是真的出网。fixture 文件只需字节内容（dry-run
不解析文档）。

覆盖：
- 全新安装：``--sync --dry-run`` 退出 0、打印计划、不创建任何存储文件。
- 已有存储：预置历史表 + Chroma 记录，``--sync --dry-run`` / ``--prune --dry-run``
  正确分类 new / unchanged / deleted，且历史表等文件逐字节不变、Chroma 记录
  逻辑不变（Chroma 字节层面不稳定的原因见 ``_snapshot``）。
- argparse 用法错误（互斥模式、``--dry-run`` 单独使用、缺 ``--path``）退出 2。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.e2e

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ingest.py"
_UNREACHABLE = "http://127.0.0.1:9"


def _write_settings(tmp_path: Path) -> Path:
    config = tmp_path / "settings.yaml"
    persist = (tmp_path / "data" / "db" / "chroma").as_posix()
    config.write_text(
        dedent(
            f"""\
            llm:
              provider: openai
              model: offline-fake
            embedding:
              provider: openai
              model: offline-embed
              api_key: sk-offline
              base_url: {_UNREACHABLE}
            vision_llm:
              provider: azure
              model: offline-fake
            vector_store:
              backend: chroma
              persist_path: {persist}
            splitter:
              provider: recursive
              chunk_size: 1000
              chunk_overlap: 200
            retrieval:
              sparse_backend: bm25
              fusion_algorithm: rrf
              top_k_dense: 20
              top_k_sparse: 20
              top_k_final: 10
            rerank:
              backend: none
            evaluation:
              backends: [custom]
            observability:
              enabled: false
            """
        ),
        encoding="utf-8",
    )
    return config


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


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=str(cwd),
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _make_docs(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    (docs / "sub").mkdir(parents=True)
    (docs / "alpha.pdf").write_bytes(b"%PDF-1.4 alpha offline fixture")
    (docs / "sub" / "beta.docx").write_bytes(b"beta offline fixture")
    # IR7.3 起 .md 已登记，不受支持扩展名的哨兵改用 .txt
    (docs / "notes.txt").write_text("not a supported extension", encoding="utf-8")
    return docs


def _snapshot(root: Path) -> dict[str, str]:
    """非 Chroma 存储文件的字节快照。

    Chroma 目录排除在外：chromadb 1.x 每次打开 PersistentClient 都会写内部
    锁表，并可能把日志压实进 HNSW 段文件，字节层面天然不稳定；其业务数据
    改由 :func:`_chroma_records` 逻辑比较。
    """
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "chroma" not in p.relative_to(root).parts
    }


_DUMP_SCRIPT = dedent(
    """\
    import json, sys
    sys.path.insert(0, sys.argv[1])
    from core.settings import load_settings
    from libs.vector_store import VectorStoreFactory

    store = VectorStoreFactory.create(load_settings(sys.argv[2]))
    rows = [
        {"id": r.get("id"), "text": r.get("text"), "metadata": r.get("metadata")}
        for r in store.get_by_metadata(None)
    ]
    print(json.dumps(sorted(rows, key=lambda r: str(r["id"])), sort_keys=True, default=str))
    """
)


def _chroma_records(tmp_path: Path, config: Path) -> list[dict]:
    result = subprocess.run(
        [sys.executable, "-c", _DUMP_SCRIPT, str(_REPO_ROOT / "src"), str(config)],
        cwd=str(tmp_path),
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


_SEED_SCRIPT = dedent(
    """\
    import sys
    sys.path.insert(0, sys.argv[1])
    from core.settings import load_settings, resolve_ingestion_versions
    from libs.loader.file_integrity import SQLiteIntegrityChecker
    from libs.vector_store import VectorRecord, VectorStoreFactory

    config, unchanged, deleted, collection = sys.argv[2:6]
    settings = load_settings(config)
    emb = resolve_ingestion_versions(settings)["embedding_version"]
    history = SQLiteIntegrityChecker()
    history.mark_success(
        history.compute_sha256(unchanged), unchanged,
        collection=collection, embedding_version=emb,
    )
    history.mark_success("0" * 64, deleted, collection=collection, embedding_version=emb)
    history.close()
    store = VectorStoreFactory.create(settings)
    meta = {"collection": collection, "doc_hash": "0" * 64}
    store.upsert([
        VectorRecord(id="gone-0", vector=[0.1, 0.2, 0.3], text="gone",
                     metadata={**meta, "source_path": deleted}),
        VectorRecord(id="kept-0", vector=[0.3, 0.2, 0.1], text="kept",
                     metadata={**meta, "source_path": unchanged}),
    ])
    """
)


def _seed_stores(tmp_path: Path, config: Path, unchanged: Path, deleted: Path, collection: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _SEED_SCRIPT,
            str(_REPO_ROOT / "src"),
            str(config),
            str(unchanged),
            str(deleted),
            collection,
        ],
        cwd=str(tmp_path),
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(autouse=True)
def _repo_data_untouched():
    def stat_snapshot() -> dict[str, tuple[int, int]]:
        root = _REPO_ROOT / "data"
        if not root.exists():
            return {}
        return {
            str(p): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*")
            if p.is_file()
        }

    before = stat_snapshot()
    yield
    assert stat_snapshot() == before


def test_sync_dry_run_fresh_install_prints_plan_and_writes_nothing(tmp_path):
    config = _write_settings(tmp_path)
    docs = _make_docs(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))

    result = _run(
        ["--path", str(docs), "--collection", "c", "--sync", "--dry-run", "--config", str(config)],
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "sync plan (dry-run, nothing written): collection='c'" in out
    assert "ingest  (new)      : 2" in out
    assert "replace (modified) : 0" in out
    assert "prune   (deleted)  : 0" in out
    assert str(docs / "alpha.pdf") in out
    assert str(docs / "sub" / "beta.docx") in out
    assert "notes.txt" not in out
    after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert after == before
    assert not (tmp_path / "data").exists()


def test_dry_run_against_existing_stores_classifies_and_leaves_bytes_unchanged(tmp_path):
    config = _write_settings(tmp_path)
    docs = _make_docs(tmp_path)
    unchanged = docs / "alpha.pdf"
    deleted = docs / "removed.pdf"
    _seed_stores(tmp_path, config, unchanged, deleted, "c")
    data_before = _snapshot(tmp_path / "data")
    assert any(name.endswith("ingestion_history.db") for name in data_before)
    records_before = _chroma_records(tmp_path, config)
    assert {r["id"] for r in records_before} == {"gone-0", "kept-0"}

    sync = _run(
        ["--path", str(docs), "--collection", "c", "--sync", "--dry-run", "--config", str(config)],
        cwd=tmp_path,
    )
    assert sync.returncode == 0, sync.stdout + sync.stderr
    assert "ingest  (new)      : 1" in sync.stdout
    assert f"+ {docs / 'sub' / 'beta.docx'}" in sync.stdout
    assert "skip    (unchanged): 1" in sync.stdout
    assert f"= {unchanged}" in sync.stdout
    assert "prune   (deleted)  : 1" in sync.stdout
    assert f"- {deleted}" in sync.stdout

    prune = _run(
        ["--path", str(docs), "--collection", "c", "--prune", "--dry-run", "--config", str(config)],
        cwd=tmp_path,
    )
    assert prune.returncode == 0, prune.stdout + prune.stderr
    assert "prune plan (dry-run, nothing written)" in prune.stdout
    assert "prune   (deleted)  : 1" in prune.stdout
    assert "ingest  (new)" not in prune.stdout

    other = _run(
        ["--path", str(docs), "--collection", "other", "--sync", "--dry-run", "--config", str(config)],
        cwd=tmp_path,
    )
    assert other.returncode == 0, other.stdout + other.stderr
    assert "ingest  (new)      : 2" in other.stdout
    assert "prune   (deleted)  : 0" in other.stdout

    assert _snapshot(tmp_path / "data") == data_before
    assert _chroma_records(tmp_path, config) == records_before


@pytest.mark.parametrize(
    "argv",
    [
        ["--path", "docs", "--sync", "--prune"],
        ["--path", "docs", "--dry-run"],
        ["--sync"],
    ],
    ids=["sync-and-prune-mutually-exclusive", "dry-run-without-mode", "sync-without-path"],
)
def test_usage_errors_exit_2(tmp_path, argv):
    _make_docs(tmp_path)
    result = _run(argv, cwd=tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "usage:" in result.stderr
    assert not (tmp_path / "data").exists()
