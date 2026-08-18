"""RagasEvaluator（阶段 H1）。

封装 Ragas 评估框架，实现 ``BaseEvaluator`` 接口，提供
RAG 生成质量指标：
- ``faithfulness``：答案对上下文的忠实度。
- ``answer_relevancy``：答案与问题的相关性。
- ``context_precision``：上下文排序质量。

设计要点：
- **优雅降级**：Ragas 未安装时不阻断导入，仅在真正执行评估时
  抛出带安装提示的 ``ImportError``。
- **可注入**：``metrics_fn`` / ``evaluate_fn`` / LLM judge 均可注入，
  单元测试无需安装 ragas、无需真实网络。
- Ragas 指标是生成质量指标，需要 ``answer``（生成答案）与
  ``contexts``（检索上下文），经 ``evaluate()`` 的 kwargs 传入；
  ``contexts`` 缺省回退为 ``retrieved_ids``（降级语义）。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from core.trace.trace_context import TraceContext
from libs.evaluator.base_evaluator import BaseEvaluator

__all__ = ["RagasEvaluator", "RAGAS_INSTALL_HINT"]

RAGAS_INSTALL_HINT = "pip install ragas"

#: 默认启用的 Ragas 指标名。
DEFAULT_METRICS = ("faithfulness", "answer_relevancy", "context_precision")


def _default_metrics_fn() -> list[Any]:
    """延迟导入 ragas.metrics，返回默认指标列表。"""
    try:
        from ragas.metrics import answer_relevancy, context_precision, faithfulness
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise ImportError(
            f"ragas is not installed: {exc}. install it with `{RAGAS_INSTALL_HINT}` "
            "to use the ragas evaluator backend."
        ) from exc
    return [faithfulness, answer_relevancy, context_precision]


def _default_evaluate_fn(**kwargs: Any) -> dict[str, Any]:
    """延迟导入 ragas.evaluate 并转发调用。"""
    try:
        from ragas import evaluate
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise ImportError(
            f"ragas is not installed: {exc}. install it with `{RAGAS_INSTALL_HINT}` "
            "to use the ragas evaluator backend."
        ) from exc
    return evaluate(**kwargs)


class RagasEvaluator(BaseEvaluator):
    """Ragas 框架适配器：faithfulness / answer_relevancy / context_precision。"""

    def __init__(
        self,
        *,
        llm: Any | None = None,
        embeddings: Any | None = None,
        metrics_fn: Callable[[], list[Any]] | None = None,
        evaluate_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        """
        Args:
            llm: Ragas 评估用的 LLM judge（可注入 fake/真实 langchain wrapper）。
            embeddings: 可选 embeddings（answer_relevancy 部分版本需要）。
            metrics_fn: 返回指标列表的可调用（缺省延迟导入 ragas.metrics）。
            evaluate_fn: 执行评估的可调用（缺省延迟导入 ragas.evaluate）。
        """
        self._llm = llm
        self._embeddings = embeddings
        self._metrics_fn = metrics_fn or _default_metrics_fn
        self._evaluate_fn = evaluate_fn or _default_evaluate_fn

    @property
    def name(self) -> str:
        return "ragas"

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
        """计算单条 query 的 Ragas 生成质量指标。

        Args:
            query: 用户查询。
            retrieved_ids: 检索返回的候选 id 列表。
            golden_ids: 标准答案 id 列表（映射为 ground_truth）。
            top_k: 可选截断（作用于 contexts）。
            answer: 生成答案（Ragas 指标必需），经 kwargs 传入。
            contexts: 检索上下文文本列表，缺省回退为 retrieved_ids。
            trace: 可选 TraceContext，打点 ``evaluation`` 阶段。

        Returns:
            形如 ``{"faithfulness": 0.9, "answer_relevancy": 0.8,
            "context_precision": 1.0}`` 的指标字典。

        Raises:
            TypeError/ValueError: 参数非法。
            ImportError: ragas 未安装。
        """
        if not isinstance(query, str) or not query.strip():
            raise TypeError("query must be a non-empty string")
        if not isinstance(retrieved_ids, (list, tuple)):
            raise TypeError("retrieved_ids must be a list of ids")

        answer = kwargs.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(
                "ragas metrics require a non-empty 'answer' kwarg "
                "(the generated answer being evaluated)"
            )

        contexts = kwargs.get("contexts")
        if contexts is None:
            contexts = list(retrieved_ids)  # 降级：以 id 充当上下文。
        if not isinstance(contexts, (list, tuple)) or not all(isinstance(c, str) for c in contexts):
            raise TypeError("contexts must be a list of strings")

        considered_contexts = list(contexts[:top_k] if top_k is not None else contexts)

        dataset = {
            "question": [query],
            "answer": [answer],
            "contexts": [considered_contexts],
            "ground_truth": [list(golden_ids)],
        }

        eval_kwargs: dict[str, Any] = {"dataset": dataset, "metrics": self._metrics_fn()}
        if self._llm is not None:
            eval_kwargs["llm"] = self._llm
        if self._embeddings is not None:
            eval_kwargs["embeddings"] = self._embeddings

        raw = self._evaluate_fn(**eval_kwargs) or {}

        metrics: dict[str, float] = {}
        for key, value in dict(raw).items():
            try:
                metrics[str(key)] = float(value)
            except (TypeError, ValueError):
                continue  # 非数值指标（如报告对象）跳过，保持 float 契约。

        if trace is not None:
            trace.record_stage(
                "evaluation",
                method="ragas_evaluator",
                details={
                    "metrics": sorted(metrics),
                    "contexts": len(considered_contexts),
                    "has_llm": self._llm is not None,
                },
            )
        return metrics
