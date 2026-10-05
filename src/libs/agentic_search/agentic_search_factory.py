"""Agentic 检索工厂 — 阶段 S1.2。

与 ``libs/*`` 其它工厂同一注册机制：后端实现通过
``AgenticSearchFactory.register(backend)`` 装饰器登记构造函数，
``create`` 按 ``settings.agentic_search.backend`` 路由。

注册方：
- ``sirchmunk_http``（S1.4，HTTP sidecar，唯一生产后端）
- ``fake``（S1.2，确定性测试后端；测试注入用，不作生产选择）
"""

from __future__ import annotations

from typing import Any, Callable

from core.settings import AgenticSearchConfig, Settings
from libs.agentic_search.base_agentic_search import BaseAgenticSearch

__all__ = ["AgenticSearchFactory", "AgenticSearchBackendRouteError"]


class AgenticSearchBackendRouteError(RuntimeError):
    """Agentic 检索 backend 路由/构造错误。"""


class AgenticSearchFactory:
    """Agentic 检索工厂：按配置 backend 路由到具体实现。"""

    # backend 名 -> 构造函数 (agentic_config: AgenticSearchConfig, **kwargs) -> BaseAgenticSearch
    _registry: dict[str, Callable[..., BaseAgenticSearch]] = {}

    @classmethod
    def register(cls, backend: str) -> Callable[[Callable[..., BaseAgenticSearch]], Callable[..., BaseAgenticSearch]]:
        """装饰器：注册一个 backend 的构造函数。"""

        def decorator(ctor: Callable[..., BaseAgenticSearch]) -> Callable[..., BaseAgenticSearch]:
            cls._registry[backend] = ctor
            return ctor

        return decorator

    @classmethod
    def is_registered(cls, backend: str) -> bool:
        return backend in cls._registry

    @classmethod
    def available_backends(cls) -> list[str]:
        return sorted(cls._registry)

    @classmethod
    def create(cls, settings: Settings, **kwargs: Any) -> BaseAgenticSearch:
        """根据 ``settings.agentic_search.backend`` 创建 ``BaseAgenticSearch`` 实例。

        Raises:
            AgenticSearchBackendRouteError: backend 未注册时。
        """
        agentic_config: AgenticSearchConfig = settings.agentic_search
        backend = agentic_config.backend
        ctor = cls._registry.get(backend)
        if ctor is None:
            raise AgenticSearchBackendRouteError(
                f"unknown agentic_search backend: {backend!r}. "
                f"registered: {cls.available_backends() or '(none)'}."
            )
        return ctor(agentic_config, **kwargs)
