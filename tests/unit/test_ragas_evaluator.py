"""RagasEvaluator 单元测试（阶段 H1）。

全部离线：注入 fake ``metrics_fn`` / ``evaluate_fn`` / LLM judge，
无需安装 ragas、无需网络。
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from core.trace.trace_context import TraceContext
from libs.evaluator.evaluator_factory import EvaluatorFactory
from observability.evaluation.ragas_evaluator import RAGAS_INSTALL_HINT, RagasEvaluator


def _fake_metrics() -> list[str]:
    return ["m_faithfulness", "m_answer_relevancy", "m_context_precision"]


def _make_evaluator(evaluate_fn=None, **ctor_kwargs):
    kwargs: dict[str, Any] = {"metrics_fn": _fake_metrics}
    if evaluate_fn is not None:
        kwargs["evaluate_fn"] = evaluate_fn
    kwargs.update(ctor_kwargs)
    return RagasEvaluator(**kwargs)


def _ok_evaluate(**kwargs):
    return {
        "faithfulness": 0.9,
        "answer_relevancy": 0.8,
        "context_precision": 1.0,
    }


class TestHappyPath:
    def test_returns_ragas_metrics_as_floats(self):
        evaluator = _make_evaluator(_ok_evaluate)
        metrics = evaluator.evaluate(
            "如何配置 Azure？",
            ["chunk_1", "chunk_2"],
            ["chunk_1"],
            answer="配置方法是……",
        )
        assert metrics == {
            "faithfulness": 0.9,
            "answer_relevancy": 0.8,
            "context_precision": 1.0,
        }
        assert all(isinstance(v, float) for v in metrics.values())

    def test_dataset_passed_to_evaluate_fn(self):
        captured: dict[str, Any] = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return {}

        evaluator = _make_evaluator(capture)
        evaluator.evaluate(
            "q",
            ["chunk_1", "chunk_2"],
            ["chunk_2"],
            answer="a",
            contexts=["ctx1", "ctx2"],
        )
        assert captured["dataset"] == {
            "question": ["q"],
            "answer": ["a"],
            "contexts": [["ctx1", "ctx2"]],
            "ground_truth": [["chunk_2"]],
        }
        assert captured["metrics"] == _fake_metrics()

    def test_llm_and_embeddings_forwarded_when_provided(self):
        captured: dict[str, Any] = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return {}

        fake_llm = object()
        fake_emb = object()
        evaluator = _make_evaluator(capture, llm=fake_llm, embeddings=fake_emb)
        evaluator.evaluate("q", ["c1"], ["c1"], answer="a")
        assert captured["llm"] is fake_llm
        assert captured["embeddings"] is fake_emb

    def test_llm_absent_not_forwarded(self):
        captured: dict[str, Any] = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return {}

        evaluator = _make_evaluator(capture)
        evaluator.evaluate("q", ["c1"], ["c1"], answer="a")
        assert "llm" not in captured
        assert "embeddings" not in captured

    def test_contexts_default_fallback_to_retrieved_ids(self):
        captured: dict[str, Any] = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return {}

        evaluator = _make_evaluator(capture)
        evaluator.evaluate("q", ["c1", "c2"], ["c1"], answer="a")
        assert captured["dataset"]["contexts"] == [["c1", "c2"]]

    def test_top_k_truncates_contexts(self):
        captured: dict[str, Any] = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return {}

        evaluator = _make_evaluator(capture)
        evaluator.evaluate(
            "q", ["c1", "c2", "c3"], ["c1"], answer="a", top_k=2
        )
        assert captured["dataset"]["contexts"] == [["c1", "c2"]]

    def test_non_numeric_results_filtered(self):
        evaluator = _make_evaluator(
            lambda **kw: {"faithfulness": 0.5, "report": object(), "nan_like": "x"}
        )
        metrics = evaluator.evaluate("q", ["c1"], ["c1"], answer="a")
        assert metrics == {"faithfulness": 0.5}

    def test_int_results_coerced_to_float(self):
        evaluator = _make_evaluator(lambda **kw: {"faithfulness": 1})
        metrics = evaluator.evaluate("q", ["c1"], ["c1"], answer="a")
        assert metrics == {"faithfulness": 1.0}
        assert isinstance(metrics["faithfulness"], float)


class TestFailFast:
    def test_empty_query_type_error(self):
        evaluator = _make_evaluator(_ok_evaluate)
        with pytest.raises(TypeError):
            evaluator.evaluate("  ", ["c1"], ["c1"], answer="a")

    def test_non_str_query_type_error(self):
        evaluator = _make_evaluator(_ok_evaluate)
        with pytest.raises(TypeError):
            evaluator.evaluate(None, ["c1"], ["c1"], answer="a")  # type: ignore[arg-type]

    def test_non_list_retrieved_ids_type_error(self):
        evaluator = _make_evaluator(_ok_evaluate)
        with pytest.raises(TypeError):
            evaluator.evaluate("q", "c1", ["c1"], answer="a")  # type: ignore[arg-type]

    @pytest.mark.parametrize("answer", [None, "", "   "])
    def test_missing_answer_value_error(self, answer):
        evaluator = _make_evaluator(_ok_evaluate)
        with pytest.raises(ValueError, match="answer"):
            evaluator.evaluate("q", ["c1"], ["c1"], answer=answer)

    @pytest.mark.parametrize("contexts", ["ctx", ["ok", 1], [None]])
    def test_invalid_contexts_type_error(self, contexts):
        evaluator = _make_evaluator(_ok_evaluate)
        with pytest.raises(TypeError):
            evaluator.evaluate("q", ["c1"], ["c1"], answer="a", contexts=contexts)


class TestGracefulDegradation:
    def test_ragas_missing_raises_import_error_with_hint(self, monkeypatch):
        # 让 `from ragas import ...` 抛 ImportError（ragas 未安装场景）。
        monkeypatch.setitem(sys.modules, "ragas", None)
        monkeypatch.setitem(sys.modules, "ragas.metrics", None)
        evaluator = RagasEvaluator()  # 构造不触发导入。
        with pytest.raises(ImportError, match=RAGAS_INSTALL_HINT):
            evaluator.evaluate("q", ["c1"], ["c1"], answer="a")

    def test_importable_without_ragas_installed(self):
        # 模块导入与实例构造均不要求 ragas 已安装。
        evaluator = RagasEvaluator()
        assert evaluator.name == "ragas"


class TestTrace:
    def test_records_evaluation_stage(self):
        evaluator = _make_evaluator(_ok_evaluate)
        trace = TraceContext()
        evaluator.evaluate("q", ["c1"], ["c1"], answer="a", trace=trace)
        stage = trace.get_stage("evaluation")
        assert stage is not None
        assert stage["method"] == "ragas_evaluator"
        assert "faithfulness" in stage["details"]["metrics"]
        assert stage["details"]["has_llm"] is False

    def test_no_trace_no_error(self):
        evaluator = _make_evaluator(_ok_evaluate)
        metrics = evaluator.evaluate("q", ["c1"], ["c1"], answer="a", trace=None)
        assert "faithfulness" in metrics


class TestFactoryIntegration:
    def test_registered_as_ragas_backend(self):
        assert EvaluatorFactory.is_registered("ragas")

    def test_create_returns_ragas_evaluator(self):
        evaluator = EvaluatorFactory.create("ragas")
        assert isinstance(evaluator, RagasEvaluator)
        assert evaluator.name == "ragas"

    def test_create_forwards_kwargs(self):
        evaluator = EvaluatorFactory.create(
            "ragas", metrics_fn=_fake_metrics, evaluate_fn=_ok_evaluate
        )
        metrics = evaluator.evaluate("q", ["c1"], ["c1"], answer="a")
        assert metrics["answer_relevancy"] == 0.8

    def test_available_backends_contains_ragas(self):
        assert "ragas" in EvaluatorFactory.available_backends()
