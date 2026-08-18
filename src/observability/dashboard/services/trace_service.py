"""TraceService：解析 traces.jsonl，按 trace_type 分类（阶段 G5）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.settings import Settings

__all__ = ["TraceService"]

_DEFAULT_TRACES = Path("logs/traces.jsonl")


class TraceService:
    """读取 JSON Lines trace 日志，供 Ingestion / Query 追踪页使用。"""

    def __init__(
        self,
        traces_path: str | Path | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        if traces_path is not None:
            self._path = Path(traces_path)
        elif settings is not None:
            self._path = Path(settings.observability.log_file)
        else:
            self._path = _DEFAULT_TRACES

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

    def search_query_traces(self, keyword: str) -> list[dict[str, Any]]:
        needle = (keyword or "").strip().lower()
        traces = self.list_traces("query")
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
        if not self._path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
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
