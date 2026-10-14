"""Trace 列表 / 详情模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["StageRecord", "StageTiming", "TraceDetail", "TraceSummary"]


class StageRecord(BaseModel):
    """trace 中的一个阶段；未建模字段原样保留（各阶段形态不同）。"""

    model_config = ConfigDict(extra="allow")

    stage: str = ""
    elapsed_ms: float | None = None
    method: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details", mode="before")
    @classmethod
    def _none_details_as_empty(cls, value: Any) -> Any:
        return {} if value is None else value


class StageTiming(BaseModel):
    stage: str
    elapsed_ms: float


class TraceSummary(BaseModel):
    trace_id: str
    trace_type: str
    started_at: str | None = None
    finished_at: str | None = None
    total_elapsed_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    stage_count: int = 0
    query: str | None = None
    source_path: str | None = None
    collection: str | None = None
    routes: str | None = None
    skipped: bool | None = Field(default=None, description="Ingestion only: integrity check skipped the file")
    chunks: int | None = Field(default=None, description="Ingestion only: chunks written by the store stage")
    images: int | None = Field(default=None, description="Ingestion only: images written by the store stage")

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "TraceSummary":
        metadata = record.get("metadata") or {}
        stages = record.get("stages") or []
        is_ingestion = record.get("trace_type") == "ingestion"

        def _details(name: str) -> dict[str, Any]:
            for item in reversed(stages):
                if item.get("stage") == name:
                    return item.get("details") or {}
            return {}

        source_path = metadata.get("source_path") or _details("load").get("source_path")
        store = _details("store") if is_ingestion else {}
        return cls(
            trace_id=str(record.get("trace_id")),
            trace_type=str(record.get("trace_type") or ""),
            started_at=record.get("started_at"),
            finished_at=record.get("finished_at"),
            total_elapsed_ms=record.get("total_elapsed_ms"),
            metadata=metadata,
            stage_count=len(stages),
            query=str(metadata["query"]) if metadata.get("query") is not None else None,
            source_path=str(source_path) if source_path else None,
            collection=metadata.get("collection"),
            routes=metadata.get("routes"),
            skipped=bool(_details("integrity").get("skipped")) if is_ingestion else None,
            chunks=store.get("chunks"),
            images=store.get("images"),
        )


class TraceDetail(TraceSummary):
    stages: list[StageRecord] = Field(default_factory=list)
    timings: list[StageTiming] = Field(default_factory=list)
