"""CompositeEvaluator 单元测试（阶段 H2）。"""

from __future__ import annotations

from typing import Any

import pytest

from core.trace.trace_context import TraceContext
from libs.evaluator.base_evaluator import BaseEvaluator
from libs.evaluator.custom_evaluator import CustomEvaluator
from observability.evaluation.composite_evaluator import CompositeEvaluator
from observability.evaluation.ragas_evaluator import RagasEvaluator


class FakeEvaluator(BaseEvaluator):
    """可脚本化 fake：固定 metrics / 抛异常 / 记录调用参数。"""

    def __init__(self, name: str, metrics: dict[str, Any] | None = None, error: Exception | None = None):
        self._name = name
        self._metrics = metrics or {}
        self._error = error
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, query, retrieved_ids, golden_ids, *, top_k=None, trace=None, **kwargs):
        self.calls.append(
            {
                "query": query,
                "retrieved_ids": list(retrieved_ids),
                "golden_ids": list(golden_ids),
                "top_k": top_k,
                "kwargs": dict(kwargs),
            }
        )
        if self._error is not None:
            raise self._error
        return dict(self._metrics)


class TestConstruction:
    def test_empty_evaluators_value_error(self):
        with pytest.raises(ValueError):
            CompositeEvaluator([])

    def test_non_base_evaluator_type_error(self):
        with pytest.raises(TypeError):
            CompositeEvaluator([CustomEvaluator(), object()])  # type: ignore[list-item]

    def test_from_settings_combines_backends(self, monkeypatch, tmp_path):
        # 复用仓库默认 settings（evaluation.backends = [ragas, custom]）→ 工厂组合。
        from pathlib import Path

        from core.settings import load_settings

        repo_config = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
        monkeypatch.chdir(tmp_path)  # 数据目录落临时区
        settings = load_settings(str(repo_config))
        assert settings.evaluation.backends == ["ragas", "custom"]
        composite = CompositeEvaluator.from_settings(settings)
        names = [e.name for e in composite.evaluators]
        assert names == ["ragas", "custom"]
        assert isinstance(composite.evaluators[0], RagasEvaluator)
        assert isinstance(composite.evaluators[1], CustomEvaluator)


class TestEvaluation:
    def test_merges_metrics_from_multiple_evaluators(self):
        e1 = FakeEvaluator("custom", {"hit_rate": 1.0, "mrr": 0.5})
        e2 = FakeEvaluator(
            "ragas", {"faithfulness": 0.9, "answer_relevancy": 0.8}
        )
        composite = CompositeEvaluator([e1, e2])
        metrics = composite.evaluate("q", ["c1"], ["c1"], answer="a")
        assert metrics == {
            "hit_rate": 1.0,
            "mrr": 0.5,
            "faithfulness": 0.9,
            "answer_relevancy": 0.8,
        }

    def test_all_evaluators_receive_same_args_and_kwargs(self):
        e1 = FakeEvaluator("e1", {"a": 1.0})
        e2 = FakeEvaluator("e2", {"b": 2.0})
        composite = CompositeEvaluator([e1, e2])
        composite.evaluate("q", ["c1", "c2"], ["c1"], top_k=3, answer="ans")
        for e in (e1, e2):
            assert len(e.calls) == 1
            call = e.calls[0]
            assert call["query"] == "q"
            assert call["retrieved_ids"] == ["c1", "c2"]
            assert call["golden_ids"] == ["c1"]
            assert call["top_k"] == 3
            assert call["kwargs"] == {"answer": "ans"}

    def test_metric_key_collision_prefixed_symmetrically(self):
        e1 = FakeEvaluator("custom", {"score": 1.0, "unique_a": 1.0})
        e2 = FakeEvaluator("ragas", {"score": 0.5, "unique_b": 2.0})
        composite = CompositeEvaluator([e1, e2])
        metrics = composite.evaluate("q", ["c1"], ["c1"])
        assert metrics == {
            "custom.score": 1.0,
            "ragas.score": 0.5,
            "unique_a": 1.0,
            "unique_b": 2.0,
        }

    def test_single_evaluator_runs_without_pool(self):
        e = FakeEvaluator("solo", {"x": 1.0})
        composite = CompositeEvaluator([e])
        assert composite.evaluate("q", ["c1"], ["c1"]) == {"x": 1.0}

    def test_non_numeric_metrics_filtered(self):
        e = FakeEvaluator("e", {"ok": 1, "bad": object()})
        composite = CompositeEvaluator([e])
        assert composite.evaluate("q", ["c1"], ["c1"]) == {"ok": 1.0}

    def test_failing_evaluator_isolated(self):
        bad = FakeEvaluator("bad", error=RuntimeError("boom"))
        good = FakeEvaluator("good", {"hit_rate": 1.0})
        composite = CompositeEvaluator([bad, good])
        metrics = composite.evaluate("q", ["c1"], ["c1"])
        assert metrics == {"hit_rate": 1.0}
        assert composite.last_errors == {"bad": "boom"}

    def test_last_errors_reset_each_call(self):
        bad = FakeEvaluator("bad", error=RuntimeError("boom"))
        good = FakeEvaluator("good", {"x": 1.0})
        composite = CompositeEvaluator([bad, good])
        composite.evaluate("q", ["c1"], ["c1"])
        assert composite.last_errors
        composite.evaluate("q", ["c1"], ["c1"])
        assert composite.last_errors == {"bad": "boom"}  # 仍只含本次失败


class TestTrace:
    def test_records_composite_stage(self):
        e1 = FakeEvaluator("custom", {"hit_rate": 1.0})
        e2 = FakeEvaluator("ragas", {"faithfulness": 0.9})
        composite = CompositeEvaluator([e1, e2])
        trace = TraceContext()
        composite.evaluate("q", ["c1"], ["c1"], answer="a", trace=trace)
        stage = trace.get_stage("evaluation")
        assert stage is not None
        assert stage["method"] == "composite_evaluator"
        assert stage["details"]["evaluators"] == ["custom", "ragas"]
        assert stage["details"]["metrics_by_evaluator"]["custom"] == ["hit_rate"]
        assert stage["details"]["errors"] == {}

    def test_trace_forwarded_to_sub_evaluators(self):
        e = FakeEvaluator("e", {"x": 1.0})
        composite = CompositeEvaluator([e])
        trace = TraceContext()
        composite.evaluate("q", ["c1"], ["c1"], trace=trace)
        assert len(trace.stages) >= 1

    def test_no_trace_no_error(self):
        composite = CompositeEvaluator([FakeEvaluator("e", {"x": 1.0})])
        assert composite.evaluate("q", ["c1"], ["c1"]) == {"x": 1.0}


class TestContract:
    def test_is_base_evaluator(self):
        composite = CompositeEvaluator([FakeEvaluator("e", {})])
        assert isinstance(composite, BaseEvaluator)
        assert composite.name == "composite"

    def test_with_real_custom_evaluator(self):
        composite = CompositeEvaluator([CustomEvaluator()])
        metrics = composite.evaluate("q", ["c1", "c2"], ["c2"])
        assert metrics["hit_rate"] == 1.0
        assert metrics["mrr"] == 0.5
