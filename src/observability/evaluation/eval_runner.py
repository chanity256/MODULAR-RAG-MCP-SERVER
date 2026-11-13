"""EvalRunner（阶段 H3）。

读取 golden test set（JSON），逐条驱动 ``HybridSearch`` 检索，
用 ``BaseEvaluator``（默认 ``CustomEvaluator``）计算指标，聚合为
``EvalReport``。

golden test set 格式（spec H3）::

    {
      "test_cases": [
        {
          "query": "如何配置 Azure OpenAI？",
          "expected_chunk_ids": ["chunk_abc_001", "chunk_abc_002"],
          "expected_sources": ["config_guide.pdf"]
        }
      ]
    }

设计要点：
- ``hybrid_search`` / ``evaluator`` 可注入（离线单测 fake 检索栈）；
  缺省按 ``Settings`` 创建 ``HybridSearch`` 与 ``CompositeEvaluator``。
- 单条 query 检索失败（如 ``HybridSearchError``）记录 error 后继续，
  不中断整批评估。
- 报告聚合：逐指标取均值（hit_rate / mrr / hit_at_k 等数值指标），
  附 per-query 明细（retrieved_ids / golden_ids / metrics / error）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from core.query_engine.hybrid_search import HybridSearch, HybridSearchError
from core.settings import Settings
from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext
from libs.evaluator.base_evaluator import BaseEvaluator
from libs.evaluator.custom_evaluator import CustomEvaluator
from observability.evaluation.composite_evaluator import CompositeEvaluator
from observability.logger import get_logger

__all__ = ["EvalRunner", "EvalReport", "QueryEvalResult", "GoldenTestCase"]

logger = get_logger(__name__)


@dataclass
class GoldenTestCase:
    """单条黄金测试用例。"""

    query: str
    expected_chunk_ids: list[str]
    expected_sources: list[str] = field(default_factory=list)


@dataclass
class QueryEvalResult:
    """单条 query 的评估明细。"""

    query: str
    retrieved_ids: list[str]
    expected_chunk_ids: list[str]
    metrics: dict[str, float]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "retrieved_ids": list(self.retrieved_ids),
            "expected_chunk_ids": list(self.expected_chunk_ids),
            "metrics": dict(self.metrics),
            "error": self.error,
        }


@dataclass
class EvalReport:
    """整批评估报告：聚合指标 + 逐 query 明细。"""

    total: int
    evaluated: int
    failed: int
    metrics: dict[str, float]
    per_query: list[QueryEvalResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "evaluated": self.evaluated,
            "failed": self.failed,
            "metrics": dict(self.metrics),
            "per_query": [q.to_dict() for q in self.per_query],
        }


def load_golden_test_set(path: str | Path) -> list[GoldenTestCase]:
    """读取并校验 golden test set JSON。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = data.get("test_cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(
            f"golden test set {path!r} must contain a non-empty 'test_cases' list"
        )
    parsed: list[GoldenTestCase] = []
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"test_cases[{i}] must be an object, got {type(case).__name__}")
        query = case.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"test_cases[{i}].query must be a non-empty string")
        expected = case.get("expected_chunk_ids")
        if not isinstance(expected, list) or not all(isinstance(c, str) for c in expected):
            raise ValueError(
                f"test_cases[{i}].expected_chunk_ids must be a list of strings"
            )
        sources = case.get("expected_sources", [])
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            raise ValueError(f"test_cases[{i}].expected_sources must be a list of strings")
        parsed.append(GoldenTestCase(query=query, expected_chunk_ids=expected, expected_sources=sources))
    return parsed


class EvalRunner:
    """评估执行器：golden set → HybridSearch → evaluator → EvalReport。"""

    def __init__(
        self,
        settings: Settings,
        hybrid_search: HybridSearch | None = None,
        evaluator: BaseEvaluator | None = None,
    ) -> None:
        # 注入判定必须 is not None（对齐 C14 纪律）。
        self._settings = settings
        self._hybrid_search = hybrid_search if hybrid_search is not None else HybridSearch(settings)
        self._evaluator = evaluator if evaluator is not None else CustomEvaluator()

    @property
    def hybrid_search(self) -> HybridSearch:
        return self._hybrid_search

    @property
    def evaluator(self) -> BaseEvaluator:
        return self._evaluator

    def run(
        self,
        test_set_path: str | Path,
        *,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> EvalReport:
        """运行整批评估并返回报告。

        单条 query 检索/评估异常记录 error 后继续，不中断批次。
        """
        cases = load_golden_test_set(test_set_path)
        per_query: list[QueryEvalResult] = []
        metric_sums: dict[str, float] = {}
        evaluated = 0

        for case in cases:
            trace = TraceContext(trace_type=TRACE_TYPE_QUERY)
            try:
                results = self._hybrid_search.search(
                    case.query, top_k=top_k, filters=filters, trace=trace
                )
                retrieved_ids = [r.chunk_id for r in results]
                metrics = self._evaluator.evaluate(
                    case.query, retrieved_ids, case.expected_chunk_ids, top_k=top_k
                )
                metrics = {k: float(v) for k, v in metrics.items()}
                per_query.append(
                    QueryEvalResult(
                        query=case.query,
                        retrieved_ids=retrieved_ids,
                        expected_chunk_ids=case.expected_chunk_ids,
                        metrics=metrics,
                    )
                )
                evaluated += 1
                for key, value in metrics.items():
                    metric_sums[key] = metric_sums.get(key, 0.0) + value
            except Exception as exc:  # 单条失败隔离。
                logger.warning("eval case failed: %s: %s", case.query, exc)
                per_query.append(
                    QueryEvalResult(
                        query=case.query,
                        retrieved_ids=[],
                        expected_chunk_ids=case.expected_chunk_ids,
                        metrics={},
                        error=str(exc),
                    )
                )

        aggregate = (
            {key: value / evaluated for key, value in metric_sums.items()}
            if evaluated
            else {}
        )
        return EvalReport(
            total=len(cases),
            evaluated=evaluated,
            failed=len(cases) - evaluated,
            metrics=aggregate,
            per_query=per_query,
        )


def build_default_evaluator(settings: Settings) -> BaseEvaluator:
    """按 ``evaluation.backends`` 组合评估器（供 scripts/evaluate.py 使用）。"""
    return CompositeEvaluator.from_settings(settings)
