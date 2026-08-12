"""LLM 工厂（阶段 B1）。

根据 ``Settings.llm.provider`` 动态实例化对应的 ``BaseLLM`` 实现，
实现"一处配置，处处生效"的可插拔切换。

注册机制：
- 各 provider 实现通过 ``LLMFactory.register(provider)`` 装饰器登记构造函数。
- 工厂在 ``create`` 时按 ``settings.llm.provider`` 路由。
- 未知 provider 抛出可读错误（不泄露敏感配置）。

阶段 B1 仅定义工厂与注册机制；真实 provider 实现见 B7.1（OpenAI-compatible）/
B7.2（Ollama）/ B8（Vision）。
"""

from __future__ import annotations

from typing import Any, Callable

from core.settings import LLMConfig, Settings
from libs.llm.base_llm import BaseLLM

__all__ = ["LLMFactory", "LLMProviderError"]


class LLMProviderError(RuntimeError):
    """LLM provider 路由/构造错误。"""


class LLMFactory:
    """LLM 工厂：按配置 provider 路由到具体实现。"""

    # provider 名 -> 构造函数 (llm_config: LLMConfig, **kwargs) -> BaseLLM
    _registry: dict[str, Callable[..., BaseLLM]] = {}

    @classmethod
    def register(cls, provider: str) -> Callable[[Callable[..., BaseLLM]], Callable[..., BaseLLM]]:
        """装饰器：注册一个 provider 的构造函数。"""

        def decorator(ctor: Callable[..., BaseLLM]) -> Callable[..., BaseLLM]:
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
    def create(cls, settings: Settings, **kwargs: Any) -> BaseLLM:
        """根据 ``settings.llm.provider`` 创建 ``BaseLLM`` 实例。

        Args:
            settings: 全局配置。
            **kwargs: 透传给 provider 构造函数的额外参数。

        Raises:
            LLMProviderError: provider 未注册时。
        """
        llm_config: LLMConfig = settings.llm
        provider = llm_config.provider
        ctor = cls._registry.get(provider)
        if ctor is None:
            raise LLMProviderError(
                f"unknown LLM provider: {provider!r}. "
                f"registered: {cls.available_providers() or '(none)'}."
            )
        return ctor(llm_config, **kwargs)
