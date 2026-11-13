"""ImageStorage：图片文件存储 + SQLite 索引映射（阶段 C13）。

接收图片字节流，落盘到约定目录，并在 ``data/db/image_index.db`` 的
``image_index`` 表中维护 ``image_id → file_path`` 映射（spec 精确约定的
表结构），完成 C 阶段存储链路的最后一环（Sparse/BM25、Dense/向量之后
的图片持久化），并为 Dashboard / DocumentManager（E/F 阶段的
``list_images`` / ``delete_images``）提供查询与删除入口。

目录布局（spec 约定 ``data/images/{collection}/`` 与 C3 PdfLoader 现行
``data/images/{doc_hash}/`` 的统一）：
- ``collection`` 提供时写入 ``{images_root}/{collection}/{image_id}{ext}``；
- ``collection`` 缺省而 ``doc_hash`` 提供时回退 ``{images_root}/{doc_hash}/``
  （对齐 C3 ``PdfLoader`` 的确定性布局与 DEV_SPEC 数据目录注释
  “{collection}/（实际存储在 {doc_hash}/ 子目录下）”）；
- 两者皆缺 → fail-fast（无法确定落盘目录）。

image_id 契约（不重新派生，同 C12 直接用 ``chunk.id`` 的纪律）：
- 全链路稳定 id 由 C3 ``make_image_id(doc_hash, page, seq)`` 生成
  （``{doc_hash}_{page}_{seq}``），与 ``ImageRef.id`` /
  ``[IMAGE: {image_id}]`` 占位符 / C4 ``image_refs`` 共用同一 id 空间；
- 本类以 ``image_id`` 为 ``PRIMARY KEY``，含路径分隔符或为 "."/".." 的
  id 视为契约破坏（fail-fast，防目录逃逸）。

幂等性：同一 ``image_id`` 重复 ``put`` → 文件原子覆盖
（tmp + ``os.replace``）+ ``INSERT OR REPLACE`` 更新行（``created_at``
刷新，不产生重复行）；若新目录与旧行不同则尽力清理旧文件（防孤儿）。

SQLite 模式（复用 C2 ``SQLiteIntegrityChecker`` 架构）：WAL +
``busy_timeout=5000`` 支持并发读写，``threading.Lock`` 保护单连接，
构造失败自动关闭连接；查询 miss（``get``）返回 None 而非抛错
（契约破坏才 fail-fast，同 C11/C12 纪律）。

注：caption 不入索引表——spec 表结构无此列，C7 的 caption 存于
chunk ``metadata["image_captions"]``，图片↔chunk 关联由 C4 占位符 /
``image_refs`` 表达，索引保持最小契约。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.trace.trace_context import TraceContext
from observability.logger import get_logger

__all__ = [
    "ImageRecord",
    "ImageStorage",
    "ImageStorageError",
    "DEFAULT_IMAGES_ROOT",
    "DEFAULT_DB_PATH",
    "DEFAULT_EXTENSION",
]

#: 图片落盘根目录（spec 约定 data/images/）。
DEFAULT_IMAGES_ROOT = "data/images"

#: 索引数据库路径（spec 验收标准约定 data/db/image_index.db）。
DEFAULT_DB_PATH = "data/db/image_index.db"

#: 默认图片扩展名（ImageRef.path 约定 {image_id}.png）。
DEFAULT_EXTENSION = ".png"

#: spec 精确约定的索引表结构（与 DEV_SPEC 5.2 数据目录注释一致）。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_index (
    image_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    collection TEXT,
    doc_hash TEXT,
    page_num INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_collection ON image_index(collection);
CREATE INDEX IF NOT EXISTS idx_doc_hash ON image_index(doc_hash);
"""

#: image_id / 目录名中禁止出现的字符（路径分隔符，防目录逃逸）。
_FORBIDDEN_ID_CHARS = ("/", "\\", os.sep, (os.altsep or ""))


class ImageStorageError(RuntimeError):
    """put/查询/删除输入违反契约（需要暴露的 bug，而非可降级故障）。"""


@dataclass
class ImageRecord:
    """``image_index`` 表中一行图片索引记录。

    Attributes:
        image_id: 全局唯一图片标识符（C3 ``make_image_id`` 产物）。
        file_path: 图片落盘路径（与 ``ImageRef.path`` 同一约定）。
        collection: 所属集合名（可空；缺省时以 ``doc_hash`` 归档）。
        doc_hash: 来源文档 SHA256（增量摄取与文档级删除的键）。
        page_num: 图片在原文档中的页码（可空）。
        created_at: 索引行写入时间（SQLite ``datetime('now')`` 格式）。
    """

    image_id: str
    file_path: str
    collection: str | None = None
    doc_hash: str | None = None
    page_num: int | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 序列化 dict。"""
        return {
            "image_id": self.image_id,
            "file_path": self.file_path,
            "collection": self.collection,
            "doc_hash": self.doc_hash,
            "page_num": self.page_num,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | tuple | list) -> "ImageRecord":
        """从 SELECT 行构造（容忍多余列，按列名/位置取值）。"""
        if isinstance(row, sqlite3.Row):
            keys = row.keys()
            getter = lambda name: row[name] if name in keys else None  # noqa: E731
        else:
            values = list(row)
            getter = lambda name: {  # noqa: E731
                "image_id": values[0],
                "file_path": values[1],
                "collection": values[2],
                "doc_hash": values[3],
                "page_num": values[4],
                "created_at": values[5],
            }.get(name)
        return cls(
            image_id=str(getter("image_id")),
            file_path=str(getter("file_path")),
            collection=getter("collection"),
            doc_hash=getter("doc_hash"),
            page_num=getter("page_num"),
            created_at=getter("created_at"),
        )


class ImageStorage:
    """图片文件存储 + SQLite 索引（WAL，支持并发访问）。

    Args:
        images_root: 图片落盘根目录，默认 ``data/images``。
        db_path: 索引数据库路径，默认 ``data/db/image_index.db``；
            父目录不存在时自动创建（同 ``SQLiteIntegrityChecker``）。
    """

    def __init__(
        self,
        images_root: str = DEFAULT_IMAGES_ROOT,
        db_path: str = DEFAULT_DB_PATH,
    ) -> None:
        self._images_root = Path(images_root)
        self._db_path = Path(db_path)
        self._images_root.mkdir(parents=True, exist_ok=True)
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
        self._logger = get_logger("modular-rag.ingestion.image_storage")

    # ------------------------------------------------------------------
    # 只读视图
    # ------------------------------------------------------------------

    @property
    def images_root(self) -> Path:
        """图片落盘根目录（只读视图语义）。"""
        return self._images_root

    @property
    def db_path(self) -> Path:
        """索引数据库路径（只读视图语义）。"""
        return self._db_path

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def put(
        self,
        image_id: str,
        data: bytes | bytearray,
        *,
        collection: str | None = None,
        doc_hash: str | None = None,
        page_num: int | None = None,
        ext: str = DEFAULT_EXTENSION,
        trace: TraceContext | None = None,
    ) -> ImageRecord:
        """保存一张图片并登记索引（幂等：同 id 覆盖，不产生重复行）。

        Args:
            image_id: 全局唯一图片标识符（C3 ``make_image_id`` 产物）。
            data: 图片字节流（非空 bytes/bytearray）。
            collection: 所属集合；缺省时以 ``doc_hash`` 作为归档目录
                （对齐 C3 布局）；两者皆缺为契约错误。
            doc_hash: 来源文档 SHA256（索引列 + 目录回退键）。
            page_num: 图片页码（可选，非负整数）。
            ext: 文件扩展名（默认 ``.png``；自动补前导点）。
            trace: 可选追踪上下文（storage 阶段打点）。

        Returns:
            写入后的 ``ImageRecord``（含 ``created_at``，按库中回读值）。

        Raises:
            ImageStorageError: 输入违反契约（空/类型非法 id、data，
                路径分隔符逃逸、非法 page_num/ext、目录键全缺）。
        """
        started = time.perf_counter()
        self._validate_id_like(image_id, "image_id")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ImageStorageError(
                f"data must be non-empty bytes/bytearray, got {type(data).__name__} "
                f"of length {len(data) if hasattr(data, '__len__') else '?'}"
            )
        payload = bytes(data)
        if collection is not None:
            self._validate_id_like(collection, "collection")
        if doc_hash is not None:
            self._validate_id_like(doc_hash, "doc_hash")
        if collection is None and doc_hash is None:
            raise ImageStorageError(
                "either collection or doc_hash is required to determine the image directory"
            )
        if page_num is not None:
            if isinstance(page_num, bool) or not isinstance(page_num, int) or page_num < 0:
                raise ImageStorageError(f"page_num must be a non-negative int or None, got {page_num!r}")
        ext = self._normalize_ext(ext)
        directory = collection if collection is not None else doc_hash
        out_dir = self._images_root / str(directory)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{image_id}{ext}"

        with self._lock:
            old_row = self._conn.execute(
                "SELECT file_path FROM image_index WHERE image_id = ?", (image_id,)
            ).fetchone()
            self._write_atomic(out_path, payload)
            self._conn.execute(
                """
                INSERT OR REPLACE INTO image_index
                    (image_id, file_path, collection, doc_hash, page_num, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (image_id, str(out_path), collection, doc_hash, page_num),
            )
            self._conn.commit()
        # 目录变更的重复 put：尽力清理旧文件，避免孤儿（失败不阻塞）。
        if old_row is not None and old_row[0] != str(out_path):
            self._unlink_quietly(Path(old_row[0]))

        record = self.get(image_id)
        assert record is not None  # 刚写入必命中（防御断言，不可达分支）
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if trace is not None:
            trace.record_stage(
                "storage",
                method="image_storage",
                details={
                    "action": "put",
                    "images": 1,
                    "replaced": old_row is not None,
                    "elapsed_ms": elapsed_ms,
                },
            )
        self._logger.info(
            "image put: %s -> %s (%d bytes%s, %.1fms)",
            image_id, out_path, len(payload),
            ", replaced" if old_row is not None else "", elapsed_ms,
        )
        return record

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, image_id: str) -> ImageRecord | None:
        """按 image_id 查找索引记录（miss 返回 None，不抛错）。"""
        if not isinstance(image_id, str) or not image_id:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT image_id, file_path, collection, doc_hash, page_num, created_at
                FROM image_index WHERE image_id = ?
                """,
                (image_id,),
            ).fetchone()
        return ImageRecord.from_row(row) if row is not None else None

    def list_images(
        self,
        collection: str | None = None,
        doc_hash: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[ImageRecord]:
        """按 collection / doc_hash 批量查询（皆缺省时返回全部）。

        Returns:
            按 ``image_id`` 升序的记录列表（确定性排序）。

        Raises:
            ImageStorageError: ``limit`` 非 None 且非正整数时。
        """
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ImageStorageError(f"limit must be a positive int or None, got {limit!r}")
        clauses: list[str] = []
        params: list[Any] = []
        if collection is not None:
            clauses.append("collection = ?")
            params.append(collection)
        if doc_hash is not None:
            clauses.append("doc_hash = ?")
            params.append(doc_hash)
        query = (
            "SELECT image_id, file_path, collection, doc_hash, page_num, created_at "
            "FROM image_index"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY image_id ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [ImageRecord.from_row(row) for row in rows]

    def count(self, collection: str | None = None, doc_hash: str | None = None) -> int:
        """按过滤条件统计索引行数（皆缺省时为全表计数）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if collection is not None:
            clauses.append("collection = ?")
            params.append(collection)
        if doc_hash is not None:
            clauses.append("doc_hash = ?")
            params.append(doc_hash)
        query = "SELECT COUNT(*) FROM image_index"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------

    def delete(self, image_id: str) -> bool:
        """删除单张图片（索引行 + 文件）；不存在时返回 False 且无副作用。"""
        if not isinstance(image_id, str) or not image_id:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT file_path FROM image_index WHERE image_id = ?", (image_id,)
            ).fetchone()
            if row is None:
                return False
            self._conn.execute("DELETE FROM image_index WHERE image_id = ?", (image_id,))
            self._conn.commit()
        self._unlink_quietly(Path(row[0]))
        self._logger.info("image deleted: %s", image_id)
        return True

    def delete_images(
        self,
        collection: str | None = None,
        doc_hash: str | None = None,
        *,
        trace: TraceContext | None = None,
    ) -> int:
        """按 collection / doc_hash 批量删除图片（至少一个过滤条件）。

        Returns:
            实际删除的记录数。

        Raises:
            ImageStorageError: 两个过滤条件皆缺（防全表误删）。
        """
        started = time.perf_counter()
        if collection is None and doc_hash is None:
            raise ImageStorageError(
                "delete_images requires at least one of collection or doc_hash (refuse to wipe all)"
            )
        clauses: list[str] = []
        params: list[Any] = []
        if collection is not None:
            clauses.append("collection = ?")
            params.append(collection)
        if doc_hash is not None:
            clauses.append("doc_hash = ?")
            params.append(doc_hash)
        with self._lock:
            rows = self._conn.execute(
                "SELECT image_id, file_path FROM image_index WHERE " + " AND ".join(clauses),
                params,
            ).fetchall()
            self._conn.execute(
                "DELETE FROM image_index WHERE " + " AND ".join(clauses), params
            )
            self._conn.commit()
        for row in rows:
            self._unlink_quietly(Path(row[1]))
        if trace is not None:
            trace.record_stage(
                "storage",
                method="image_storage",
                details={
                    "action": "delete_images",
                    "deleted": len(rows),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
        if rows:
            self._logger.info("deleted %d images (collection=%r, doc_hash=%r)", len(rows), collection, doc_hash)
        return len(rows)

    def close(self) -> None:
        """释放底层 SQLite 连接。"""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_id_like(value: Any, name: str) -> None:
        """校验用作 id/目录名的字符串：非空 str 且不含路径分隔符。"""
        if not isinstance(value, str) or not value:
            raise ImageStorageError(f"{name} must be a non-empty string, got {value!r}")
        for forbidden in _FORBIDDEN_ID_CHARS:
            if forbidden and forbidden in value:
                raise ImageStorageError(
                    f"{name} must not contain path separators (got {value!r})"
                )
        if value in (".", ".."):
            raise ImageStorageError(f"{name} must not be a relative path element: {value!r}")

    @staticmethod
    def _normalize_ext(ext: Any) -> str:
        """归一化扩展名为 ``.xxx`` 形式（空/非 str 为契约错误）。"""
        if not isinstance(ext, str) or not ext.strip():
            raise ImageStorageError(f"ext must be a non-empty string, got {ext!r}")
        ext = ext.strip()
        if any(f and f in ext for f in _FORBIDDEN_ID_CHARS):
            raise ImageStorageError(f"ext must not contain path separators (got {ext!r})")
        return ext if ext.startswith(".") else "." + ext

    @staticmethod
    def _write_atomic(path: Path, payload: bytes) -> None:
        """原子写文件（tmp + ``os.replace``，同 BM25Indexer.save 纪律）。"""
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_bytes(payload)
        os.replace(tmp_path, path)

    def _unlink_quietly(self, path: Path) -> None:
        """尽力删除文件（缺失/失败仅告警，不阻塞主流程）。"""
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - 文件系统异常兜底
            self._logger.warning("failed to remove image file %s: %s", path, exc)
