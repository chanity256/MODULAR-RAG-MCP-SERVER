"""``libs.loader.file_integrity`` 单元测试（阶段 C2）。

覆盖验收标准：
- 同一文件多次计算 hash 结果一致（且与 hashlib 参考值一致，含大文件流式读取）。
- 标记 success 后 ``should_skip`` 返回 True；failed 不跳过。
- 数据库文件自动创建（含父目录）；默认路径为 ``data/db/ingestion_history.db``。
- WAL 模式 + 并发写入安全（单实例多线程 / 多连接多线程）。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading

import pytest

from libs.loader.file_integrity import (
    DEFAULT_DB_PATH,
    FileIntegrityChecker,
    SQLiteIntegrityChecker,
)


@pytest.fixture
def checker(tmp_path):
    """使用临时目录的 checker，测试结束自动关闭。"""
    ck = SQLiteIntegrityChecker(str(tmp_path / "data" / "db" / "ingestion_history.db"))
    yield ck
    ck.close()


@pytest.fixture
def sample_file(tmp_path):
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"%PDF-1.7 fake content for hashing")
    return p


# ---------------------------------------------------------------------------
# compute_sha256
# ---------------------------------------------------------------------------


def test_compute_sha256_stable_and_matches_hashlib(checker, sample_file):
    h1 = checker.compute_sha256(str(sample_file))
    h2 = checker.compute_sha256(str(sample_file))
    assert h1 == h2
    assert h1 == hashlib.sha256(sample_file.read_bytes()).hexdigest()
    assert len(h1) == 64


def test_compute_sha256_different_content_different_hash(checker, tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("content A")
    b.write_text("content B")
    assert checker.compute_sha256(str(a)) != checker.compute_sha256(str(b))


def test_compute_sha256_large_file_streaming(checker, tmp_path):
    """超过单块（64KB）的文件分块读取后仍与整体 hash 一致。"""
    big = tmp_path / "big.bin"
    payload = bytes(range(256)) * 1024  # 256KB > 64KB
    big.write_bytes(payload)
    assert checker.compute_sha256(str(big)) == hashlib.sha256(payload).hexdigest()


def test_compute_sha256_missing_file_raises_readable_error(checker, tmp_path):
    with pytest.raises(FileNotFoundError) as exc_info:
        checker.compute_sha256(str(tmp_path / "nope.pdf"))
    assert "nope.pdf" in str(exc_info.value)


# ---------------------------------------------------------------------------
# should_skip / mark_success / mark_failed
# ---------------------------------------------------------------------------


def test_should_skip_false_for_unknown_hash(checker):
    assert checker.should_skip("deadbeef" * 8) is False


def test_mark_success_then_should_skip_true(checker, sample_file):
    h = checker.compute_sha256(str(sample_file))
    assert checker.should_skip(h) is False
    checker.mark_success(h, str(sample_file))
    assert checker.should_skip(h) is True
    # 幂等：重复标记仍为 True。
    checker.mark_success(h, str(sample_file))
    assert checker.should_skip(h) is True


def test_mark_failed_not_skipped_and_records_error(checker, tmp_path, sample_file):
    db = tmp_path / "data" / "db" / "ingestion_history.db"
    ck = SQLiteIntegrityChecker(str(db))
    try:
        h = "ab" * 32
        ck.mark_failed(h, "pdf parse error: page 3")
        assert ck.should_skip(h) is False
        row = sqlite3.connect(str(db)).execute(
            "SELECT file_path, status, error_msg FROM ingestion_history WHERE file_hash = ?", (h,)
        ).fetchone()
        assert row == (None, "failed", "pdf parse error: page 3")
    finally:
        ck.close()


def test_mark_success_overrides_failed_and_clears_error(checker, sample_file):
    h = "cd" * 32
    checker.mark_failed(h, "transient error")
    checker.mark_success(h, str(sample_file))
    assert checker.should_skip(h) is True
    with sqlite3.connect(str(checker._db_path)) as conn:  # noqa: SLF001 - 契约断言需要读库
        status, error_msg = conn.execute(
            "SELECT status, error_msg FROM ingestion_history WHERE file_hash = ?", (h,)
        ).fetchone()
    assert status == "success"
    assert error_msg is None


def test_mark_failed_preserves_known_file_path(checker, sample_file):
    h = "ef" * 32
    checker.mark_success(h, str(sample_file))
    checker.mark_failed(h, "later run failed")
    with sqlite3.connect(str(checker._db_path)) as conn:  # noqa: SLF001
        file_path, status = conn.execute(
            "SELECT file_path, status FROM ingestion_history WHERE file_hash = ?", (h,)
        ).fetchone()
    assert file_path == str(sample_file)
    assert status == "failed"
    assert checker.should_skip(h) is False


# ---------------------------------------------------------------------------
# 持久化与数据库布局
# ---------------------------------------------------------------------------


def test_db_file_created_with_nested_dirs(tmp_path):
    db = tmp_path / "data" / "db" / "ingestion_history.db"
    assert not db.exists()
    ck = SQLiteIntegrityChecker(str(db))
    try:
        assert db.is_file()
        ck.mark_success("aa" * 32, "x.pdf")
        assert db.is_file()
    finally:
        ck.close()


def test_default_db_path_constant():
    assert DEFAULT_DB_PATH == "data/db/ingestion_history.db"


def test_persistence_across_reopen(tmp_path, sample_file):
    db = str(tmp_path / "ingestion_history.db")
    h = "12" * 32
    SQLiteIntegrityChecker(db).mark_success(h, str(sample_file))
    reopened = SQLiteIntegrityChecker(db)
    try:
        assert reopened.should_skip(h) is True
    finally:
        reopened.close()


def test_wal_mode_enabled(checker):
    mode = sqlite3.connect(str(checker._db_path)).execute("PRAGMA journal_mode").fetchone()[0]  # noqa: SLF001
    assert mode.lower() == "wal"


def test_table_columns_match_spec(checker, tmp_path):
    cols = [r[1] for r in sqlite3.connect(str(checker._db_path)).execute("PRAGMA table_info(ingestion_history)")]  # noqa: SLF001
    assert cols == ["file_hash", "file_path", "status", "processed_at", "error_msg"]


# ---------------------------------------------------------------------------
# 并发写入（WAL + 锁 + busy_timeout）
# ---------------------------------------------------------------------------


def test_concurrent_writes_same_instance(tmp_path):
    ck = SQLiteIntegrityChecker(str(tmp_path / "h.db"))
    try:
        hashes = [[f"{t:02x}{i:06x}" + "0" * 24 for i in range(20)] for t in range(4)]
        threads = [
            threading.Thread(
                target=lambda hs=hs: [ck.mark_success(h, f"doc_{h[:4]}.pdf") for h in hs]
            )
            for hs in hashes
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total = sqlite3.connect(str(tmp_path / "h.db")).execute(
            "SELECT COUNT(*) FROM ingestion_history"
        ).fetchone()[0]
        assert total == 80
    finally:
        ck.close()


def test_concurrent_writes_separate_connections(tmp_path):
    """多连接（模拟多进程/多实例）并发写，WAL + busy_timeout 下不丢数据。"""
    db = str(tmp_path / "h.db")
    SQLiteIntegrityChecker(db).close()  # 初始化 schema
    errors: list[Exception] = []

    def writer(tid: int) -> None:
        try:
            ck = SQLiteIntegrityChecker(db)
            try:
                for i in range(15):
                    ck.mark_success(f"{tid:02x}{i:06x}" + "1" * 24, f"doc_{tid}_{i}.pdf")
            finally:
                ck.close()
        except Exception as exc:  # pragma: no cover - 收集线程内异常
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    total = sqlite3.connect(db).execute("SELECT COUNT(*) FROM ingestion_history").fetchone()[0]
    assert total == 60


# ---------------------------------------------------------------------------
# 抽象接口
# ---------------------------------------------------------------------------


def test_abstract_interface_cannot_instantiate():
    with pytest.raises(TypeError):
        FileIntegrityChecker()  # type: ignore[abstract]


def test_incomplete_subclass_rejected():
    class _Broken(FileIntegrityChecker):
        def compute_sha256(self, path):
            return ""

    with pytest.raises(TypeError):
        _Broken()  # type: ignore[abstract]
