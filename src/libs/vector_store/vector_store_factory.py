"""VectorStore 工厂（阶段 B4）。

根据 ``Settings.vector_store.backend`` 动态实例化对应的 ``BaseVectorStore`` 实现。

注册机制与其它工厂一致：
- 各后端实现通过 ``VectorStoreFactory.register(backend)`` 装饰器登记构造函数。
- 工厂在 ``create`` 时按 ``settings.vector_store.backend`` 路由。
- 未知 backend 抛出可读错误（不泄露敏感配置）。

阶段 B4 仅定义工厂与注册机制；真实实现见 B7.6（ChromaStore）。
"""

from __future__ import annotations

from typing import Any, Callable

from core.settings import Settings, VectorStoreConfig
from libs.vector_store.base_vector_store import BaseVectorStore

__all__ = ["VectorStoreFactory", "VectorStoreBackendError"]


class VectorStoreBackendError(RuntimeError):
    """VectorStore backend 路由/构造错误。"""


class VectorStoreFactory:
    """VectorStore 工厂：按配置 backend 路由到具体实现。"""

    # backend 名 -> 构造函数 (vs_config: VectorStoreConfig, **kwargs) -> BaseVectorStore
    _registry: dict[str, Callable[..., BaseVectorStore]] = {}

    @classmethod
    def register(cls, backend: str) -> Callable[[Callable[..., BaseVectorStore]], Callable[..., BaseVectorStore]]:
        """装饰器：注册一个 backend 的构造函数。"""

        def decorator(ctor: Callable[..., BaseVectorStore]) -> Callable[..., BaseVectorStore]:
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
    def create(cls, settings: Settings, **kwargs: Any) -> BaseVectorStore:
        """根据 ``settings.vector_store.backend`` 创建 ``BaseVectorStore`` 实例。

        Raises:
            VectorStoreBackendError: backend 未注册时。
        """
        vs_config: VectorStoreConfig = settings.vector_store
        backend = vs_config.backend
        ctor = cls._registry.get(backend)
        if ctor is None:
            raise VectorStoreBackendError(
                f"unknown vector_store backend: {backend!r}. "
                f"registered: {cls.available_backends() or '(none)'}."
            )
        return ctor(vs_config, **kwargs)
