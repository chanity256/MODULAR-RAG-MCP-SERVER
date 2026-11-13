"""摄取健壮性基线测试（INGESTION_ROBUSTNESS_SPEC 阶段 IR0.1）。

两类用例：

- **缺陷用例**（``xfail(strict=False)``）：按审计 A1–A7 断言*修复后*的期望行为；
  当前实现下预期失败，对应 IR 任务落地后自动转为 XPASS，届时去掉标记转正。
- **快照用例**（普通断言）：固化修复过程中不得回归的现有行为
  （同集合未变更跳过、chunk_id 格式、``.pdf`` 双登记、``.md`` 不登记等）。

纯离线：复用 ``test_ingestion_pipeline`` 的 fake Loader/Embedding/VectorStore，
全部持久化落在 ``tmp_path``。断言尽量走 pipeline / DocumentManager 的对外行为，
避免绑定 ``FileIntegrityChecker`` 的具体签名（IR1 会扩展它）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from core.types import make_image_id
from ingestion import IngestionPipelineError
from ingestion.document_manager import DocumentManager
from ingestion.embedding import SparseDocStats
from ingestion.pipeline import _SUPPORTED_EXTENSIONS
from ingestion.storage import BM25Indexer, ImageStorage
from libs.loader.file_integrity import SQLiteIntegrityChecker
from tests.unit.test_document_manager import FakeStore, _chunk
from tests.unit.test_ingestion_pipeline import (
    FakeLoader,
    FakeVectorStore,
    _build_pipeline,
    _write_source,
)

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest.py"
_CLI_MODULE_NAME = "scripts_ingest"
_CHUNK_ID_RE = re.compile(r"^(?P<doc>[0-9a-f]{64})_(?P<index>\d{4})_(?P<hash8>[0-9a-f]{8})$")
_PNG = b"\x89PNG\r\n\x1a\nfake-image-payload"


def _load_cli():
    # 与 test_ingest_entry 共用模块名，避免同一脚本被导入两份。
    if _CLI_MODULE_NAME in sys.modules:
        return sys.modules[_CLI_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_CLI_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_CLI_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _bm25_count(index_file: Path, doc_hash: str) -> int:
    """在独立重载的索引副本上统计 doc_hash 前缀的 chunk 数（不改动被测实例）。"""
    if not index_file.is_file():
        return 0
    return BM25Indexer(index_path=index_file).load().remove_document(doc_hash)


def _history_records(integrity) -> list[dict]:
    return list(integrity.list_processed())


def _record_path(record: dict) -> str | None:
    # v1 列名 file_path；v2 规划为 source_path，两者兼容。
    return record.get("source_path") or record.get("file_path")


# ===========================================================================
# 快照：修复期间不得回归的现有行为
# ===========================================================================


def test_snapshot_unchanged_file_same_collection_is_skipped(tmp_path):
    store = FakeVectorStore()
    pipeline, _ = _build_pipeline(tmp_path, FakeLoader(tmp_path), store)
    source = _write_source(tmp_path)

    first = pipeline.run(str(source), collection="a")
    upserts = len(store.upsert_calls)
    second = pipeline.run(str(source), collection="a")

    assert first.status == "success"
    assert second.status == "skipped"
    assert second.doc_hash == first.doc_hash
    assert len(store.upsert_calls) == upserts


def test_snapshot_force_same_content_is_idempotent(tmp_path):
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, FakeLoader(tmp_path), store)
    source = _write_source(tmp_path)

    first = pipeline.run(str(source), collection="a")
    ids_before = set(store.records)
    second = pipeline.run(str(source), collection="a", force=True)

    assert second.status == "success"
    assert set(store.records) == ids_before
    assert len(parts["bm25"]) == first.chunks
    assert parts["image_storage"].count(doc_hash=first.doc_hash) == 1


def test_snapshot_chunk_id_format_is_doc_hash_index_hash8(tmp_path):
    store = FakeVectorStore()
    pipeline, _ = _build_pipeline(tmp_path, FakeLoader(tmp_path), store)
    source = _write_source(tmp_path)

    result = pipeline.run(str(source), collection="a")

    assert store.records
    indexes = []
    # hash8 取自切分时的原文，而 record.text 可能已被 transform 改写，故只校验格式与前缀。
    for chunk_id in store.records:
        match = _CHUNK_ID_RE.match(chunk_id)
        assert match, chunk_id
        assert match["doc"] == result.doc_hash
        indexes.append(int(match["index"]))
    assert sorted(indexes) == list(range(len(indexes)))


def test_snapshot_failed_run_is_not_skipped_next_time(tmp_path):
    store = FakeVectorStore()
    pipeline, _ = _build_pipeline(tmp_path, FakeLoader(tmp_path, fail=True), store)
    source = _write_source(tmp_path)
    with pytest.raises(IngestionPipelineError):
        pipeline.run(str(source), collection="a")

    healthy, _ = _build_pipeline(tmp_path, FakeLoader(tmp_path), store)
    result = healthy.run(str(source), collection="a")
    assert result.status == "success"


def test_snapshot_pdf_and_markdown_registered():
    """IR7.3 翻转：``.md``/``.markdown`` 转为登记，两处清单保持一致。"""
    cli = _load_cli()
    assert ".pdf" in _SUPPORTED_EXTENSIONS
    assert ".pdf" in cli.SUPPORTED_EXTENSIONS
    for ext in (".md", ".markdown"):
        assert ext in _SUPPORTED_EXTENSIONS
        assert ext in cli.SUPPORTED_EXTENSIONS
    assert set(cli.SUPPORTED_EXTENSIONS) == set(_SUPPORTED_EXTENSIONS)


def test_snapshot_delete_single_version_document_clears_all_stores(tmp_path):
    doc = "cc" * 32
    store = FakeStore()
    store.upsert([
        _chunk(f"{doc}_0000_deadbeef", "docs/c.pdf", "alpha", doc, "Doc C"),
        _chunk(f"{doc}_0001_feedface", "docs/c.pdf", "alpha", doc, "Doc C"),
    ])
    indexer = BM25Indexer(index_path=tmp_path / "bm25.json").build([
        SparseDocStats(chunk_id=f"{doc}_0000_deadbeef", term_freqs={"x": 1}, length=1),
        SparseDocStats(chunk_id=f"{doc}_0001_feedface", term_freqs={"y": 1}, length=1),
    ])
    images = ImageStorage(images_root=str(tmp_path / "images"), db_path=str(tmp_path / "images.db"))
    images.put(f"{doc}_0_0", _PNG, collection="alpha", doc_hash=doc, page_num=0)
    integrity = SQLiteIntegrityChecker(str(tmp_path / "history.db"))
    integrity.mark_success(doc, "docs/c.pdf")
    manager = DocumentManager(
        chroma_store=store, bm25_indexer=indexer, image_storage=images, file_integrity=integrity
    )

    result = manager.delete_document("docs/c.pdf", "alpha")

    assert result.chroma_deleted == 2
    assert result.bm25_deleted == 2
    assert result.images_deleted == 1
    assert result.integrity_removed is True
    assert len(indexer) == 0
    assert not [r for r in _history_records(integrity) if _record_path(r) == "docs/c.pdf"]


# ===========================================================================
# A1：should_skip 不看 collection（跨 collection 误跳过）
# ===========================================================================


def test_a1_same_file_into_second_collection_is_not_skipped(tmp_path):
    store = FakeVectorStore()
    pipeline, _ = _build_pipeline(tmp_path, FakeLoader(tmp_path), store)
    source = _write_source(tmp_path)

    first = pipeline.run(str(source), collection="a")
    second = pipeline.run(str(source), collection="b")

    assert first.status == "success"
    assert second.status == "success"
    assert second.chunks == first.chunks
    assert any(r.metadata.get("collection") == "b" for r in store.records.values())


# ===========================================================================
# A2：文件修改后旧版本 chunk 残留
# ===========================================================================


def test_a2_modified_file_reingest_leaves_no_old_version(tmp_path):
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, FakeLoader(tmp_path), store)
    source = _write_source(tmp_path)
    index_file = tmp_path / "db" / "bm25" / "index.json"

    first = pipeline.run(str(source), collection="a")
    source.write_bytes(b"source-document-bytes-v2")
    second = pipeline.run(str(source), collection="a")

    assert second.status == "success"
    assert second.doc_hash != first.doc_hash
    assert {cid.split("_", 1)[0] for cid in store.records} == {second.doc_hash}
    assert len(store.records) == second.chunks
    assert _bm25_count(index_file, first.doc_hash) == 0
    assert _bm25_count(index_file, second.doc_hash) == second.chunks
    assert parts["image_storage"].count(doc_hash=first.doc_hash) == 0
    assert parts["image_storage"].count(doc_hash=second.doc_hash) == 1


# ===========================================================================
# A3：_doc_hash_of 只取第一个哈希（混合版本删除不彻底）
# ===========================================================================


@pytest.fixture
def two_generation_stack(tmp_path: Path):
    old, new = "aa" * 32, "ab" * 32
    store = FakeStore()
    store.upsert([
        _chunk(f"{old}_0000_deadbeef", "docs/a.pdf", "alpha", old, "Doc A v1"),
        _chunk(f"{old}_0001_feedface", "docs/a.pdf", "alpha", old, "Doc A v1"),
        _chunk(f"{new}_0000_cafebabe", "docs/a.pdf", "alpha", new, "Doc A v2"),
    ])
    indexer = BM25Indexer(index_path=tmp_path / "bm25.json").build([
        SparseDocStats(chunk_id=f"{old}_0000_deadbeef", term_freqs={"old": 1}, length=1),
        SparseDocStats(chunk_id=f"{old}_0001_feedface", term_freqs={"old": 1}, length=1),
        SparseDocStats(chunk_id=f"{new}_0000_cafebabe", term_freqs={"new": 1}, length=1),
    ])
    images = ImageStorage(images_root=str(tmp_path / "images"), db_path=str(tmp_path / "images.db"))
    images.put(f"{old}_0_0", _PNG, collection="alpha", doc_hash=old, page_num=0)
    images.put(f"{new}_0_0", _PNG, collection="alpha", doc_hash=new, page_num=0)
    integrity = SQLiteIntegrityChecker(str(tmp_path / "history.db"))
    integrity.mark_success(old, "docs/a.pdf")
    integrity.mark_success(new, "docs/a.pdf")
    manager = DocumentManager(
        chroma_store=store, bm25_indexer=indexer, image_storage=images, file_integrity=integrity
    )
    return {
        "manager": manager,
        "store": store,
        "indexer": indexer,
        "images": images,
        "integrity": integrity,
        "old": old,
        "new": new,
    }


def test_a3_snapshot_chroma_side_already_covers_both_generations(two_generation_stack):
    stack = two_generation_stack
    result = stack["manager"].delete_document("docs/a.pdf", "alpha")
    assert result.chroma_deleted == 3
    assert stack["store"].get_by_metadata({"source_path": "docs/a.pdf"}) == []


def test_a3_delete_mixed_generation_document_clears_every_hash(two_generation_stack):
    stack = two_generation_stack

    result = stack["manager"].delete_document("docs/a.pdf", "alpha")

    assert result.bm25_deleted == 3
    assert len(stack["indexer"]) == 0
    assert stack["images"].count(doc_hash=stack["old"]) == 0
    assert stack["images"].count(doc_hash=stack["new"]) == 0
    assert result.images_deleted == 2
    assert not [r for r in _history_records(stack["integrity"]) if _record_path(r) == "docs/a.pdf"]


# ===========================================================================
# A4：无删除检测 / 无重试 / 无版本元数据
# ===========================================================================


def test_a4_success_record_carries_version_metadata(tmp_path):
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, FakeLoader(tmp_path), store)
    source = _write_source(tmp_path)

    pipeline.run(str(source), collection="a")

    records = [r for r in _history_records(parts["integrity"]) if _record_path(r)]
    assert records
    for key in ("parser_version", "chunk_version", "embedding_version"):
        assert records[0].get(key), key


def test_a4_cli_exposes_sync_prune_dry_run():
    parser = _load_cli().build_parser()
    for flag in ("--sync", "--prune"):
        args = parser.parse_args(["--path", "data", flag, "--dry-run"])
        assert getattr(args, flag.lstrip("-")) is True
        assert args.dry_run is True


def test_a4_cli_exposes_retry_failed():
    parser = _load_cli().build_parser()
    # 未定 --retry-failed 是否仍要求 --path：两种 CLI 形态都接受。
    try:
        args = parser.parse_args(["--retry-failed"])
    except SystemExit:
        args = parser.parse_args(["--path", "data", "--retry-failed"])
    assert args.retry_failed is True


# ===========================================================================
# A5：历史表没有 failed_stage
# ===========================================================================


def test_a5_failed_record_carries_failed_stage(tmp_path):
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, FakeLoader(tmp_path, fail=True), store)
    source = _write_source(tmp_path)

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source), collection="a")

    assert exc_info.value.stage == "load"
    failed = [r for r in _history_records(parts["integrity"]) if r.get("status") == "failed"]
    assert failed
    assert failed[0].get("failed_stage") == "load"


# ===========================================================================
# A6：只支持 .pdf（IR5.2 已修复）
# ===========================================================================


@pytest.mark.parametrize("ext", [".docx", ".pptx", ".xlsx", ".html"])
def test_a6_markitdown_extensions_are_dual_registered(ext):
    cli = _load_cli()
    assert ext in _SUPPORTED_EXTENSIONS
    assert ext in cli.SUPPORTED_EXTENSIONS


# ===========================================================================
# A7：store 阶段部分失败无回滚
# ===========================================================================


class _FailingUpsertStore(FakeVectorStore):
    def upsert(self, records, *, trace=None, **kwargs) -> int:
        raise RuntimeError("injected vector upsert failure")


def test_a7_snapshot_store_failure_surfaces_stage_error(tmp_path):
    pipeline, _ = _build_pipeline(tmp_path, FakeLoader(tmp_path), _FailingUpsertStore())
    source = _write_source(tmp_path)

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source), collection="a")

    assert exc_info.value.stage == "store"
    assert "injected vector upsert failure" in str(exc_info.value)


def test_a7_vector_failure_rolls_back_bm25_and_images(tmp_path):
    pipeline, parts = _build_pipeline(tmp_path, FakeLoader(tmp_path), _FailingUpsertStore())
    source = _write_source(tmp_path)
    doc_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    index_file = tmp_path / "db" / "bm25" / "index.json"

    with pytest.raises(IngestionPipelineError):
        pipeline.run(str(source), collection="a")

    assert _bm25_count(index_file, doc_hash) == 0
    assert parts["image_storage"].count(doc_hash=doc_hash) == 0


def test_a7_image_failure_rolls_back_bm25_and_vectors(tmp_path, monkeypatch):
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, FakeLoader(tmp_path), store)
    source = _write_source(tmp_path)
    doc_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    index_file = tmp_path / "db" / "bm25" / "index.json"

    def _boom(*args, **kwargs):
        raise RuntimeError("injected image put failure")

    monkeypatch.setattr(parts["image_storage"], "put", _boom)

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source), collection="a")

    assert exc_info.value.stage == "store"
    assert _bm25_count(index_file, doc_hash) == 0
    assert not [cid for cid in store.records if cid.startswith(f"{doc_hash}_")]
    assert parts["image_storage"].get(make_image_id(doc_hash, 0, 0)) is None
