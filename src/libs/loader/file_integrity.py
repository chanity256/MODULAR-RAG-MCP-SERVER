"""文件完整性检查（阶段 C2；历史表 v2 见 INGESTION_ROBUSTNESS_SPEC 3.2 / IR1）。

为 Ingestion Pipeline 提供"增量摄取"判定基础：
- ``compute_sha256(path)``：流式计算文件 SHA256（同一文件多次计算结果一致）。
- ``should_skip(file_hash, source_path=..., collection=...)``：同路径同集合
  最近一次 ``success`` 且哈希未变才跳过；换 collection / 内容变更均不命中。
- ``mark_success`` / ``mark_failed``：记录处理结果（含 ``failed_stage`` 与
  parser/chunk/embedding 三个版本标识），失败不阻塞（下次重跑）。

默认实现 ``SQLiteIntegrityChecker`` 使用 SQLite 持久化到
``data/db/ingestion_history.db`` 的 ``ingestion_history_v2`` 表
（主键 ``(source_path, collection)``），WAL 模式 + 写锁 + busy_timeout
支持并发写入。打开数据库时若检测到旧版 ``ingestion_history``
（``file_hash`` 主键）表，会在同一事务内把记录迁入 v2 并把旧表改名为
``ingestion_history_legacy_backup``；迁移失败直接抛错，不静默降级。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

__all__ = [
    "FileIntegrityChecker",
    "SQLiteIntegrityChecker",
    "DEFAULT_DB_PATH",
    "DEFAULT_COLLECTION",
    "HISTORY_TABLE",
    "LEGACY_TABLE",
    "LEGACY_BACKUP_TABLE",
    "LEGACY_VERSION",
]

#: 默认数据库文件路径（DEV_SPEC 5.2 约定）。
DEFAULT_DB_PATH = "data/db/ingestion_history.db"

#: 未显式指定集合时的默认集合名（与 pipeline / CLI 缺省一致）。
DEFAULT_COLLECTION = "default"

HISTORY_TABLE = "ingestion_history_v2"
LEGACY_TABLE = "ingestion_history"
LEGACY_BACKUP_TABLE = "ingestion_history_legacy_backup"

#: 迁移自旧表的记录，版本三列统一填该值（旧表无版本信息）。
LEGACY_VERSION = "legacy"

# 旧表 file_path 可能为 NULL（mark_failed 先于任何 success），而 v2 的
# source_path 是主键列不可为空；用带哈希的占位值保证每条记录主键唯一，
# 对外（list_processed / get_record）再还原为 None。
_UNKNOWN_SOURCE_PREFIX = "<unknown>:"

# 流式读取块大小（64KB，避免大文件一次性载入内存）。
_CHUNK_SIZE = 64 * 1024

_LEGACY_COLUMNS = {"file_hash", "file_path", "status", "processed_at", "error_msg"}

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
    source_path       TEXT NOT NULL,
    collection        TEXT NOT NULL,
    file_hash         TEXT NOT NULL,
    status            TEXT NOT NULL,
    failed_stage      TEXT,
    parser_version    TEXT,
    chunk_version     TEXT,
    embedding_version TEXT,
    processed_at      TEXT NOT NULL,
    error_msg         TEXT,
    PRIMARY KEY (source_path, collection)
);
CREATE INDEX IF NOT EXISTS idx_ingestion_history_v2_hash ON {HISTORY_TABLE}(file_hash);
CREATE INDEX IF NOT EXISTS idx_ingestion_history_v2_status ON {HISTORY_TABLE}(status);
"""

_V2_COLUMNS = (
    "source_path", "collection", "file_hash", "status", "failed_stage",
    "parser_version", "chunk_version", "embedding_version", "processed_at", "error_msg",
)


def _unknown_source(file_hash: str) -> str:
    return f"{_UNKNOWN_SOURCE_PREFIX}{file_hash}"


def _public_source(source_path: str) -> str | None:
    return None if source_path.startswith(_UNKNOWN_SOURCE_PREFIX) else source_path


class FileIntegrityChecker(ABC):
    """文件完整性检查抽象接口。

    判定语义（历史表 v2）：记录以 ``(source_path, collection)`` 为键；
    ``should_skip`` 仅当同路径同集合最近一次为 ``success`` 且 ``file_hash``
    一致时返回 True。为兼容既有调用方，``source_path`` / ``collection``
    以关键字参数给出；省略 ``source_path`` 时退化为按哈希匹配（旧语义，
    不区分集合，仅供兼容，pipeline 必须传入路径与集合）。
    """

    @abstractmethod
    def compute_sha256(self, path: str) -> str:
        """计算文件 SHA256（hex 摘要）。文件不存在时抛出可读错误。"""
        raise NotImplementedError

    @abstractmethod
    def should_skip(
        self,
        file_hash: str,
        *,
        source_path: str | None = None,
        collection: str | None = None,
    ) -> bool:
        """同 ``(source_path, collection)`` 最近一次 success 且哈希相同时为 True。"""
        raise NotImplementedError

    @abstractmethod
    def mark_success(
        self,
        file_hash: str,
        file_path: str,
        *,
        collection: str = DEFAULT_COLLECTION,
        parser_version: str | None = None,
        chunk_version: str | None = None,
        embedding_version: str | None = None,
    ) -> None:
        """标记处理成功（幂等，覆盖旧记录并清除 error_msg / failed_stage）。"""
        raise NotImplementedError

    @abstractmethod
    def mark_failed(
        self,
        file_hash: str,
        error_msg: str,
        *,
        source_path: str | None = None,
        collection: str | None = None,
        failed_stage: str | None = None,
        parser_version: str | None = None,
        chunk_version: str | None = None,
        embedding_version: str | None = None,
    ) -> None:
        """标记处理失败（不阻塞后续重跑，记录失败原因与失败阶段）。"""
        raise NotImplementedError

    def remove_record(
        self,
        file_hash: str | None = None,
        *,
        source_path: str | None = None,
        collection: str | None = None,
    ) -> bool:
        """删除处理记录，使文件可重新摄入（阶段 G2）。

        给出 ``source_path`` 时按 v2 主键删除；否则按 ``file_hash`` 删除
        （可选按 ``collection`` 过滤）。

        Returns:
            至少删除一条记录为 True；不存在为 False（无副作用）。
        """
        raise NotImplementedError

    def get_record(
        self, source_path: str, collection: str = DEFAULT_COLLECTION
    ) -> dict[str, Any] | None:
        """按 ``(source_path, collection)`` 读取单条记录；不存在返回 None。"""
        raise NotImplementedError

    def list_processed(self) -> list[dict]:
        """列出全部处理记录（阶段 G2：文档列表/摄入时间）。

        Returns:
            每条含 ``file_hash, file_path, source_path, collection, status,
            failed_stage, parser_version, chunk_version, embedding_version,
            processed_at, error_msg``（``file_path`` 与 ``source_path`` 同值，
            前者为兼容键），按 ``processed_at`` 降序、``file_hash`` 升序。
        """
        raise NotImplementedError

    def close(self) -> None:
        """释放底层连接（默认无操作，供具体实现覆盖）。"""


class SQLiteIntegrityChecker(FileIntegrityChecker):
    """基于 SQLite 的默认实现（WAL 模式，支持并发写入）。

    ``source_path`` 原样存储（不做 realpath 归一）：归一化由调用方负责，
    以保证与 Chroma ``metadata.source_path`` 等其他存储的键一致。

    Args:
        db_path: 数据库文件路径，默认 ``data/db/ingestion_history.db``；
            父目录不存在时自动创建。
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._migrate()
        except Exception:
            self._conn.close()
            raise

    # ------------------------------------------------------------------
    # 查询与写入
    # ------------------------------------------------------------------

    def compute_sha256(self, path: str) -> str:
        """流式计算文件 SHA256；同一文件多次调用结果一致。

        Raises:
            FileNotFoundError: 文件不存在（错误信息包含路径）。
        """
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"file not found or not a regular file: {file_path}")
        digest = hashlib.sha256()
        with file_path.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def should_skip(
        self,
        file_hash: str,
        *,
        source_path: str | None = None,
        collection: str | None = None,
    ) -> bool:
        """同路径同集合最近一次 success 且哈希一致才返回 True。

        ``source_path`` 省略时为兼容旧调用：任一 success 记录的哈希命中即
        True（``collection`` 给定时限定该集合）。
        """
        with self._lock:
            if source_path is not None:
                row = self._conn.execute(
                    f"SELECT status, file_hash FROM {HISTORY_TABLE} "
                    "WHERE source_path = ? AND collection = ?",
                    (str(source_path), collection or DEFAULT_COLLECTION),
                ).fetchone()
                return bool(row) and row[0] == "success" and row[1] == file_hash
            sql = f"SELECT 1 FROM {HISTORY_TABLE} WHERE file_hash = ? AND status = 'success'"
            params: list[Any] = [file_hash]
            if collection is not None:
                sql += " AND collection = ?"
                params.append(collection)
            row = self._conn.execute(sql + " LIMIT 1", params).fetchone()
        return row is not None

    def mark_success(
        self,
        file_hash: str,
        file_path: str,
        *,
        collection: str = DEFAULT_COLLECTION,
        parser_version: str | None = None,
        chunk_version: str | None = None,
        embedding_version: str | None = None,
    ) -> None:
        """幂等写入 success 记录（覆盖旧记录，清除 error_msg / failed_stage）。"""
        source = str(file_path) if file_path else _unknown_source(file_hash)
        coll = collection or DEFAULT_COLLECTION
        with self._lock:
            # 路径未知时记下的失败占位记录由本次成功记录取代。
            self._conn.execute(
                f"DELETE FROM {HISTORY_TABLE} WHERE source_path = ? AND collection = ?",
                (_unknown_source(file_hash), coll),
            )
            self._conn.execute(
                f"""
                INSERT OR REPLACE INTO {HISTORY_TABLE}
                    (source_path, collection, file_hash, status, failed_stage,
                     parser_version, chunk_version, embedding_version, processed_at, error_msg)
                VALUES (?, ?, ?, 'success', NULL, ?, ?, ?, datetime('now'), NULL)
                """,
                (source, coll, file_hash, parser_version, chunk_version, embedding_version),
            )
            self._conn.commit()

    def mark_failed(
        self,
        file_hash: str,
        error_msg: str,
        *,
        source_path: str | None = None,
        collection: str | None = None,
        failed_stage: str | None = None,
        parser_version: str | None = None,
        chunk_version: str | None = None,
        embedding_version: str | None = None,
    ) -> None:
        """写入 failed 记录。

        给出 ``source_path`` 时按主键 upsert（哈希更新为本次哈希）；省略时
        更新该哈希的已有记录（保留其路径），无记录则以占位路径新建。
        版本参数为 None 时保留已有值。
        """
        versions = (parser_version, chunk_version, embedding_version)
        with self._lock:
            if source_path is None:
                sql = (
                    f"UPDATE {HISTORY_TABLE} SET status = 'failed', failed_stage = ?, "
                    "error_msg = ?, processed_at = datetime('now'), "
                    "parser_version = COALESCE(?, parser_version), "
                    "chunk_version = COALESCE(?, chunk_version), "
                    "embedding_version = COALESCE(?, embedding_version) "
                    "WHERE file_hash = ?"
                )
                params: list[Any] = [failed_stage, error_msg, *versions, file_hash]
                if collection is not None:
                    sql += " AND collection = ?"
                    params.append(collection)
                updated = self._conn.execute(sql, params).rowcount
                if updated:
                    self._conn.commit()
                    return
                source = _unknown_source(file_hash)
            else:
                source = str(source_path)
            self._conn.execute(
                f"""
                INSERT INTO {HISTORY_TABLE}
                    (source_path, collection, file_hash, status, failed_stage,
                     parser_version, chunk_version, embedding_version, processed_at, error_msg)
                VALUES (?, ?, ?, 'failed', ?, ?, ?, ?, datetime('now'), ?)
                ON CONFLICT(source_path, collection) DO UPDATE SET
                    file_hash = excluded.file_hash,
                    status = 'failed',
                    failed_stage = excluded.failed_stage,
                    parser_version = COALESCE(excluded.parser_version, parser_version),
                    chunk_version = COALESCE(excluded.chunk_version, chunk_version),
                    embedding_version = COALESCE(excluded.embedding_version, embedding_version),
                    processed_at = excluded.processed_at,
                    error_msg = excluded.error_msg
                """,
                (
                    source, collection or DEFAULT_COLLECTION, file_hash, failed_stage,
                    *versions, error_msg,
                ),
            )
            self._conn.commit()

    def remove_record(
        self,
        file_hash: str | None = None,
        *,
        source_path: str | None = None,
        collection: str | None = None,
    ) -> bool:
        """按主键（给出 source_path 时）或 file_hash 删除；无删除返回 False。"""
        if source_path is not None:
            if not isinstance(source_path, str) or not source_path:
                return False
            sql = f"DELETE FROM {HISTORY_TABLE} WHERE source_path = ? AND collection = ?"
            params: list[Any] = [source_path, collection or DEFAULT_COLLECTION]
        else:
            if not isinstance(file_hash, str) or not file_hash:
                return False
            sql = f"DELETE FROM {HISTORY_TABLE} WHERE file_hash = ?"
            params = [file_hash]
            if collection is not None:
                sql += " AND collection = ?"
                params.append(collection)
        with self._lock:
            deleted = self._conn.execute(sql, params).rowcount
            self._conn.commit()
        return deleted > 0

    def get_record(
        self, source_path: str, collection: str = DEFAULT_COLLECTION
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {', '.join(_V2_COLUMNS)} FROM {HISTORY_TABLE} "
                "WHERE source_path = ? AND collection = ?",
                (str(source_path), collection or DEFAULT_COLLECTION),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_processed(self) -> list[dict]:
        """列出全部处理记录（processed_at 降序，file_hash 升序）。"""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT {', '.join(_V2_COLUMNS)}
                FROM {HISTORY_TABLE}
                ORDER BY processed_at DESC, file_hash ASC, source_path ASC, collection ASC
                """
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: tuple) -> dict[str, Any]:
        record = dict(zip(_V2_COLUMNS, row))
        record["source_path"] = _public_source(record["source_path"])
        record["file_path"] = record["source_path"]
        return record

    def _table_columns(self, name: str) -> set[str] | None:
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        if not exists:
            return None
        return {r[1] for r in self._conn.execute(f'PRAGMA table_info("{name}")')}

    def _backup_table_name(self) -> str:
        name = LEGACY_BACKUP_TABLE
        suffix = 2
        while self._table_columns(name) is not None:
            name = f"{LEGACY_BACKUP_TABLE}_{suffix}"
            suffix += 1
        return name

    def _migrate(self) -> None:
        """建 v2 表并（如需）迁移旧表；单事务完成，幂等。

        BEGIN IMMEDIATE 先取写锁再检测旧表，避免多进程同时打开时重复迁移。
        同一路径在旧表中有多个哈希（文件被修改过）时保留 processed_at
        最新的一条；v2 已有同主键记录时以 v2 为准。
        """
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in filter(str.strip, _SCHEMA.split(";")):
                conn.execute(statement)
            legacy_cols = self._table_columns(LEGACY_TABLE)
            if legacy_cols is not None:
                if not _LEGACY_COLUMNS.issubset(legacy_cols):
                    raise sqlite3.DatabaseError(
                        f"unrecognized legacy table '{LEGACY_TABLE}' columns: {sorted(legacy_cols)}"
                    )
                rows = conn.execute(
                    f"SELECT file_hash, file_path, status, processed_at, error_msg "
                    f"FROM {LEGACY_TABLE} ORDER BY processed_at ASC, rowid ASC"
                ).fetchall()
                latest: dict[str, tuple] = {}
                for file_hash, file_path, status, processed_at, error_msg in rows:
                    source = file_path if file_path else _unknown_source(file_hash)
                    latest[source] = (
                        source, DEFAULT_COLLECTION, file_hash, status, None,
                        LEGACY_VERSION, LEGACY_VERSION, LEGACY_VERSION,
                        processed_at, error_msg,
                    )
                conn.executemany(
                    f"INSERT OR IGNORE INTO {HISTORY_TABLE} ({', '.join(_V2_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(_V2_COLUMNS))})",
                    list(latest.values()),
                )
                conn.execute(f'ALTER TABLE {LEGACY_TABLE} RENAME TO "{self._backup_table_name()}"')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
