"""AgenticTrigger：第三路 agentic 检索触发策略（S2.2）。

独立于 HybridSearch 主链路（S2.3 才在 HybridSearch 中注入第三路），
仅回答一个问题：**本次查询是否自动触发 agentic 检索**。

三种策略（``agentic_search.trigger``）：

- ``tool_only``（默认，安全值）：永不自动触发，只由 MCP 工具显式调用。
- ``fallback``：主路（dense+sparse 融合）信号不足时触发——融合后 top
  分数低于 ``trigger_score_threshold``，或结果数低于
  ``trigger_min_results``。
- ``always``：每次查询都触发（**仅用于实验/评估**，生产禁用）。

.. warning:: 阈值校准纪律（对齐 DEV_SPEC 9.5 / SIRCHMUNK_INTEGRATION_SPEC S2.2）

    cross-encoder 分数是**未校准 logits**，不是概率。默认阈值
    ``0.5`` 仅是占位值，**未经过任何校准**。阈值必须在**独立校准集**
    上标定；禁止用评估 golden set 调参（否则评估指标失真）。

.. note:: 触发时机成本

    若用 **rerank 后**分数触发：fallback 判定发生在融合之后、rerank
    之后，而一旦触发第三路又需要把 agentic 结果与主路结果**二次融合
    + 重排**，rerank 计算成本翻倍。若追求更低成本可改用 rerank 前
    信号（如 RRF 融合名次），但该信号更粗糙——这是精度/成本的权衡，
    由 S2.3 注入方决定取哪个信号喂给 :meth:`AgenticTrigger.should_trigger`。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "TRIGGER_TOOL_ONLY",
    "TRIGGER_FALLBACK",
    "TRIGGER_ALWAYS",
    "VALID_TRIGGERS",
    "DEFAULT_TRIGGER_SCORE_THRESHOLD",
    "DEFAULT_TRIGGER_MIN_RESULTS",
    "AgenticTriggerError",
    "AgenticTrigger",
    "TriggerDecision",
]

# 策略常量（与 settings.AgenticSearchConfig.trigger / settings.yaml 对齐）。
TRIGGER_TOOL_ONLY = "tool_only"
TRIGGER_FALLBACK = "fallback"
TRIGGER_ALWAYS = "always"
VALID_TRIGGERS = frozenset({TRIGGER_TOOL_ONLY, TRIGGER_FALLBACK, TRIGGER_ALWAYS})

# 默认阈值：**未校准占位值**。cross-encoder 分数是未校准 logits，
# 必须在独立校准集上标定后才能用于生产（禁止用评估 golden set 调参）。
DEFAULT_TRIGGER_SCORE_THRESHOLD = 0.5
DEFAULT_TRIGGER_MIN_RESULTS = 1


class AgenticTriggerError(ValueError):
    """触发策略非法（策略名不在 ``VALID_TRIGGERS`` 内等）。"""


@dataclass(frozen=True)
class TriggerDecision:
    """触发判定结果（含原因，便于 trace 打点与测试断言）。"""

    triggered: bool
    reason: str


class AgenticTrigger:
    """第三路 agentic 检索的触发策略（纯函数式判定，无 IO，离线可测）。

    Args:
        strategy: ``tool_only`` / ``fallback`` / ``always``。
        score_threshold: fallback 策略的 top 分数下限（**未校准占位值**，
            见模块 docstring 的校准纪律说明）。
        min_results: fallback 策略的结果数下限（低于则视为信号不足）。

    Raises:
        AgenticTriggerError: 策略名非法或参数类型/范围非法（fail-fast）。
    """

    def __init__(
        self,
        strategy: str = TRIGGER_TOOL_ONLY,
        score_threshold: float = DEFAULT_TRIGGER_SCORE_THRESHOLD,
        min_results: int = DEFAULT_TRIGGER_MIN_RESULTS,
    ) -> None:
        if strategy not in VALID_TRIGGERS:
            raise AgenticTriggerError(
                f"agentic trigger strategy must be one of {sorted(VALID_TRIGGERS)}, "
                f"got {strategy!r}"
            )
        if isinstance(score_threshold, bool) or not isinstance(score_threshold, (int, float)):
            raise AgenticTriggerError(
                f"agentic trigger score_threshold must be a number, got {score_threshold!r}"
            )
        if not 0.0 <= float(score_threshold) <= 1.0:
            raise AgenticTriggerError(
                f"agentic trigger score_threshold must be in [0, 1], got {score_threshold!r}"
            )
        if isinstance(min_results, bool) or not isinstance(min_results, int) or min_results < 0:
            raise AgenticTriggerError(
                f"agentic trigger min_results must be a non-negative integer, got {min_results!r}"
            )
        self._strategy = strategy
        self._score_threshold = float(score_threshold)
        self._min_results = min_results

    @property
    def strategy(self) -> str:
        """当前策略名。"""
        return self._strategy

    @property
    def score_threshold(self) -> float:
        """fallback 策略的 top 分数下限（未校准占位值）。"""
        return self._score_threshold

    @property
    def min_results(self) -> int:
        """fallback 策略的结果数下限。"""
        return self._min_results

    def should_trigger(self, top_score: float | None, result_count: int) -> TriggerDecision:
        """判定是否触发第三路 agentic 检索。

        Args:
            top_score: 主路（融合/rerank 后）top1 分数；无结果时传 ``None``。
            result_count: 主路结果数。

        Returns:
            :class:`TriggerDecision`：``triggered`` 与可读 ``reason``。
        """
        if self._strategy == TRIGGER_TOOL_ONLY:
            return TriggerDecision(False, "strategy=tool_only（仅由 MCP 工具显式调用）")
        if self._strategy == TRIGGER_ALWAYS:
            return TriggerDecision(True, "strategy=always（实验/评估用）")
        # fallback：任一"信号不足"即触发。
        if result_count < self._min_results:
            return TriggerDecision(
                True, f"fallback：结果数 {result_count} < min_results {self._min_results}"
            )
        if top_score is None:
            return TriggerDecision(True, "fallback：无结果（top_score=None），信号不足")
        if top_score < self._score_threshold:
            return TriggerDecision(
                True,
                f"fallback：top_score {top_score:.4f} < threshold {self._score_threshold:.4f}",
            )
        return TriggerDecision(
            False,
            f"fallback：信号充足（top_score={top_score:.4f}, result_count={result_count}）",
        )
