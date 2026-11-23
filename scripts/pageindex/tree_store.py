"""PageIndex sidecar 文档映射存储（PI1，纯标准库）。

树本体在上游 DocStore，本模块只存「source_path ↔ doc_id ↔ file_hash ↔
derived_pdf」映射与 MD 标题索引缓存（规格 §3.2）。JSON 文件 + 原子写
（tmp + ``replace``）+ 单进程 ``threading.Lock``；按 ``source_path`` 为键
幂等（D5 对账同步按 ``file_hash`` 变化重建、历史不存在即删除，删除 MD
记录时返回待删派生 PDF）。
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path

REQUIRED_FIELDS = ("source_path", "file_hash", "format")


class TreeStore:
    """JSON 映射存储。``format`` ∈ {"pdf", "md"}；MD 记录可带 ``headings``
    （heading_index 解析结果缓存）与 ``derived_pdf``。"""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if not isinstance(data, dict):
                # 损坏文件（含合法 JSON 但非对象，如 [1, 2]）不阻断启动，
                # 但先把原件留存为 ``<name>.corrupt``
                # （10.9 #12：直接覆盖会让全部 source_path → doc_id / derived_pdf
                # 映射永久丢失，D5 对账随即全部重提交且上游不去重）
                try:
                    os.replace(self.path,
                               self.path.with_name(self.path.name + ".corrupt"))
                except OSError:
                    print(f"tree_store: corrupt store file could not be moved "
                          f"aside: {self.path}", file=sys.stderr)
                print(f"tree_store: corrupt store file moved aside, "
                      f"starting empty: {self.path}", file=sys.stderr)
                self._records = {}
            else:
                self._records = data

    # -- 内部 ---------------------------------------------------------------

    def _flush_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.parent / (self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._records, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _validate(record: dict) -> dict:
        for field in REQUIRED_FIELDS:
            if not record.get(field):
                raise ValueError(f"tree_store record missing {field}: {record!r}")
        if record["format"] not in ("pdf", "md"):
            raise ValueError(f"tree_store record bad format: {record['format']!r}")
        return record

    # -- API ----------------------------------------------------------------

    def get(self, source_path: str) -> dict | None:
        with self._lock:
            rec = self._records.get(source_path)
            # 深拷贝：嵌套的 headings 列表不得与内部存储共享（10.9 #20）
            return copy.deepcopy(rec) if rec else None

    def upsert(self, record: dict) -> dict:
        """按 ``source_path`` 幂等插入或更新，返回存储后的记录副本。"""
        self._validate(record)
        with self._lock:
            self._records[record["source_path"]] = copy.deepcopy(record)
            self._flush_locked()
            return copy.deepcopy(self._records[record["source_path"]])

    def remove(self, source_path: str) -> dict | None:
        """删除记录并返回其副本（含 ``derived_pdf``，调用方连带删除派生
        PDF，D5）；不存在返回 ``None``（幂等）。"""
        with self._lock:
            rec = self._records.pop(source_path, None)
            if rec is not None:
                self._flush_locked()
            return copy.deepcopy(rec) if rec else None

    def list_all(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(list(self._records.values()))

    def find_by_hash(self, file_hash: str) -> dict | None:
        """按内容哈希找记录（对账同步判断「file_hash 未变不重建」）。"""
        with self._lock:
            for rec in self._records.values():
                if rec.get("file_hash") == file_hash:
                    return copy.deepcopy(rec)
            return None
