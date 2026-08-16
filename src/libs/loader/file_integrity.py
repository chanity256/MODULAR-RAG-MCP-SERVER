"""文件完整性检查（阶段 C2）。

为 Ingestion Pipeline 提供"增量摄取"判定基础：
- ``compute_sha256(path)``：流式计算文件 SHA256（同一文件多次计算结果一致）。
- ``should_skip(file_hash)``：已成功处理且内容未变更的文件跳过重复摄取。
- ``mark_success`` / ``mark_failed``：记录处理结果，失败不阻塞（下次重跑）。

默认实现 ``SQLiteIntegrityChecker`` 使用 SQLite 持久化到
``data/db/ingestion_history.db``（表结构见 DEV_SPEC 5.2：file_hash /
file_path / status / processed_at / error_msg），WAL 模式 + 写锁 +
busy_timeout 支持并发写入；后续可替换为 Redis / PostgreSQL 后端
（实现 ``FileIntegrityChecker`` 抽象接口即可）。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path

__all__ = ["FileIntegrityChecker", "SQLiteIntegrityChecker", "DEFAULT_DB_PATH"]

#: 默认数据库文件路径（DEV_SPEC 5.2 约定）。
DEFAULT_DB_PATH = "data/db/ingestion_history.db"

# 流式读取块大小（64KB，避免大文件一次性载入内存）。
_CHUNK_SIZE = 64 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingestion_history (
    file_hash    TEXT PRIMARY KEY,
    file_path    TEXT,
    status       TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    error_msg    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingestion_history_status ON ingestion_history(status);
"""


class FileIntegrityChecker(ABC):
    """文件完整性检查抽象接口。

    所有后端（SQLite / Redis / PostgreSQL）需实现以下四个方法；
    Pipeline 只依赖本抽象，不感知具体存储。
    """

    @abstractmethod
    def compute_sha256(self, path: str) -> str:
        """计算文件 SHA256（hex 摘要）。文件不存在时抛出可读错误。"""
        raise NotImplementedError

    @abstractmethod
    def should_skip(self, file_hash: str) -> bool:
        """判断该 hash 是否可跳过（仅 ``status == "success"`` 时为 True）。"""
        raise NotImplementedError

    @abstractmethod
    def mark_success(self, file_hash: str, file_path: str) -> None:
        """标记处理成功（幂等，重复标记覆盖旧记录并清除 error_msg）。"""
        raise NotImplementedError

    @abstractmethod
    def mark_failed(self, file_hash: str, error_msg: str) -> None:
        """标记处理失败（不阻塞后续重跑，记录失败原因）。"""
        raise NotImplementedError

    def close(self) -> None:
        """释放底层连接（默认无操作，供具体实现覆盖）。"""


class SQLiteIntegrityChecker(FileIntegrityChecker):
    """基于 SQLite 的默认实现（WAL 模式，支持并发写入）。

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
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
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

    def should_skip(self, file_hash: str) -> bool:
        """仅当该 hash 存在且最近一次状态为 success 时返回 True。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM ingestion_history WHERE file_hash = ?", (file_hash,)
            ).fetchone()
        return bool(row) and row[0] == "success"

    def mark_success(self, file_hash: str, file_path: str) -> None:
        """幂等写入 success 记录（覆盖旧记录并清除 error_msg）。"""
        self._upsert(file_hash, file_path=file_path, status="success", error_msg=None)

    def mark_failed(self, file_hash: str, error_msg: str) -> None:
        """写入 failed 记录；保留已有 file_path（若此前记录过）。"""
        self._upsert(file_hash, file_path=None, status="failed", error_msg=error_msg)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _upsert(
        self,
        file_hash: str,
        *,
        file_path: str | None,
        status: str,
        error_msg: str | None,
    ) -> None:
        """INSERT OR REPLACE 写入；file_path 为 None 时保留旧值。"""
        with self._lock:
            if file_path is None:
                row = self._conn.execute(
                    "SELECT file_path FROM ingestion_history WHERE file_hash = ?", (file_hash,)
                ).fetchone()
                file_path = row[0] if row else None
            self._conn.execute(
                """
                INSERT OR REPLACE INTO ingestion_history
                    (file_hash, file_path, status, processed_at, error_msg)
                VALUES (?, ?, ?, datetime('now'), ?)
                """,
                (file_hash, file_path, status, error_msg),
            )
            self._conn.commit()
