"""TraceService：解析 traces.jsonl，按 trace_type 分类（阶段 G5）。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from core.settings import Settings
from observability.log_files import resolve_log_path

__all__ = ["KNOWN_SOURCES", "SOURCE_DEFAULT", "TraceService"]

_DEFAULT_TRACES = Path("logs/traces.jsonl")

#: 来源标签：``metadata.routes`` 缺省（CLI / query_knowledge_hub）为 default。
SOURCE_DEFAULT = "default"
#: 页面筛选器固定提供的来源（即使当前日志里还没有该来源的 trace）。
KNOWN_SOURCES: tuple[str, ...] = (SOURCE_DEFAULT, "agentic", "chat")


class TraceService:
    """读取 JSON Lines trace 日志，供 Ingestion / Query 追踪页使用。"""

    def __init__(
        self,
        traces_path: str | Path | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        if traces_path is not None:
            self._path = resolve_log_path(traces_path)
        elif settings is not None:
            self._path = resolve_log_path(settings.observability.log_file)
        else:
            self._path = resolve_log_path(_DEFAULT_TRACES)
        self._cache_lock = threading.Lock()
        self._cache_key: tuple[int, int] | None = None
        self._cache_records: list[dict[str, Any]] = []

    @property
    def path(self) -> Path:
        return self._path

    def list_traces(self, trace_type: str | None = None) -> list[dict[str, Any]]:
        """按时间倒序返回 trace 列表（最新在前）。"""
        records = self._load()
        if trace_type:
            records = [item for item in records if item.get("trace_type") == trace_type]
        records.sort(key=lambda item: item.get("started_at") or "", reverse=True)
        return records

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        if not trace_id:
            return None
        for item in self._load():
            if item.get("trace_id") == trace_id:
                return item
        return None

    def latest(self, trace_type: str) -> dict[str, Any] | None:
        traces = self.list_traces(trace_type)
        return traces[0] if traces else None

    @staticmethod
    def trace_source(trace: dict[str, Any]) -> str:
        """trace 来源标签：取 ``metadata.routes``（``chat`` / ``agentic`` ...），缺省为 ``default``。"""
        routes = (trace.get("metadata") or {}).get("routes")
        return str(routes).strip() if isinstance(routes, str) and routes.strip() else SOURCE_DEFAULT

    def query_sources(self) -> list[str]:
        """筛选器可选来源：固定的 KNOWN_SOURCES + 日志里出现过的其它来源。"""
        seen = {self.trace_source(item) for item in self.list_traces("query")}
        return [*KNOWN_SOURCES, *sorted(seen - set(KNOWN_SOURCES))]

    def search_query_traces(self, keyword: str, source: str | None = None) -> list[dict[str, Any]]:
        """按关键词（query / trace_id）与可选来源筛选 query trace；``source`` 为空表示不筛选。"""
        needle = (keyword or "").strip().lower()
        traces = self.list_traces("query")
        if source:
            traces = [item for item in traces if self.trace_source(item) == source]
        if not needle:
            return traces
        matched: list[dict[str, Any]] = []
        for item in traces:
            metadata = item.get("metadata") or {}
            query = str(metadata.get("query") or "")
            if needle in query.lower() or needle in str(item.get("trace_id") or "").lower():
                matched.append(item)
        return matched

    def stage_timings(self, trace: dict[str, Any], stages: list[str] | None = None) -> list[dict[str, Any]]:
        """按指定阶段名提取耗时（同名阶段累加，用于瀑布图）。"""
        wanted = list(stages or [])
        totals: dict[str, float] = {name: 0.0 for name in wanted}
        extras: list[dict[str, Any]] = []
        for record in trace.get("stages") or []:
            name = str(record.get("stage") or "")
            elapsed = float(record.get("elapsed_ms") or (record.get("details") or {}).get("elapsed_ms") or 0.0)
            if wanted and name in totals:
                totals[name] += elapsed
            elif not wanted:
                extras.append({"stage": name, "elapsed_ms": elapsed, "method": record.get("method")})
        if wanted:
            return [{"stage": name, "elapsed_ms": totals[name]} for name in wanted]
        return extras

    def _load(self) -> list[dict[str, Any]]:
        """解析 traces.jsonl；以 (mtime_ns, size) 为键缓存，文件变化即失效。

        返回新列表（调用方可原地排序），其中的 trace dict 与缓存共享，调用方只读。
        """
        try:
            stat = self._path.stat()
        except OSError:
            return []
        if not self._path.is_file():
            return []
        key = (stat.st_mtime_ns, stat.st_size)
        with self._cache_lock:
            if self._cache_key == key:
                return list(self._cache_records)
        try:
            records = self._parse(self._path.read_text(encoding="utf-8"))
        except OSError:
            return []
        with self._cache_lock:
            self._cache_key = key
            self._cache_records = records
        return list(records)

    @staticmethod
    def _parse(text: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            # JSONFormatter 把 extra 合并到顶层；兼容 {message, extra} 两种形态。
            if "trace_id" not in payload and isinstance(payload.get("extra"), dict):
                payload = dict(payload["extra"])
            if payload.get("trace_id"):
                records.append(payload)
        return records
