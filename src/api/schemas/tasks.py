"""后台任务与写接口的请求/响应模型（WC4）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DeleteDocumentResponse",
    "EvaluationRequest",
    "IngestionRequest",
    "TaskModel",
    "TaskSubmitted",
    "UploadResponse",
    "UploadedFile",
]

TaskStatusLiteral = Literal["pending", "running", "succeeded", "failed"]


class TaskModel(BaseModel):
    """任务快照。

    ``detail`` 约定键：``params``（提交参数）、``progress``（``{stage, current, total}``）、
    ``result``（成功/部分结果）、``error``（``{type, message}``）。
    """

    task_id: str
    kind: str
    status: TaskStatusLiteral
    version: int = Field(description="Monotonic per task; use it to de-duplicate events")
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


class TaskSubmitted(BaseModel):
    task_id: str
    status: TaskStatusLiteral
    task: TaskModel


class UploadedFile(BaseModel):
    name: str = Field(description="Stored basename; pass it to POST /ingestions")
    size: int


class UploadResponse(BaseModel):
    files: list[UploadedFile]


class IngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(min_length=1, description="Basenames returned by POST /uploads")
    collection: str = Field(default="default", min_length=1)
    force: bool = False


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_set_path: str | None = Field(default=None, description="Defaults to evaluation.golden_test_set")
    backend: Literal["composite", "custom", "ragas"] = "composite"
    top_k: int | None = Field(default=None, ge=1)
    filters: dict[str, Any] | None = None


class DeleteDocumentResponse(BaseModel):
    source_path: str
    collection: str
    chroma_deleted: int = 0
    bm25_deleted: int = 0
    images_deleted: int = 0
    integrity_removed: bool = False
