"""Query Playground 协议模型：``POST /search`` 与 ``GET /search/capabilities``（WC9.4）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

__all__ = ["ModeStatus", "SearchCapabilities", "SearchRequest", "SearchResponse"]

SearchMode = Literal["normal", "fast", "deep"]


class SearchRequest(BaseModel):
    # 拼错参数名必须被拒绝而不是被静默忽略（与 chat 上行帧同一纪律）。
    model_config = ConfigDict(extra="forbid")

    mode: SearchMode = Field(default="normal", description="normal = hybrid retrieval; fast/deep = agentic sidecar")
    query: StrictStr = Field(min_length=1, max_length=4000)
    top_k: StrictInt | None = Field(default=None, ge=1, le=100)
    collection: StrictStr | None = Field(
        default=None, max_length=256, description="Only applied in normal mode; ignored by fast/deep"
    )

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @field_validator("collection")
    @classmethod
    def _blank_collection_as_none(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class SearchResponse(BaseModel):
    mode: SearchMode
    query: str
    elapsed_ms: float
    markdown: str = Field(description="Answer Markdown; the raw error text when is_error")
    structured: dict[str, Any] = Field(
        default_factory=dict,
        description="structuredContent subset kept by SearchService: {citations: [...], agentic?: {...telemetry}}",
    )
    is_error: bool = False
    error: str | None = None


class ModeStatus(BaseModel):
    available: bool
    reason: str | None = None


class SearchCapabilities(BaseModel):
    modes: list[SearchMode] = Field(description="Currently usable modes; normal is always present")
    default_mode: SearchMode = "normal"
    agentic_enabled: bool = Field(description="agentic_search.enabled (fast available)")
    allow_deep: bool = Field(description="agentic_search.allow_deep AND enabled (deep available)")
    availability: dict[str, ModeStatus]
