"""后台任务模型：SQLite 持久化 + 有界线程池 + 事件订阅（WEBUI_CHAT_SPEC WC4.1）。

状态机 ``pending → running → succeeded | failed``，每次状态/详情变更都落库并
向订阅者推送，使页面刷新、WS 断线后仍能经 ``GET /api/v1/tasks/{id}`` 取回状态。

- :class:`TaskStore`：线程安全的 SQLite 存取，首次使用才建库（``create_app`` 零 IO）；
  首次打开时把上个进程遗留的 ``pending`` / ``running`` 任务标记为 ``failed``
  （``error.type == "interrupted"``）。假设同一个 ``tasks.db`` 只被一个进程使用。
- :class:`TaskManager`：把可调用对象投递到有界线程池执行；回调通过
  :class:`TaskContext` 上报进度；订阅者在 worker 线程中同步被回调，异步侧用
  :class:`TaskSubscription` 经 ``loop.call_soon_threadsafe`` 桥接到 ``asyncio.Queue``。

本模块纯标准库，日志走 logging（stderr）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "TaskContext",
    "TaskFailure",
    "TaskManager",
    "TaskRecord",
    "TaskStatus",
    "TaskStore",
    "TaskSubscription",
]

logger = logging.getLogger(__name__)


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ACTIVE_STATUSES = (TaskStatus.PENDING, TaskStatus.RUNNING)
TERMINAL_STATUSES = (TaskStatus.SUCCEEDED, TaskStatus.FAILED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_seq ON tasks(seq);
"""

_COLUMNS = "task_id, kind, status, version, detail_json, created_at, updated_at, started_at, finished_at"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TaskFailure(Exception):
    """任务函数主动判定失败；``result`` 作为部分结果仍写入 ``detail.result``。"""

    def __init__(self, message: str, *, result: Any = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    kind: str
    status: str
    version: int
    detail: dict[str, Any]
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "status": self.status,
            "version": self.version,
            "detail": self.detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _row_to_record(row: sqlite3.Row | tuple[Any, ...]) -> TaskRecord:
    task_id, kind, status, version, detail_json, created, updated, started, finished = row
    try:
        detail = json.loads(detail_json)
    except (TypeError, ValueError):
        detail = {}
    return TaskRecord(
        task_id=task_id,
        kind=kind,
        status=status,
        version=version,
        detail=detail if isinstance(detail, dict) else {},
        created_at=created,
        updated_at=updated,
        started_at=started,
        finished_at=finished,
    )


def _json_default(value: Any) -> Any:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


class TaskStore:
    """线程安全的 SQLite 任务存取；单连接 + 互斥锁。"""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def _db(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self._path), check_same_thread=False)
                conn.executescript(_SCHEMA)
                self._conn = conn
                self._recover_interrupted(conn)
            return self._conn

    def _recover_interrupted(self, conn: sqlite3.Connection) -> None:
        now = _now()
        rows = conn.execute(
            "SELECT task_id, detail_json FROM tasks WHERE status IN (?, ?)", ACTIVE_STATUSES
        ).fetchall()
        for task_id, detail_json in rows:
            try:
                detail = json.loads(detail_json)
            except (TypeError, ValueError):
                detail = {}
            if not isinstance(detail, dict):
                detail = {}
            detail["error"] = {
                "type": "interrupted",
                "message": "Task interrupted by a service restart",
            }
            conn.execute(
                "UPDATE tasks SET status=?, detail_json=?, updated_at=?, finished_at=?, "
                "version=version+1 WHERE task_id=?",
                (TaskStatus.FAILED, json.dumps(detail, default=_json_default), now, now, task_id),
            )
        if rows:
            conn.commit()
            logger.warning("marked %d interrupted task(s) as failed", len(rows))

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def create(self, kind: str, detail: dict[str, Any] | None = None) -> TaskRecord:
        now = _now()
        task_id = uuid.uuid4().hex
        with self._lock:
            conn = self._db()
            seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM tasks").fetchone()[0]
            conn.execute(
                "INSERT INTO tasks (task_id, seq, kind, status, version, detail_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    task_id,
                    seq,
                    kind,
                    TaskStatus.PENDING,
                    json.dumps(detail or {}, default=_json_default),
                    now,
                    now,
                ),
            )
            conn.commit()
            return self._get(conn, task_id)  # type: ignore[return-value]

    @staticmethod
    def _get(conn: sqlite3.Connection, task_id: str) -> TaskRecord | None:
        row = conn.execute(f"SELECT {_COLUMNS} FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return _row_to_record(row) if row else None

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._get(self._db(), task_id)

    def list(
        self,
        *,
        kind: str | None = None,
        statuses: Iterable[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TaskRecord], int]:
        """新任务在前；返回 ``(当前页, 过滤后总数)``。"""
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        status_list = list(statuses) if statuses is not None else []
        if status_list:
            clauses.append(f"status IN ({','.join('?' * len(status_list))})")
            params.extend(status_list)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            conn = self._db()
            total = conn.execute(f"SELECT COUNT(*) FROM tasks{where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM tasks{where} ORDER BY seq DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [_row_to_record(row) for row in rows], total

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        detail_patch: dict[str, Any] | None = None,
    ) -> TaskRecord | None:
        """读-改-写：``detail_patch`` 浅合并进 ``detail``，``status`` 迁移时补时间戳；版本号 +1。"""
        with self._lock:
            conn = self._db()
            current = self._get(conn, task_id)
            if current is None:
                return None
            if current.terminal:
                return current
            now = _now()
            detail = dict(current.detail)
            if detail_patch:
                detail.update(detail_patch)
            started = current.started_at
            finished = current.finished_at
            new_status = status or current.status
            if new_status == TaskStatus.RUNNING and started is None:
                started = now
            if new_status in TERMINAL_STATUSES:
                finished = now
            conn.execute(
                "UPDATE tasks SET status=?, detail_json=?, updated_at=?, started_at=?, finished_at=?, "
                "version=version+1 WHERE task_id=?",
                (new_status, json.dumps(detail, default=_json_default), now, started, finished, task_id),
            )
            conn.commit()
            return self._get(conn, task_id)


class TaskContext:
    """传给任务函数的句柄：上报进度与自定义详情。"""

    def __init__(self, manager: "TaskManager", task_id: str) -> None:
        self._manager = manager
        self.task_id = task_id

    def progress(self, stage: str, current: int, total: int) -> None:
        """签名与 ``IngestionPipeline`` 的 ``on_progress`` 一致，可直接透传。"""
        self.update(progress={"stage": stage, "current": int(current), "total": int(total)})

    def update(self, **fields: Any) -> None:
        self._manager._apply(self.task_id, detail_patch=fields)


TaskFn = Callable[[TaskContext], Any]
Listener = Callable[[TaskRecord], None]


class TaskManager:
    """在有界线程池中执行任务并发布事件。

    Args:
        store: 任务存储（``TaskStore`` 或 db 路径）。
        max_workers: 并发任务上限；超出的任务保持 ``pending`` 排队。
    """

    def __init__(self, store: TaskStore | str | Path, *, max_workers: int = 2) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._store = store if isinstance(store, TaskStore) else TaskStore(store)
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._futures: dict[str, Future[None]] = {}
        self._listeners: dict[str, list[Listener]] = {}

    @property
    def store(self) -> TaskStore:
        return self._store

    def get(self, task_id: str) -> TaskRecord | None:
        return self._store.get(task_id)

    def list(
        self,
        *,
        kind: str | None = None,
        statuses: Iterable[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TaskRecord], int]:
        return self._store.list(kind=kind, statuses=statuses, limit=limit, offset=offset)

    def submit(
        self,
        kind: str,
        fn: TaskFn,
        *,
        detail: dict[str, Any] | None = None,
    ) -> TaskRecord:
        """创建 ``pending`` 任务并投递；返回创建时快照。"""
        record = self._store.create(kind, detail)
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers, thread_name_prefix="api-task"
                )
            self._futures[record.task_id] = self._executor.submit(self._run, record.task_id, fn)
        return record

    def wait(self, task_id: str, timeout: float | None = None) -> TaskRecord | None:
        """阻塞到任务线程结束（主要供测试）；非本进程提交的任务直接返回当前快照。"""
        with self._lock:
            future = self._futures.get(task_id)
        if future is not None:
            future.result(timeout=timeout)
        return self._store.get(task_id)

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=True)
        self._store.close()

    # ------------------------------------------------------------------ events
    def add_listener(self, task_id: str, listener: Listener) -> None:
        with self._lock:
            self._listeners.setdefault(task_id, []).append(listener)

    def remove_listener(self, task_id: str, listener: Listener) -> None:
        with self._lock:
            listeners = self._listeners.get(task_id)
            if listeners and listener in listeners:
                listeners.remove(listener)
            if not listeners:
                self._listeners.pop(task_id, None)

    def subscribe(self, task_id: str, loop: asyncio.AbstractEventLoop | None = None) -> "TaskSubscription":
        return TaskSubscription(self, task_id, loop or asyncio.get_running_loop())

    def _publish(self, record: TaskRecord) -> None:
        with self._lock:
            listeners = list(self._listeners.get(record.task_id, ()))
        for listener in listeners:
            try:
                listener(record)
            except Exception:
                logger.warning("task listener raised for %s", record.task_id, exc_info=True)

    # --------------------------------------------------------------- execution
    def _apply(
        self,
        task_id: str,
        *,
        status: str | None = None,
        detail_patch: dict[str, Any] | None = None,
    ) -> TaskRecord | None:
        record = self._store.update(task_id, status=status, detail_patch=detail_patch)
        if record is not None:
            self._publish(record)
        return record

    def _run(self, task_id: str, fn: TaskFn) -> None:
        try:
            self._apply(task_id, status=TaskStatus.RUNNING)
            result = fn(TaskContext(self, task_id))
        except TaskFailure as exc:
            patch: dict[str, Any] = {"error": {"type": "TaskFailure", "message": str(exc)}}
            if exc.result is not None:
                patch["result"] = exc.result
            self._finish(task_id, TaskStatus.FAILED, patch)
        except Exception as exc:
            logger.warning("task %s failed", task_id, exc_info=True)
            self._finish(
                task_id,
                TaskStatus.FAILED,
                {"error": {"type": type(exc).__name__, "message": str(exc)}},
            )
        else:
            self._finish(task_id, TaskStatus.SUCCEEDED, {"result": result})

    def _finish(self, task_id: str, status: str, patch: dict[str, Any]) -> None:
        try:
            self._apply(task_id, status=status, detail_patch=patch)
        except Exception:
            # 终态写入失败（如存储已关闭）不应让线程池吞掉异常而毫无痕迹。
            logger.error("failed to persist terminal state of task %s", task_id, exc_info=True)


@dataclass
class TaskSubscription:
    """把 worker 线程里的任务事件桥接到 asyncio 队列。

    先订阅、再读取当前快照（调用方负责），可避免订阅与读取之间丢事件；
    ``version`` 单调递增，消费方据此去重。
    """

    manager: TaskManager
    task_id: str
    loop: asyncio.AbstractEventLoop
    queue: "asyncio.Queue[TaskRecord]" = field(default_factory=asyncio.Queue)

    def __post_init__(self) -> None:
        self.manager.add_listener(self.task_id, self._push)

    def _push(self, record: TaskRecord) -> None:
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, record)
        except RuntimeError:
            # 事件循环已关闭（连接已销毁）：静默丢弃，订阅随后被移除。
            pass

    def close(self) -> None:
        self.manager.remove_listener(self.task_id, self._push)
