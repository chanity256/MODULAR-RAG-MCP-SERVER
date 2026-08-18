"""Evaluator 工厂（阶段 B6）。

按名称实例化对应的 ``BaseEvaluator`` 实现。

注册机制与其它工厂一致：
- 各评估器实现通过 ``EvaluatorFactory.register(name)`` 装饰器登记构造函数。
- ``custom`` 评估器在工厂初始化时默认注册。
- ``create(name)`` 按名称路由，未知名称抛出可读错误。
- ``create_from_settings(settings)`` 按 ``settings.evaluation.backends`` 批量创建
  （供阶段 H2 CompositeEvaluator 组合使用）。

真实框架适配见 H1（RagasEvaluator）。
"""

from __future__ import annotations

from typing import Any, Callable

from core.settings import Settings
from libs.evaluator.base_evaluator import BaseEvaluator
from libs.evaluator.custom_evaluator import CustomEvaluator
from observability.evaluation.ragas_evaluator import RagasEvaluator

__all__ = ["EvaluatorFactory", "EvaluatorBackendError"]


class EvaluatorBackendError(RuntimeError):
    """Evaluator backend 路由/构造错误。"""


def _custom_ctor(**kwargs: Any) -> CustomEvaluator:
    return CustomEvaluator()


def _ragas_ctor(**kwargs: Any) -> RagasEvaluator:
    return RagasEvaluator(**kwargs)


class EvaluatorFactory:
    """Evaluator 工厂：按名称路由到具体实现。"""

    # name -> 构造函数 (**kwargs) -> BaseEvaluator
    _registry: dict[str, Callable[..., BaseEvaluator]] = {
        "custom": _custom_ctor,
        "ragas": _ragas_ctor,
    }

    @classmethod
    def register(cls, name: str) -> Callable[[Callable[..., BaseEvaluator]], Callable[..., BaseEvaluator]]:
        """装饰器：注册一个评估器的构造函数。"""

        def decorator(ctor: Callable[..., BaseEvaluator]) -> Callable[..., BaseEvaluator]:
            cls._registry[name] = ctor
            return ctor

        return decorator

    @classmethod
    def is_registered(cls, name: str) -> bool:
        return name in cls._registry

    @classmethod
    def available_backends(cls) -> list[str]:
        return sorted(cls._registry)

    @classmethod
    def create(cls, name: str, **kwargs: Any) -> BaseEvaluator:
        """按名称创建 ``BaseEvaluator`` 实例。

        Raises:
            EvaluatorBackendError: name 未注册时。
        """
        ctor = cls._registry.get(name)
        if ctor is None:
            raise EvaluatorBackendError(
                f"unknown evaluator backend: {name!r}. "
                f"registered: {cls.available_backends() or '(none)'}."
            )
        return ctor(**kwargs)

    @classmethod
    def create_from_settings(cls, settings: Settings, **kwargs: Any) -> list[BaseEvaluator]:
        """按 ``settings.evaluation.backends`` 批量创建评估器列表。

        Raises:
            EvaluatorBackendError: 任一 backend 未注册时。
        """
        backends = settings.evaluation.backends
        return [cls.create(name, **kwargs) for name in backends]
