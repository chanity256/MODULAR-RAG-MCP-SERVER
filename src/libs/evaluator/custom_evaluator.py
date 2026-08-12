"""自定义轻量评估器（阶段 B6）。

实现 ``BaseEvaluator`` 接口，提供无需 LLM 的工程指标：
- ``hit_rate``：golden 是否出现在 retrieved 的 Top-K 中（命中为 1.0，否则 0.0）。
- ``mrr``（Mean Reciprocal Rank）：第一个命中的 golden 在 retrieved 中的倒数排名。
- ``hit_at_k``：Top-K 内命中的 golden 数量占比。

所有指标对同一输入确定性可复现。
"""

from __future__ import annotations

from typing import Any, Sequence

from libs.evaluator.base_evaluator import BaseEvaluator

__all__ = ["CustomEvaluator"]


class CustomEvaluator(BaseEvaluator):
    """自定义指标评估器：hit_rate / mrr / hit_at_k。"""

    @property
    def name(self) -> str:
        return "custom"

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
        retrieved = list(retrieved_ids)
        golden_set = set(golden_ids)
        if not golden_set:
            # 无标准答案时，指标定义为 0.0，避免除零。
            return {"hit_rate": 0.0, "mrr": 0.0, "hit_at_k": 0.0}

        # 可选截断。
        considered = retrieved[:top_k] if top_k is not None else retrieved

        # hit_rate：Top-K 内是否命中任意 golden。
        hit_rate = 1.0 if any(rid in golden_set for rid in considered) else 0.0

        # mrr：第一个命中 golden 的倒数排名（基于全量 retrieved，按检索惯例）。
        mrr = 0.0
        for rank, rid in enumerate(retrieved, start=1):
            if rid in golden_set:
                mrr = 1.0 / rank
                break

        # hit_at_k：Top-K 内命中的 golden 数量 / golden 总数。
        hits_in_k = sum(1 for rid in considered if rid in golden_set)
        hit_at_k = hits_in_k / len(golden_set)

        return {"hit_rate": hit_rate, "mrr": mrr, "hit_at_k": hit_at_k}
