"""健康检查模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["HealthResponse", "SidecarHealth", "StorageHealth"]


class SidecarHealth(BaseModel):
    status: Literal["ok", "unreachable", "disabled", "unknown"]
    enabled: bool = False


class StorageHealth(BaseModel):
    status: Literal["ok", "degraded", "unknown"]
    components: dict[str, bool] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    sidecar: SidecarHealth
    storage: StorageHealth
