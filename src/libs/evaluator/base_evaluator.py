"""Evaluator 抽象基类（阶段 B6）。

定义全链路共用的 ``BaseEvaluator`` 接口，屏蔽不同评估框架
（Ragas / DeepEval / 自定义指标）的差异。

设计要点：
- ``evaluate(query, retrieved_ids, golden_ids)`` 为统一入口，返回 ``dict[str, float]``。
- 预留 ``trace: TraceContext | None`` 参数（阶段 F 打点时使用，当前仅占位）。
- 不在此处做任何网络/IO 的"业务初始化"，仅定义契约。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

__all__ = ["BaseEvaluator"]


class BaseEvaluator(ABC):
    """Evaluator 抽象基类，所有评估框架实现需继承并实现 ``evaluate``。"""

    @property
    def name(self) -> str:
        """评估器名称，供报告区分来源。默认取类名，子类可覆盖。"""
        return self.__class__.__name__

    @abstractmethod
    def evaluate(
        self,
        query: str,
        retrieved_ids: Sequence[str],
        golden_ids: Sequence[str],
        *,
        top_k: int | None = None,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, float]:
        """计算单条 query 的检索质量指标。

        Args:
            query: 用户查询。
            retrieved_ids: 检索返回的候选 id 列表（按相关性降序）。
            golden_ids: 标准答案 id 列表。
            top_k: 可选的截断位置（计算 hit@k / mrr@k 时使用）。
            trace: 可选的 ``TraceContext``（当前占位）。
            **kwargs: 评估器特定的扩展参数。

        Returns:
            指标字典，如 ``{"hit_rate": 1.0, "mrr": 0.5}``。
        """
        raise NotImplementedError
