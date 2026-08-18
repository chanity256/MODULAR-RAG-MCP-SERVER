"""ConfigService：封装 Settings 读取与组件卡片格式化（阶段 G1）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.settings import Settings, load_settings

__all__ = ["ComponentCard", "ConfigService"]

_DEFAULT_CONFIG = Path(__file__).resolve().parents[4] / "config" / "settings.yaml"


@dataclass(frozen=True)
class ComponentCard:
    """系统总览页的一张组件配置卡片。"""

    name: str
    provider: str
    model: str | None = None
    extras: dict[str, Any] | None = None

    def headline(self) -> str:
        if self.model:
            return f"{self.provider} / {self.model}"
        return self.provider


class ConfigService:
    """读取 Settings 并格式化为 Dashboard 可展示的组件信息。"""

    def __init__(self, settings: Settings | None = None, *, config_path: str | Path | None = None) -> None:
        self._settings = settings
        self._config_path = Path(config_path) if config_path is not None else _DEFAULT_CONFIG

    def get_settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings(self._config_path)
        return self._settings

    def component_cards(self) -> list[ComponentCard]:
        settings = self.get_settings()
        rerank_model = settings.rerank.model
        if settings.rerank.backend == "none":
            rerank_model = "None"
        return [
            ComponentCard(
                name="LLM",
                provider=settings.llm.provider,
                model=settings.llm.model,
            ),
            ComponentCard(
                name="Embedding",
                provider=settings.embedding.provider,
                model=settings.embedding.model,
                extras={"dimensions": settings.embedding.dimensions},
            ),
            ComponentCard(
                name="Splitter",
                provider=settings.splitter.provider,
                extras={
                    "chunk_size": settings.splitter.chunk_size,
                    "chunk_overlap": settings.splitter.chunk_overlap,
                },
            ),
            ComponentCard(
                name="Reranker",
                provider=settings.rerank.backend,
                model=rerank_model,
            ),
            ComponentCard(
                name="Evaluator",
                provider=", ".join(settings.evaluation.backends),
            ),
        ]
