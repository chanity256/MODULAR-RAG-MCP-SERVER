"""Splitter 工厂（阶段 B3）。

根据 ``Settings.splitter.provider`` 动态实例化对应的 ``BaseSplitter`` 实现。

注册机制与 ``LLMFactory`` / ``EmbeddingFactory`` 一致：
- 各策略实现通过 ``SplitterFactory.register(provider)`` 装饰器登记构造函数。
- 工厂在 ``create`` 时按 ``settings.splitter.provider`` 路由。
- 未知 provider 抛出可读错误（不泄露敏感配置）。

阶段 B3 仅定义工厂与注册机制；真实策略实现见 B7.5（Recursive）等。
"""

from __future__ import annotations

from typing import Any, Callable

from core.settings import Settings, SplitterConfig
from libs.splitter.base_splitter import BaseSplitter

__all__ = ["SplitterFactory", "SplitterProviderError"]


class SplitterProviderError(RuntimeError):
    """Splitter provider 路由/构造错误。"""


class SplitterFactory:
    """Splitter 工厂：按配置 provider 路由到具体实现。"""

    # provider 名 -> 构造函数 (splitter_config: SplitterConfig, **kwargs) -> BaseSplitter
    _registry: dict[str, Callable[..., BaseSplitter]] = {}

    @classmethod
    def register(cls, provider: str) -> Callable[[Callable[..., BaseSplitter]], Callable[..., BaseSplitter]]:
        """装饰器：注册一个 provider 的构造函数。"""

        def decorator(ctor: Callable[..., BaseSplitter]) -> Callable[..., BaseSplitter]:
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
    def create(cls, settings: Settings, **kwargs: Any) -> BaseSplitter:
        """根据 ``settings.splitter.provider`` 创建 ``BaseSplitter`` 实例。

        Raises:
            SplitterProviderError: provider 未注册时。
        """
        splitter_config: SplitterConfig = settings.splitter
        provider = splitter_config.provider
        ctor = cls._registry.get(provider)
        if ctor is None:
            raise SplitterProviderError(
                f"unknown splitter provider: {provider!r}. "
                f"registered: {cls.available_providers() or '(none)'}."
            )
        return ctor(splitter_config, **kwargs)
