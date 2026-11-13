"""Chat 会话持久化（WC6.3）：SQLite 两表 ``chat_sessions`` / ``chat_messages``。

- 单连接 + ``RLock``（``check_same_thread=False``）：API 层的线程池/事件循环线程可共用一个
  store 实例，写入串行化；本项目是单机本地场景，不需要连接池。
- 用户消息到达即落库，助手消息在终帧落库（含取消/中断时的部分内容，
  ``metadata.cancelled: true``），因此服务重启不丢会话。
- ``message_count`` 与消息写入在同一事务内维护。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = ["SessionStore", "SessionNotFoundError", "VALID_ROLES"]

VALID_ROLES = ("user", "assistant", "system")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id    TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    message_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    sources_json  TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at);
"""


class SessionNotFoundError(KeyError):
    """会话不存在。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _session_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "settings": json.loads(row["settings_json"]),
        "message_count": row["message_count"],
    }


def _message_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "sources": json.loads(row["sources_json"]),
        "metadata": json.loads(row["metadata_json"]),
        "created_at": row["created_at"],
    }


class SessionStore:
    """线程安全的 SQLite 会话存储。

    Args:
        db_path: 数据库文件路径（``":memory:"`` 用于测试）；父目录不存在时自动创建。
        clock: 返回 ISO 时间串的可注入时钟（测试用）。
    """

    def __init__(self, db_path: str | Path, *, clock: Callable[[], str] | None = None) -> None:
        self._clock = clock or _utc_now
        self._lock = threading.RLock()
        path = str(db_path)
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            path = str(Path(path).expanduser())
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- sessions -------------------------------------------------------

    def create_session(
        self,
        *,
        title: str = "",
        settings: Mapping[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """创建会话（``session_id`` 缺省生成 uuid）；已存在的 id 抛 ``ValueError``。"""
        sid = session_id or uuid.uuid4().hex
        now = self._clock()
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO chat_sessions(session_id, title, created_at, updated_at, settings_json, "
                        "message_count) VALUES (?, ?, ?, ?, ?, 0)",
                        (sid, title, now, now, _dumps(dict(settings or {}))),
                    )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"session already exists: {sid}") from exc
        return {
            "session_id": sid,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "settings": dict(settings or {}),
            "message_count": 0,
        }

    def session_exists(self, session_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM chat_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row is not None

    def list_sessions(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """按最近更新倒序列出会话（不含消息）。"""
        if limit < 0 or offset < 0:
            raise ValueError("limit and offset must be non-negative")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chat_sessions ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_session_row(r) for r in rows]

    def count_sessions(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0])

    def get_session(self, session_id: str, *, with_messages: bool = True) -> dict[str, Any] | None:
        """返回会话；``with_messages`` 时附带 ``messages``（旧 -> 新）。不存在返回 ``None``。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            session = _session_row(row)
            if with_messages:
                session["messages"] = self._messages_locked(session_id)
        return session

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """会话消息（旧 -> 新）；会话不存在返回空列表。"""
        with self._lock:
            return self._messages_locked(session_id)

    def _messages_locked(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY rowid ASC", (session_id,)
        ).fetchall()
        return [_message_row(r) for r in rows]

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其消息；返回是否真的删除了。"""
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
        return cursor.rowcount > 0

    def update_title(self, session_id: str, title: str) -> bool:
        """更新标题（不改变 ``updated_at``，避免重命名打乱最近更新排序）。"""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE chat_sessions SET title = ? WHERE session_id = ?", (title, session_id)
            )
        return cursor.rowcount > 0

    def update_settings(self, session_id: str, settings: Mapping[str, Any]) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE chat_sessions SET settings_json = ? WHERE session_id = ?",
                (_dumps(dict(settings)), session_id),
            )
        return cursor.rowcount > 0

    # ---- messages -------------------------------------------------------

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        sources: list[Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """追加一条消息并在同一事务内更新 ``message_count`` / ``updated_at``。

        Raises:
            ValueError: ``role`` 非法。
            SessionNotFoundError: 会话不存在。
        """
        if role not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        message_id = uuid.uuid4().hex
        now = self._clock()
        sources_list = list(sources or [])
        metadata_dict = dict(metadata or {})
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE chat_sessions SET message_count = message_count + 1, updated_at = ? "
                "WHERE session_id = ?",
                (now, session_id),
            )
            if cursor.rowcount == 0:
                raise SessionNotFoundError(session_id)
            self._conn.execute(
                "INSERT INTO chat_messages(id, session_id, role, content, sources_json, metadata_json, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, session_id, role, content, _dumps(sources_list), _dumps(metadata_dict), now),
            )
        return {
            "id": message_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "sources": sources_list,
            "metadata": metadata_dict,
            "created_at": now,
        }
