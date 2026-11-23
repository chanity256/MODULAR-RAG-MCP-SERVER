"""树检索工厂 — 阶段 PI3（PAGEINDEX_TREE_RETRIEVAL_SPEC §3.2）。

与 ``libs/*`` 其它工厂同一注册机制：后端实现通过
``TreeSearchFactory.register(backend)`` 装饰器登记构造函数，
``create`` 按 ``settings.tree_search.backend`` 路由。

注册方：
- ``pageindex_http``（PI3，HTTP sidecar，唯一生产后端）
- ``fake``（PI3，确定性测试后端；测试注入用，不作生产选择）
"""

from __future__ import annotations

from typing import Any, Callable

from core.settings import Settings, TreeSearchConfig
from libs.tree_search.base_tree_search import BaseTreeSearch

__all__ = ["TreeSearchFactory", "TreeSearchBackendRouteError"]


class TreeSearchBackendRouteError(RuntimeError):
    """树检索 backend 路由/构造错误。"""


class TreeSearchFactory:
    """树检索工厂：按配置 backend 路由到具体实现。"""

    # backend 名 -> 构造函数 (tree_config: TreeSearchConfig, **kwargs) -> BaseTreeSearch
    _registry: dict[str, Callable[..., BaseTreeSearch]] = {}

    @classmethod
    def register(cls, backend: str) -> Callable[[Callable[..., BaseTreeSearch]], Callable[..., BaseTreeSearch]]:
        """装饰器：注册一个 backend 的构造函数。"""

        def decorator(ctor: Callable[..., BaseTreeSearch]) -> Callable[..., BaseTreeSearch]:
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
    def create(cls, settings: Settings, **kwargs: Any) -> BaseTreeSearch:
        """根据 ``settings.tree_search.backend`` 创建 ``BaseTreeSearch`` 实例。

        Raises:
            TreeSearchBackendRouteError: backend 未注册时。
        """
        tree_config: TreeSearchConfig = settings.tree_search
        backend = tree_config.backend
        ctor = cls._registry.get(backend)
        if ctor is None:
            raise TreeSearchBackendRouteError(
                f"unknown tree_search backend: {backend!r}. "
                f"registered: {cls.available_backends() or '(none)'}."
            )
        return ctor(tree_config, **kwargs)
