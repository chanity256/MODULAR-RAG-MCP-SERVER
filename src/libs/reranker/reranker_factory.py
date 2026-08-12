"""Reranker 工厂（阶段 B5）。

根据 ``Settings.rerank.backend`` 动态实例化对应的 ``BaseReranker`` 实现。

注册机制与其它工厂一致：
- 各后端实现通过 ``RerankerFactory.register(backend)`` 装饰器登记构造函数。
- 工厂在 ``create`` 时按 ``settings.rerank.backend`` 路由。
- ``none`` 后端始终可用（``NoneReranker`` 在此处注册）。
- 未知 backend 抛出可读错误（不泄露敏感配置）。

真实后端实现见 B7.7（LLM Reranker）/ B7.8（Cross-Encoder Reranker）。
"""

from __future__ import annotations

from typing import Any, Callable

from core.settings import RerankConfig, Settings
from libs.reranker.base_reranker import BaseReranker, NoneReranker

__all__ = ["RerankerFactory", "RerankerBackendError"]


class RerankerBackendError(RuntimeError):
    """Reranker backend 路由/构造错误。"""


def _none_reranker_ctor(rerank_config: RerankConfig, **kwargs: Any) -> NoneReranker:
    return NoneReranker()


class RerankerFactory:
    """Reranker 工厂：按配置 backend 路由到具体实现。"""

    # backend 名 -> 构造函数 (rerank_config: RerankConfig, **kwargs) -> BaseReranker
    _registry: dict[str, Callable[..., BaseReranker]] = {
        "none": _none_reranker_ctor,
    }

    @classmethod
    def register(cls, backend: str) -> Callable[[Callable[..., BaseReranker]], Callable[..., BaseReranker]]:
        """装饰器：注册一个 backend 的构造函数。"""

        def decorator(ctor: Callable[..., BaseReranker]) -> Callable[..., BaseReranker]:
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
    def create(cls, settings: Settings, **kwargs: Any) -> BaseReranker:
        """根据 ``settings.rerank.backend`` 创建 ``BaseReranker`` 实例。

        Raises:
            RerankerBackendError: backend 未注册时。
        """
        rerank_config: RerankConfig = settings.rerank
        backend = rerank_config.backend
        ctor = cls._registry.get(backend)
        if ctor is None:
            raise RerankerBackendError(
                f"unknown rerank backend: {backend!r}. "
                f"registered: {cls.available_backends() or '(none)'}."
            )
        return ctor(rerank_config, **kwargs)
