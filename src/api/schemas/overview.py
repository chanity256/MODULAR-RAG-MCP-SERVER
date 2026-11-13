"""系统总览模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from api.schemas.collections import CollectionStatsModel
from api.schemas.traces import TraceSummary

__all__ = ["ComponentCardModel", "OverviewResponse", "OverviewTotals"]


class ComponentCardModel(BaseModel):
    name: str
    provider: str
    model: str | None = None
    headline: str
    extras: dict[str, Any] = Field(default_factory=dict)


class OverviewTotals(BaseModel):
    collections: int = 0
    documents: int = 0
    chunks: int = 0
    images: int = 0


class OverviewResponse(BaseModel):
    components: list[ComponentCardModel]
    totals: OverviewTotals
    collections: list[CollectionStatsModel]
    latest_ingestion: TraceSummary | None = None
    latest_query: TraceSummary | None = None
    warnings: list[str] = Field(default_factory=list, description="Non-fatal read failures")
