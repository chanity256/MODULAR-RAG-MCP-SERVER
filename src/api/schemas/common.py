"""跨路由共用的响应模型。"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

__all__ = ["ErrorBody", "ErrorResponse", "Page"]

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorBody


class Page(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    total: int = Field(description="Total matches before pagination")
    limit: int
    offset: int
