"""IR1: 历史表 v2（主键 (source_path, collection)）+ 旧表自动迁移单元测试（离线，仅 SQLite）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from libs.loader.file_integrity import (
    DEFAULT_COLLECTION,
    HISTORY_TABLE,
    LEGACY_BACKUP_TABLE,
    LEGACY_TABLE,
    LEGACY_VERSION,
    SQLiteIntegrityChecker,
)

H1 = "a1" * 32
H2 = "b2" * 32
H3 = "c3" * 32


def _tables(db: Path) -> set[str]:
    with sqlite3.connect(str(db)) as conn:
        return {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }


def _create_legacy_db(db: Path, rows: list[tuple]) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(
            """
            CREATE TABLE ingestion_history (
                file_hash    TEXT PRIMARY KEY,
                file_path    TEXT,
                status       TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                error_msg    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ingestion_history_status ON ingestion_history(status);
            """
        )
        conn.executemany("INSERT INTO ingestion_history VALUES (?, ?, ?, ?, ?)", rows)


@pytest.fixture
def checker(tmp_path):
    ck = SQLiteIntegrityChecker(str(tmp_path / "db" / "ingestion_history.db"))
    yield ck
    ck.close()


# ---------------------------------------------------------------------------
# 迁移：三种初始状态
# ---------------------------------------------------------------------------


def test_fresh_install_creates_only_v2(tmp_path):
    db = tmp_path / "db" / "h.db"
    SQLiteIntegrityChecker(str(db)).close()
    tables = _tables(db)
    assert HISTORY_TABLE in tables
    assert LEGACY_TABLE not in tables
    assert LEGACY_BACKUP_TABLE not in tables


def test_legacy_table_with_success_and_failed_rows_is_migrated(tmp_path):
    db = tmp_path / "db" / "h.db"
    _create_legacy_db(
        db,
        [
            (H1, "docs/a.pdf", "success", "2026-01-01 10:00:00", None),
            (H2, "docs/b.pdf", "failed", "2026-01-02 10:00:00", "boom"),
            (H3, None, "failed", "2026-01-03 10:00:00", "no path"),
        ],
    )
    ck = SQLiteIntegrityChecker(str(db))
    try:
        tables = _tables(db)
        assert LEGACY_TABLE not in tables
        assert LEGACY_BACKUP_TABLE in tables
        with sqlite3.connect(str(db)) as conn:
            assert conn.execute(f"SELECT COUNT(*) FROM {LEGACY_BACKUP_TABLE}").fetchone()[0] == 3

        a = ck.get_record("docs/a.pdf")
        assert a is not None
        assert a["collection"] == DEFAULT_COLLECTION
        assert a["file_hash"] == H1
        assert a["status"] == "success"
        assert a["failed_stage"] is None
        assert a["processed_at"] == "2026-01-01 10:00:00"
        assert (a["parser_version"], a["chunk_version"], a["embedding_version"]) == (
            LEGACY_VERSION,
            LEGACY_VERSION,
            LEGACY_VERSION,
        )
        b = ck.get_record("docs/b.pdf")
        assert b["status"] == "failed" and b["error_msg"] == "boom"

        records = {r["file_hash"]: r for r in ck.list_processed()}
        assert set(records) == {H1, H2, H3}
        assert records[H3]["file_path"] is None
        assert records[H3]["status"] == "failed"

        assert ck.should_skip(H1, source_path="docs/a.pdf", collection="default") is True
        assert ck.should_skip(H2, source_path="docs/b.pdf", collection="default") is False
    finally:
        ck.close()


def test_legacy_same_path_multiple_hashes_keeps_latest(tmp_path):
    db = tmp_path / "db" / "h.db"
    _create_legacy_db(
        db,
        [
            (H2, "docs/a.pdf", "success", "2026-02-01 10:00:00", None),
            (H1, "docs/a.pdf", "success", "2026-01-01 10:00:00", None),
        ],
    )
    ck = SQLiteIntegrityChecker(str(db))
    try:
        assert ck.get_record("docs/a.pdf")["file_hash"] == H2
        assert len(ck.list_processed()) == 1
    finally:
        ck.close()


def test_second_startup_is_idempotent(tmp_path):
    db = tmp_path / "db" / "h.db"
    _create_legacy_db(db, [(H1, "docs/a.pdf", "success", "2026-01-01 10:00:00", None)])
    SQLiteIntegrityChecker(str(db)).close()
    reopened = SQLiteIntegrityChecker(str(db))
    try:
        reopened.mark_success(H2, "docs/b.pdf", collection="c2")
    finally:
        reopened.close()
    third = SQLiteIntegrityChecker(str(db))
    try:
        tables = _tables(db)
        assert LEGACY_TABLE not in tables
        assert [t for t in tables if t.startswith(LEGACY_BACKUP_TABLE)] == [LEGACY_BACKUP_TABLE]
        assert len(third.list_processed()) == 2
        assert third.get_record("docs/a.pdf")["file_hash"] == H1
    finally:
        third.close()


def test_legacy_reappearing_after_migration_does_not_clobber_v2(tmp_path):
    """旧版代码在迁移后重新建出旧表：再次迁移不覆盖 v2 记录、备份表不冲突。"""
    db = tmp_path / "db" / "h.db"
    _create_legacy_db(db, [(H1, "docs/a.pdf", "success", "2026-01-01 10:00:00", None)])
    ck = SQLiteIntegrityChecker(str(db))
    ck.mark_success(H2, "docs/a.pdf")
    ck.close()
    _create_legacy_db(db, [(H3, "docs/a.pdf", "failed", "2026-03-01 10:00:00", "old code")])
    ck = SQLiteIntegrityChecker(str(db))
    try:
        assert ck.get_record("docs/a.pdf")["file_hash"] == H2
        tables = _tables(db)
        assert LEGACY_TABLE not in tables
        assert {LEGACY_BACKUP_TABLE, f"{LEGACY_BACKUP_TABLE}_2"} <= tables
    finally:
        ck.close()


def test_unrecognized_legacy_table_fails_fast(tmp_path):
    db = tmp_path / "db" / "h.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE ingestion_history (foo TEXT)")
    with pytest.raises(sqlite3.DatabaseError, match="legacy"):
        SQLiteIntegrityChecker(str(db))
    # 失败迁移整体回滚：旧表原样保留，v2 不残留。
    tables = _tables(db)
    assert LEGACY_TABLE in tables
    assert HISTORY_TABLE not in tables


# ---------------------------------------------------------------------------
# should_skip 判定矩阵
# ---------------------------------------------------------------------------


def test_should_skip_same_path_same_collection_same_hash(checker):
    checker.mark_success(H1, "docs/a.pdf", collection="c1")
    assert checker.should_skip(H1, source_path="docs/a.pdf", collection="c1") is True


def test_should_skip_isolated_per_collection(checker):
    checker.mark_success(H1, "docs/a.pdf", collection="c1")
    assert checker.should_skip(H1, source_path="docs/a.pdf", collection="c2") is False
    checker.mark_success(H1, "docs/a.pdf", collection="c2")
    assert checker.should_skip(H1, source_path="docs/a.pdf", collection="c1") is True
    assert checker.should_skip(H1, source_path="docs/a.pdf", collection="c2") is True
    assert len(checker.list_processed()) == 2


def test_should_skip_false_when_hash_changed(checker):
    checker.mark_success(H1, "docs/a.pdf", collection="c1")
    assert checker.should_skip(H2, source_path="docs/a.pdf", collection="c1") is False


def test_should_skip_false_for_other_path_with_same_hash(checker):
    checker.mark_success(H1, "docs/a.pdf", collection="c1")
    assert checker.should_skip(H1, source_path="docs/copy.pdf", collection="c1") is False


def test_should_skip_false_after_failure(checker):
    checker.mark_success(H1, "docs/a.pdf")
    checker.mark_failed(H1, "boom", source_path="docs/a.pdf", collection="default")
    assert checker.should_skip(H1, source_path="docs/a.pdf", collection="default") is False


def test_should_skip_collection_defaults_to_default(checker):
    checker.mark_success(H1, "docs/a.pdf")
    assert checker.should_skip(H1, source_path="docs/a.pdf") is True


def test_legacy_hash_only_should_skip(checker):
    checker.mark_success(H1, "docs/a.pdf", collection="c1")
    assert checker.should_skip(H1) is True
    assert checker.should_skip(H1, collection="c1") is True
    assert checker.should_skip(H1, collection="c2") is False


# ---------------------------------------------------------------------------
# failed_stage 与版本三列
# ---------------------------------------------------------------------------


def test_mark_failed_records_stage_and_versions(checker):
    checker.mark_failed(
        H1,
        "EmbeddingError: timeout",
        source_path="docs/a.pdf",
        collection="c1",
        failed_stage="embed",
        parser_version="PdfLoader",
        chunk_version="recursive/1000/200/refine=rule",
        embedding_version="openai/m/default",
    )
    record = checker.get_record("docs/a.pdf", "c1")
    assert record["status"] == "failed"
    assert record["failed_stage"] == "embed"
    assert record["error_msg"] == "EmbeddingError: timeout"
    assert record["parser_version"] == "PdfLoader"
    assert record["chunk_version"] == "recursive/1000/200/refine=rule"
    assert record["embedding_version"] == "openai/m/default"


def test_mark_failed_updates_hash_and_keeps_versions_when_omitted(checker):
    checker.mark_success(H1, "docs/a.pdf", parser_version="P1", chunk_version="C1", embedding_version="E1")
    checker.mark_failed(H2, "boom", source_path="docs/a.pdf", failed_stage="load")
    record = checker.get_record("docs/a.pdf")
    assert record["file_hash"] == H2
    assert record["failed_stage"] == "load"
    assert (record["parser_version"], record["chunk_version"], record["embedding_version"]) == (
        "P1",
        "C1",
        "E1",
    )


def test_mark_success_clears_failed_stage_and_error(checker):
    checker.mark_failed(H1, "boom", source_path="docs/a.pdf", failed_stage="store")
    checker.mark_success(H1, "docs/a.pdf", parser_version="P2", chunk_version="C2", embedding_version="E2")
    record = checker.get_record("docs/a.pdf")
    assert record["status"] == "success"
    assert record["failed_stage"] is None
    assert record["error_msg"] is None
    assert (record["parser_version"], record["chunk_version"], record["embedding_version"]) == (
        "P2",
        "C2",
        "E2",
    )


def test_legacy_mark_failed_without_path_updates_existing_row(checker):
    checker.mark_success(H1, "docs/a.pdf", collection="c1")
    checker.mark_failed(H1, "later", failed_stage="split")
    record = checker.get_record("docs/a.pdf", "c1")
    assert record["status"] == "failed"
    assert record["failed_stage"] == "split"
    assert len(checker.list_processed()) == 1


def test_list_processed_exposes_v2_fields(checker):
    checker.mark_success(H1, "docs/a.pdf", collection="c1", parser_version="P")
    [record] = checker.list_processed()
    assert record["source_path"] == record["file_path"] == "docs/a.pdf"
    assert record["collection"] == "c1"
    assert record["parser_version"] == "P"
    assert "failed_stage" in record


# ---------------------------------------------------------------------------
# remove_record
# ---------------------------------------------------------------------------


def test_remove_record_by_primary_key(checker):
    checker.mark_success(H1, "docs/a.pdf", collection="c1")
    checker.mark_success(H1, "docs/a.pdf", collection="c2")
    assert checker.remove_record(source_path="docs/a.pdf", collection="c1") is True
    assert checker.get_record("docs/a.pdf", "c1") is None
    assert checker.get_record("docs/a.pdf", "c2") is not None
    assert checker.remove_record(source_path="docs/a.pdf", collection="c1") is False


def test_remove_record_by_hash_removes_all_collections(checker):
    checker.mark_success(H1, "docs/a.pdf", collection="c1")
    checker.mark_success(H1, "docs/a.pdf", collection="c2")
    assert checker.remove_record(H1) is True
    assert checker.list_processed() == []
