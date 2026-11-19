"""决策路由器骨架（MODE_ROUTER_SPEC MR2）。

失败分型约定（``ModeRouterError.reason``，供引擎回退与 trace 使用）：
``timeout`` / ``unreachable`` / ``invalid_output`` / ``low_confidence`` / ``error``。
组件只负责把失败抛成分型异常；回退 ``fallback_retrieval`` 的语义归
ChatEngine（MR3），组件不做回退。
"""

from __future__ import annotations

import abc
from typing import Any

from core.chat.retriever import RoutingDecision
from libs.mode_router.routing_question import DEFAULT_WORDING

__all__ = ["ROUTER_ERROR_REASONS", "ModeRouterError", "BaseModeRouter"]

ROUTER_ERROR_REASONS = ("timeout", "unreachable", "invalid_output", "low_confidence", "error")


class ModeRouterError(Exception):
    """决策失败；``reason`` ∈ :data:`ROUTER_ERROR_REASONS`。"""

    def __init__(self, reason: str, message: str) -> None:
        if reason not in ROUTER_ERROR_REASONS:
            raise ValueError(f"unknown ModeRouterError reason: {reason!r}")
        super().__init__(message)
        self.reason = reason


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


class BaseModeRouter(abc.ABC):
    """决策路由器基类。

    子类实现 :meth:`_answers`（一次请求 + 解包，传输 / 包装失败抛
    :class:`ModeRouterError`）；候选内校验、数值归一与 D8 低置信度回退
    在本类统一完成，保证各后端行为一致。
    """

    def __init__(
        self,
        *,
        model: str,
        timeout_s: float,
        min_confidence: float | None = None,
        wording_lang: str = DEFAULT_WORDING,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.min_confidence = min_confidence
        self.wording_lang = wording_lang
        #: 最近一次 decide 的输入 token 数（后端返回 usage 时），供 MR3 trace 使用。
        self.last_input_tokens: int | None = None
        #: 后端标识（trace ``mode_routing.backend`` 用）；子类覆写。
        self.backend_name = "unknown"

    @abc.abstractmethod
    def _answers(self, query: str, candidates: tuple[str, ...]) -> dict[str, Any]:
        """发出一次决策请求，返回 ``answers.retrieval_mode`` 字典。"""

    def decide(self, query: str, candidates: tuple[str, ...]) -> RoutingDecision:
        """定档一次查询；成功返回 :class:`RoutingDecision`，失败抛 :class:`ModeRouterError`。

        - ``choice`` 必须属于 ``candidates``，否则 ``invalid_output``；
        - ``confidence`` / ``probabilities`` 必须是数值（缺省可接受），
          否则 ``invalid_output``；
        - ``min_confidence`` 非 None 且 ``confidence`` 已提供且低于阈值 →
          ``low_confidence``（D8）；``min_confidence`` 为 None 时不判。
        """
        if not candidates:
            raise ModeRouterError("invalid_output", "candidate set is empty")
        self.last_input_tokens = None
        answer = self._answers(query, tuple(candidates))

        choice = answer.get("choice")
        if choice not in candidates:
            raise ModeRouterError(
                "invalid_output", f"choice {choice!r} not in candidates {list(candidates)}"
            )

        confidence: float | None = None
        raw_confidence = answer.get("confidence")
        if raw_confidence is not None:
            if not _is_number(raw_confidence):
                raise ModeRouterError(
                    "invalid_output", f"confidence {raw_confidence!r} is not a number"
                )
            confidence = float(raw_confidence)

        probabilities: dict[str, float] | None = None
        raw_probabilities = answer.get("probabilities")
        if raw_probabilities is not None:
            if not isinstance(raw_probabilities, dict):
                raise ModeRouterError("invalid_output", "probabilities must be an object")
            probabilities = {}
            for key, value in raw_probabilities.items():
                if not _is_number(value):
                    raise ModeRouterError(
                        "invalid_output", f"probability for {key!r} is not a number"
                    )
                probabilities[str(key)] = float(value)

        if (
            self.min_confidence is not None
            and confidence is not None
            and confidence < self.min_confidence
        ):
            raise ModeRouterError(
                "low_confidence",
                f"confidence {confidence} < min_confidence {self.min_confidence}",
            )

        return RoutingDecision(mode=choice, confidence=confidence, probabilities=probabilities)
