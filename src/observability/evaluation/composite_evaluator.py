"""CompositeEvaluator（阶段 H2）。

组合多个 ``BaseEvaluator`` 并行执行并汇总结果：
- 构造期传入 evaluator 列表，或经 ``from_settings`` 按
  ``settings.evaluation.backends`` 由 ``EvaluatorFactory`` 批量创建。
- ``evaluate()`` 用线程池并行调用各 evaluator，指标合并为一个 dict。
- 指标名冲突时以 ``{evaluator_name}.{metric}`` 前缀消歧（确定性）。
- 单个 evaluator 异常隔离（降级），不阻断其余 evaluator，
  异常记录在 ``last_errors``（每次调用重置）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from libs.evaluator.base_evaluator import BaseEvaluator
from libs.evaluator.evaluator_factory import EvaluatorFactory

__all__ = ["CompositeEvaluator"]

#: 并行执行线程数上限。
_MAX_WORKERS = 4


class CompositeEvaluator(BaseEvaluator):
    """组合评估器：多后端并行执行，metrics 合并汇总。"""

    def __init__(self, evaluators: Sequence[BaseEvaluator]) -> None:
        evaluators = list(evaluators)
        if not evaluators:
            raise ValueError("CompositeEvaluator requires at least one evaluator")
        if not all(isinstance(e, BaseEvaluator) for e in evaluators):
            raise TypeError("all evaluators must be BaseEvaluator instances")
        self.evaluators = evaluators
        #: 最近一次 evaluate 中各 evaluator 的异常（name -> error msg），逐次重置。
        self.last_errors: dict[str, str] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> "CompositeEvaluator":
        """按 ``settings.evaluation.backends`` 工厂批量创建并组合。"""
        return cls(EvaluatorFactory.create_from_settings(settings))

    @property
    def name(self) -> str:
        return "composite"

    def evaluate(
        self,
        query: str,
        retrieved_ids: Sequence[str],
        golden_ids: Sequence[str],
        *,
        top_k: int | None = None,
        trace: TraceContext | None = None,
        **kwargs: Any,
    ) -> dict[str, float]:
        self.last_errors = {}
        per_evaluator: dict[str, list[str]] = {}
        all_metrics: list[tuple[str, dict[str, Any]]] = []

        def run_one(evaluator: BaseEvaluator) -> tuple[str, dict[str, float] | None, str | None]:
            try:
                metrics = evaluator.evaluate(
                    query,
                    retrieved_ids,
                    golden_ids,
                    top_k=top_k,
                    trace=trace,
                    **kwargs,
                )
                return evaluator.name, dict(metrics), None
            except Exception as exc:  # 单 evaluator 降级不阻断。
                return evaluator.name, None, str(exc)

        if len(self.evaluators) == 1:
            results = [run_one(self.evaluators[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(self.evaluators))) as pool:
                results = list(pool.map(run_one, self.evaluators))

        for name, metrics, error in results:
            if error is not None:
                self.last_errors[name] = error
                per_evaluator[name] = []
                continue
            assert metrics is not None
            per_evaluator[name] = sorted(metrics)
            all_metrics.append((name, metrics))

        if trace is not None:
            trace.record_stage(
                "evaluation",
                method="composite_evaluator",
                details={
                    "evaluators": [e.name for e in self.evaluators],
                    "metrics_by_evaluator": per_evaluator,
                    "errors": dict(self.last_errors),
                },
            )

        # 两遍合并：跨 evaluator 重复出现的指标键统一加 "{name}." 前缀（对称消歧）。
        key_owners: dict[str, list[str]] = {}
        for name, keys in per_evaluator.items():
            for key in keys:
                key_owners.setdefault(key, []).append(name)

        merged: dict[str, float] = {}
        for name, metrics in all_metrics:
            for key, value in metrics.items():
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                if len(key_owners.get(key, [])) > 1:
                    merged[f"{name}.{key}"] = value
                else:
                    merged[key] = value
        return merged
