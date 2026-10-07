"""IR7.5：Markdown 摄取幂等（重跑全 skipped / ``--force`` 替换 / 单文件修改）。

纯离线：真实 MarkdownLoader（经 pipeline 扩展名路由）+ fake embedding +
FakeVectorStore；历史表 / BM25 JSON / 图片索引落 ``tmp_path``；vault 夹具
每次复制到 ``tmp_path`` 后摄取，仓库 ``data/`` 零触碰。

覆盖（对齐 INGESTION_ROBUSTNESS_SPEC 阶段 IR7.5 幂等验收）：
- 重跑全 skipped：目录重摄取 6 篇笔记全部 skipped，存储 chunk 集合不变、
  BM25 数量不变、历史表不新增记录。
- ``--force`` 幂等替换：确定性 chunk_id 覆盖写，无旧版本残留
  （每个笔记的 chunk_id 前缀 == 当前文件 SHA256）、BM25 ``num_docs`` 不变。
- 修改单文件仅该文件重摄取：其余笔记 chunk_id 不变；旧 doc_hash 的 chunk
  在 Chroma 与 BM25 中均不残留（对齐 IR2 版本替换语义）。
- ``--sync`` 只重摄取 modified 的单文件（对齐 IR3 同步语义，计划行 ``~``）。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from ingestion.storage import BM25Indexer
from tests.unit.test_ingest_source_filters import cli
from tests.unit.test_ingestion_loader_routing import _build_routing_pipeline
from tests.unit.test_ingestion_pipeline import FakeVectorStore

pytestmark = pytest.mark.unit

_VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "vault"


def _copy_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    shutil.copytree(_VAULT, vault)
    return vault


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ingest(vault: Path, pipeline, *extra: str) -> tuple[int, str]:
    lines: list[str] = []
    code = cli.main(
        ["--path", str(vault), "--collection", "vault", *extra],
        pipeline=pipeline,
        out=lines.append,
    )
    return code, "\n".join(lines)


def _ingest_with_settings(vault: Path, pipeline, *extra: str) -> tuple[int, str]:
    lines: list[str] = []
    code = cli.main(
        ["--path", str(vault), "--collection", "vault", *extra],
        pipeline=pipeline,
        settings=pipeline._settings,
        out=lines.append,
    )
    return code, "\n".join(lines)


def _chunks_by_source(store: FakeVectorStore) -> dict[str, dict[str, str]]:
    """``{source_path: {chunk_id: text}}`` 视图。"""
    by_source: dict[str, dict[str, str]] = {}
    for rec in store.records.values():
        by_source.setdefault(rec.metadata["source_path"], {})[rec.id] = rec.text
    return by_source


def _reload_bm25(tmp_path: Path) -> BM25Indexer:
    return BM25Indexer(index_path=tmp_path / "db" / "bm25" / "index.json").load()


def test_vault_rerun_all_skipped_no_duplicate_chunks(tmp_path):
    """重跑全 skipped：存储零变化（chunk 集合逐字不变、BM25/历史表不增长）。"""
    vault = _copy_vault(tmp_path)
    store = FakeVectorStore()
    pipeline, parts = _build_routing_pipeline(tmp_path, store)

    code1, out1 = _ingest(vault, pipeline)
    assert code1 == 0
    assert "6 total | 6 ingested | 0 skipped | 0 failed" in out1
    first_chunks = _chunks_by_source(store)
    assert len(first_chunks) == 6  # 6 篇笔记（.obsidian/templates 已过滤）
    assert len(store.records) == sum(len(chunks) for chunks in first_chunks.values())
    assert len(parts["bm25"]) == len(store.records)

    code2, out2 = _ingest(vault, pipeline)
    assert code2 == 0
    assert "6 total | 0 ingested | 6 skipped | 0 failed" in out2
    # 存储无重复 chunk：集合与内容与首跑完全一致。
    assert _chunks_by_source(store) == first_chunks
    assert len(store.records) == len(parts["bm25"])
    # 历史表不新增记录（仍为 6 条 success）。
    history = list(parts["integrity"].list_processed())
    assert len(history) == 6
    assert all(record.get("status") == "success" for record in history)


def test_vault_force_reingest_replaces_without_stale_chunks(tmp_path):
    """``--force`` 幂等替换：同内容重摄取后无旧版本残留、总量不变。"""
    vault = _copy_vault(tmp_path)
    store = FakeVectorStore()
    pipeline, parts = _build_routing_pipeline(tmp_path, store)

    code1, out1 = _ingest(vault, pipeline)
    assert code1 == 0
    first_chunks = _chunks_by_source(store)
    first_ids = set(store.records)

    code2, out2 = _ingest(vault, pipeline, "--force")
    assert code2 == 0
    assert "6 total | 6 ingested | 0 skipped | 0 failed" in out2
    # 确定性 chunk_id：覆盖写而非追加，无旧版本残留。
    assert set(store.records) == first_ids
    assert _chunks_by_source(store) == first_chunks
    # 每个笔记的 chunk_id 前缀 == 当前文件 SHA256（无孤儿 doc_hash 版本）。
    for source, chunks in _chunks_by_source(store).items():
        file_hash = _sha(Path(source))
        assert all(chunk_id.split("_", 1)[0] == file_hash for chunk_id in chunks)
    assert all(rec.metadata["doc_type"] == "markdown" for rec in store.records.values())
    # BM25：同 chunk_id 替换而非追加。
    assert len(_reload_bm25(tmp_path)) == len(store.records) == len(parts["bm25"])


def test_vault_modified_note_reingests_only_that_file(tmp_path):
    """修改单文件重摄取：仅该文件换版本，其余笔记 chunk_id 不变、旧 hash 不残留。"""
    vault = _copy_vault(tmp_path)
    store = FakeVectorStore()
    pipeline, parts = _build_routing_pipeline(tmp_path, store)

    code1, _ = _ingest(vault, pipeline)
    assert code1 == 0
    first_chunks = _chunks_by_source(store)

    note = vault / "notes" / "frontmatter.md"
    old_hash = _sha(note)
    note.write_text(
        note.read_text(encoding="utf-8")
        + "\n\n增量段落：IR7.5 idempotency marker paragraph。\n",
        encoding="utf-8",
    )
    new_hash = _sha(note)

    code2, out2 = _ingest(vault, pipeline)
    assert code2 == 0
    assert "6 total | 1 ingested | 5 skipped | 0 failed" in out2

    second_chunks = _chunks_by_source(store)
    # 其余 5 篇笔记 chunk_id 与内容完全不变。
    for source, chunks in first_chunks.items():
        if source == str(note):
            continue
        assert second_chunks[source] == chunks
    # 被修改笔记：全部 chunk 归属新 doc_hash，新内容可见。
    modified = second_chunks[str(note)]
    assert modified
    assert all(chunk_id.split("_", 1)[0] == new_hash for chunk_id in modified)
    assert any("IR7.5" in text for text in modified.values())
    records = [rec for rec in store.records.values() if rec.metadata["source_path"] == str(note)]
    assert records and all(rec.metadata["sha256"] == new_hash for rec in records)
    # 旧 doc_hash 的 chunk 不残留（Chroma）。
    assert not any(chunk_id.startswith(old_hash) for chunk_id in store.records)
    # 旧 doc_hash 不残留（BM25）；新版本 chunk 数与 Chroma 一致。
    reloaded = _reload_bm25(tmp_path)
    assert reloaded.remove_document(old_hash) == 0
    assert reloaded.remove_document(new_hash) == len(modified)


def test_vault_sync_reingests_only_modified_note(tmp_path):
    """``--sync``：修改单文件后同步模式只重摄取该文件（计划行 ``~``）。"""
    vault = _copy_vault(tmp_path)
    store = FakeVectorStore()
    pipeline, _parts = _build_routing_pipeline(tmp_path, store)

    code1, _ = _ingest(vault, pipeline)
    assert code1 == 0
    first_chunks = _chunks_by_source(store)

    note = vault / "notes" / "inline-tags.md"
    old_hash = _sha(note)
    note.write_text(
        note.read_text(encoding="utf-8")
        + "\n\nSync 模式增量段落：IR7.5 sync marker paragraph。\n",
        encoding="utf-8",
    )
    new_hash = _sha(note)

    # 同步模式注入 pipeline 时需显式传入 settings 才应用 source_filters
    #（对齐 test_sync_skips_filtered_files 先例；.obsidian/templates 不入计划）。
    code2, out2 = _ingest_with_settings(vault, pipeline, "--sync")
    assert code2 == 0
    # 计划只把该文件标记为 modified（~），其余 unchanged（=），无 new（+）。
    assert f"~ {note}" in out2
    assert out2.count("\n    = ") == 5
    assert "    + " not in out2
    assert "1 total | 1 ingested | 0 skipped | 0 failed" in out2

    second_chunks = _chunks_by_source(store)
    for source, chunks in first_chunks.items():
        if source == str(note):
            continue
        assert second_chunks[source] == chunks
    modified = second_chunks[str(note)]
    assert all(chunk_id.split("_", 1)[0] == new_hash for chunk_id in modified)
    assert not any(chunk_id.startswith(old_hash) for chunk_id in store.records)
    reloaded = _reload_bm25(tmp_path)
    assert reloaded.remove_document(old_hash) == 0
