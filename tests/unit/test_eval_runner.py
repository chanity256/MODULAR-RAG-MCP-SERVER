"""EvalRunner 单元测试（阶段 H3）。fake HybridSearch + 真 CustomEvaluator，离线。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.query_engine.hybrid_search import HybridSearchError
from core.types import RetrievalResult
from libs.evaluator.custom_evaluator import CustomEvaluator
from observability.evaluation.eval_runner import (
    EvalRunner,
    GoldenTestCase,
    load_golden_test_set,
)


class FakeHybridSearch:
    """按 query 脚本化返回结果的 fake 检索栈。"""

    def __init__(self, responses: dict[str, list[str]], error_queries: set[str] | None = None):
        self.responses = responses
        self.error_queries = error_queries or set()
        self.calls: list[dict[str, Any]] = []

    def search(self, query, top_k=None, filters=None, trace=None):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        if query in self.error_queries:
            raise HybridSearchError("both routes failed")
        ids = self.responses.get(query, [])
        return [
            RetrievalResult(chunk_id=cid, score=1.0 / (i + 1), text=f"text {cid}", metadata={})
            for i, cid in enumerate(ids)
        ]


def _write_test_set(tmp_path, cases) -> str:
    path = tmp_path / "golden.json"
    path.write_text(json.dumps({"test_cases": cases}), encoding="utf-8")
    return str(path)


class TestLoadGoldenTestSet:
    def test_parses_spec_format(self, tmp_path):
        path = _write_test_set(
            tmp_path,
            [
                {
                    "query": "如何配置 Azure OpenAI？",
                    "expected_chunk_ids": ["c1", "c2"],
                    "expected_sources": ["config_guide.pdf"],
                }
            ],
        )
        cases = load_golden_test_set(path)
        assert cases == [
            GoldenTestCase("如何配置 Azure OpenAI？", ["c1", "c2"], ["config_guide.pdf"])
        ]

    def test_expected_sources_optional(self, tmp_path):
        path = _write_test_set(tmp_path, [{"query": "q", "expected_chunk_ids": ["c1"]}])
        assert load_golden_test_set(path)[0].expected_sources == []

    @pytest.mark.parametrize(
        "payload",
        [
            "{}",
            '{"test_cases": []}',
            '{"test_cases": [{"expected_chunk_ids": ["c"]}], "x": 1}',
            '{"test_cases": [{"query": "q"}]}',
            '{"test_cases": [{"query": "q", "expected_chunk_ids": "c1"}]}',
            '{"test_cases": [{"query": "q", "expected_chunk_ids": ["c"], "expected_sources": "f.pdf"}]}',
        ],
    )
    def test_invalid_payloads_value_error(self, tmp_path, payload):
        path = tmp_path / "golden.json"
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(ValueError):
            load_golden_test_set(path)


class TestEvalRunner:
    def _runner(self, fake_search):
        return EvalRunner(settings=object(), hybrid_search=fake_search, evaluator=CustomEvaluator())

    def test_aggregates_mean_metrics(self, tmp_path):
        path = _write_test_set(
            tmp_path,
            [
                {"query": "q1", "expected_chunk_ids": ["a"]},
                {"query": "q2", "expected_chunk_ids": ["z"]},
            ],
        )
        search = FakeHybridSearch({"q1": ["a", "b"], "q2": ["x", "y"]})
        report = self._runner(search).run(path)
        assert report.total == 2
        assert report.evaluated == 2
        assert report.failed == 0
        # q1: hit_rate=1, mrr=1；q2: hit_rate=0, mrr=0 → 均值 0.5
        assert report.metrics["hit_rate"] == 0.5
        assert report.metrics["mrr"] == 0.5
        assert report.metrics["hit_at_k"] == 0.5

    def test_per_query_details(self, tmp_path):
        path = _write_test_set(tmp_path, [{"query": "q1", "expected_chunk_ids": ["b"]}])
        search = FakeHybridSearch({"q1": ["a", "b"]})
        report = self._runner(search).run(path)
        detail = report.per_query[0]
        assert detail.query == "q1"
        assert detail.retrieved_ids == ["a", "b"]
        assert detail.expected_chunk_ids == ["b"]
        assert detail.metrics["mrr"] == 0.5
        assert detail.error is None

    def test_report_to_dict_json_serializable(self, tmp_path):
        path = _write_test_set(tmp_path, [{"query": "q1", "expected_chunk_ids": ["a"]}])
        report = self._runner(FakeHybridSearch({"q1": ["a"]})).run(path)
        data = report.to_dict()
        assert data["total"] == 1
        assert json.dumps(data)  # JSON 可序列化
        assert data["per_query"][0]["retrieved_ids"] == ["a"]

    def test_single_query_failure_isolated(self, tmp_path):
        path = _write_test_set(
            tmp_path,
            [
                {"query": "bad", "expected_chunk_ids": ["a"]},
                {"query": "good", "expected_chunk_ids": ["a"]},
            ],
        )
        search = FakeHybridSearch({"good": ["a"]}, error_queries={"bad"})
        report = self._runner(search).run(path)
        assert report.total == 2
        assert report.evaluated == 1
        assert report.failed == 1
        assert "both routes failed" in report.per_query[0].error
        assert report.metrics["hit_rate"] == 1.0  # 仅按成功 case 聚合

    def test_top_k_and_filters_forwarded_to_search(self, tmp_path):
        path = _write_test_set(tmp_path, [{"query": "q1", "expected_chunk_ids": ["a"]}])
        search = FakeHybridSearch({"q1": ["a"]})
        self._runner(search).run(path, top_k=3, filters={"collection": "docs"})
        assert search.calls[0]["top_k"] == 3
        assert search.calls[0]["filters"] == {"collection": "docs"}

    def test_default_evaluator_is_custom(self):
        # evaluator 未注入时缺省 CustomEvaluator（无需 ragas/网络）。
        runner = EvalRunner(settings=object(), hybrid_search=FakeHybridSearch({}))
        assert isinstance(runner.evaluator, CustomEvaluator)
