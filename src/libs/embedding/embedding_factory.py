"""Embedding 工厂（阶段 B2）。

根据 ``Settings.embedding.provider`` 动态实例化对应的 ``BaseEmbedding`` 实现。

注册机制与 ``LLMFactory`` 一致：
- 各 provider 实现通过 ``EmbeddingFactory.register(provider)`` 装饰器登记构造函数。
- 工厂在 ``create`` 时按 ``settings.embedding.provider`` 路由。
- 未知 provider 抛出可读错误（不泄露敏感配置）。

阶段 B2 仅定义工厂与注册机制；真实 provider 实现见 B7.3（OpenAI & Azure）/
B7.4（Ollama）。
"""

from __future__ import annotations

from typing import Any, Callable

from core.settings import EmbeddingConfig, Settings
from libs.embedding.base_embedding import BaseEmbedding

__all__ = ["EmbeddingFactory", "EmbeddingProviderError"]


class EmbeddingProviderError(RuntimeError):
    """Embedding provider 路由/构造错误。"""


class EmbeddingFactory:
    """Embedding 工厂：按配置 provider 路由到具体实现。"""

    # provider 名 -> 构造函数 (emb_config: EmbeddingConfig, **kwargs) -> BaseEmbedding
    _registry: dict[str, Callable[..., BaseEmbedding]] = {}

    @classmethod
    def register(cls, provider: str) -> Callable[[Callable[..., BaseEmbedding]], Callable[..., BaseEmbedding]]:
        """装饰器：注册一个 provider 的构造函数。"""

        def decorator(ctor: Callable[..., BaseEmbedding]) -> Callable[..., BaseEmbedding]:
            cls._registry[provider] = ctor
            return ctor

        return decorator

    @classmethod
    def is_registered(cls, provider: str) -> bool:
        return provider in cls._registry

    @classmethod
    def available_providers(cls) -> list[str]:
        return sorted(cls._registry)

    @classmethod
    def create(cls, settings: Settings, **kwargs: Any) -> BaseEmbedding:
        """根据 ``settings.embedding.provider`` 创建 ``BaseEmbedding`` 实例。

        Raises:
            EmbeddingProviderError: provider 未注册时。
        """
        emb_config: EmbeddingConfig = settings.embedding
        provider = emb_config.provider
        ctor = cls._registry.get(provider)
        if ctor is None:
            raise EmbeddingProviderError(
                f"unknown embedding provider: {provider!r}. "
                f"registered: {cls.available_providers() or '(none)'}."
            )
        return ctor(emb_config, **kwargs)
