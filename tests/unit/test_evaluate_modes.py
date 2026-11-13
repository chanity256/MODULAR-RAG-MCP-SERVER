"""scripts/evaluate.py ``--retrieval-mode`` 评估 A/B 单元测试（S3.1）。

离线手法：经 importlib 按路径加载入口模块；``run_mode_eval`` 注入
fake 后端 / fake 检索栈（零网络零子进程）；source 级 hit/MRR 用
手工构造期望值断言。hybrid 模式零回归由既有
``tests/unit/test_evaluate_entry.py`` 保证（不改一行）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_entry() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "evaluate_entry", _REPO_ROOT / "scripts" / "evaluate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluate_entry"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def entry() -> ModuleType:
    return _load_entry()


@pytest.fixture()
def fake_settings():
    """真实 Settings 副本，agentic 后端切到确定性 fake。"""
    from core.settings import load_settings

    settings = load_settings(str(_REPO_ROOT / "config" / "settings.yaml"))
    settings.agentic_search.backend = "fake"
    settings.agentic_search.enabled = False  # run_mode_eval 内部显式启用
    return settings


def _write_test_set(tmp_path: Path, cases: list[dict[str, Any]]) -> Path:
    path = tmp_path / "golden.json"
    path.write_text(json.dumps({"test_cases": cases}), encoding="utf-8")
    return path


def _result(chunk_id: str, score: float, source: str):
    from core.types import RetrievalResult

    return RetrievalResult(
        chunk_id=chunk_id, score=score, text=f"text of {chunk_id}", metadata={"source_path": source}
    )


# ---------------------------------------------------------------------------
# source 级指标：手算期望值断言
# ---------------------------------------------------------------------------


class TestSourceMetrics:
    def test_hit_first_rank_mrr_1(self, entry):
        # 检索来源 [a/b.pdf, c/d.md, a/b.pdf]，期望 [b.pdf]：
        # 去重后首位即命中 → hit=1.0，mrr=1/1=1.0。
        metrics = entry.compute_source_metrics(
            ["a/b.pdf", "c/d.md", "a/b.pdf"], ["b.pdf"]
        )
        assert metrics["source_hit_rate"] == 1.0
        assert metrics["source_mrr"] == 1.0

    def test_hit_second_rank_mrr_half(self, entry):
        # 检索来源 [x/y.md, a/b.pdf]，期望 [b.pdf, z.pdf]：
        # 命中 rank=2 → hit=1.0，mrr=0.5。
        metrics = entry.compute_source_metrics(["x/y.md", "a/b.pdf"], ["b.pdf", "z.pdf"])
        assert metrics["source_hit_rate"] == 1.0
        assert metrics["source_mrr"] == 0.5

    def test_miss(self, entry):
        metrics = entry.compute_source_metrics(["n/other.md"], ["b.pdf"])
        assert metrics["source_hit_rate"] == 0.0
        assert metrics["source_mrr"] == 0.0

    def test_basename_case_insensitive_normalization(self, entry):
        # 绝对路径 vs 裸文件名、大小写差异 → 归一化后命中。
        metrics = entry.compute_source_metrics(
            ["/data/docs/Chinese_Technical_DOC.PDF"], ["chinese_technical_doc.pdf"]
        )
        assert metrics["source_hit_rate"] == 1.0
        assert metrics["source_mrr"] == 1.0

    def test_empty_expected_skips_metric(self, entry):
        assert entry.compute_source_metrics(["a/b.pdf"], []) == {}

    def test_dedup_keeps_first_occurrence_rank(self, entry):
        # 去重保序：重复来源不产生重复排名；首中排名按去重后序列。
        metrics = entry.compute_source_metrics(
            ["a/x.md", "a/x.md", "b/target.pdf"], ["target.pdf"]
        )
        assert metrics["source_hit_rate"] == 1.0
        assert metrics["source_mrr"] == pytest.approx(1 / 2)


# ---------------------------------------------------------------------------
# agentic 模式（纯第三路，fake 后端）
# ---------------------------------------------------------------------------


class TestRunModeEvalAgentic:
    def test_pure_third_route_source_metrics_and_tokens(self, entry, fake_settings, tmp_path):
        from libs.agentic_search.base_agentic_search import AgenticEvidence

        backend_name_hit = "/data/docs/chinese_technical_doc.pdf"
        evidences = [
            AgenticEvidence(source_path=backend_name_hit, snippet="s1", score=0.9, char_start=0, char_end=2)
        ]
        from libs.agentic_search.adapter import AgenticRouteAdapter
        from libs.agentic_search.fake_backend import FakeAgenticSearch

        adapter = AgenticRouteAdapter(FakeAgenticSearch(evidences=evidences, total_llm_tokens=1024))
        test_set = _write_test_set(
            tmp_path,
            [
                {"query": "q1", "expected_chunk_ids": ["no_match_chunk"], "expected_sources": ["chinese_technical_doc.pdf"]},
                {"query": "q2", "expected_chunk_ids": ["no_match_chunk"], "expected_sources": ["other.pdf"]},
            ],
        )
        report = entry.run_mode_eval(
            fake_settings, "agentic", test_set, top_k=5, agentic_adapter=adapter
        )
        assert (report.evaluated, report.failed, report.total) == (2, 0, 2)
        # source 级：q1 命中（mrr=1），q2 未命中（mrr=0）→ 均值 0.5 / 0.5。
        assert report.metrics["source_hit_rate"] == pytest.approx(0.5)
        assert report.metrics["source_mrr"] == pytest.approx(0.5)
        # chunk 级：agentic 确定性 ID 与 expected_chunk_ids 永不匹配。
        assert report.metrics["chunk_hit_rate"] == 0.0
        assert report.metrics["chunk_mrr"] == 0.0
        # Token / 延迟：fake 每问 1024 tokens → 总 2048。
        assert report.total_llm_tokens == 2048
        assert report.total_latency_ms is not None
        assert all(q.latency_ms is not None for q in report.per_query)
        # 检索结果为该路的 RetrievalResult 列表（确定性 agentic ID 前缀）。
        assert all(qid.startswith("agentic:") for qid in report.per_query[0].retrieved_ids)

    def test_invalid_mode_raises(self, entry, fake_settings, tmp_path):
        test_set = _write_test_set(tmp_path, [{"query": "q", "expected_chunk_ids": ["a"]}])
        with pytest.raises(ValueError):
            entry.run_mode_eval(fake_settings, "hybrid", test_set)
        with pytest.raises(ValueError):
            entry.run_mode_eval(fake_settings, "nope", test_set)


# ---------------------------------------------------------------------------
# hybrid+agentic 模式（注入 fake 检索栈 + fake 第三路）
# ---------------------------------------------------------------------------


class _FakeHybridCallingAgentic:
    """模拟触发第三路的 HybridSearch：内部调用 adapter.retrieve（Token 采集）。"""

    def __init__(self, adapter: Any, sources: list[str]) -> None:
        self._adapter = adapter
        self._sources = sources

    def search(self, query, top_k=None, filters=None, *, trace=None):
        self._adapter.retrieve(query, top_k=top_k)  # 模拟 agentic 路（Token 消耗）
        return [_result(f"chunk_{i}", 1.0 - i * 0.1, src) for i, src in enumerate(self._sources)]


class _FakeHybridOnly:
    """未触发第三路的 HybridSearch（fallback 条件未满足）。"""

    def __init__(self, sources: list[str]) -> None:
        self._sources = sources

    def search(self, query, top_k=None, filters=None, *, trace=None):
        return [_result(f"chunk_{i}", 1.0 - i * 0.1, src) for i, src in enumerate(self._sources)]


class TestRunModeEvalHybridPlusAgentic:
    def test_triggered_tokens_and_source_metrics(self, entry, fake_settings, tmp_path):
        from libs.agentic_search.adapter import AgenticRouteAdapter
        from libs.agentic_search.fake_backend import FakeAgenticSearch

        adapter = AgenticRouteAdapter(FakeAgenticSearch(total_llm_tokens=512))
        test_set = _write_test_set(
            tmp_path,
            [{"query": "q1", "expected_chunk_ids": ["chunk_0"], "expected_sources": ["target.pdf"]}],
        )
        fake_hs = _FakeHybridCallingAgentic(adapter, ["/docs/target.pdf", "/docs/other.md"])
        report = entry.run_mode_eval(
            fake_settings, "hybrid+agentic", test_set, hybrid_search=fake_hs, agentic_adapter=adapter
        )
        assert report.evaluated == 1
        # chunk 级命中（hybrid 主路 chunk_id 可匹配）与 source 级命中并列。
        assert report.metrics["chunk_hit_rate"] == 1.0
        assert report.metrics["source_hit_rate"] == 1.0
        assert report.metrics["source_mrr"] == 1.0
        # 第三路触发 → Token 采集自适配器。
        assert report.total_llm_tokens == 512

    def test_not_triggered_tokens_none(self, entry, fake_settings, tmp_path):
        test_set = _write_test_set(
            tmp_path,
            [{"query": "q1", "expected_chunk_ids": ["chunk_0"], "expected_sources": ["target.pdf"]}],
        )
        fake_hs = _FakeHybridOnly(["/docs/other.md"])
        report = entry.run_mode_eval(
            fake_settings, "hybrid+agentic", test_set, hybrid_search=fake_hs
        )
        # 未触发第三路：token 无（None），source 级未命中。
        assert report.total_llm_tokens is None
        assert report.metrics["source_hit_rate"] == 0.0
        assert report.metrics["chunk_hit_rate"] == 1.0


# ---------------------------------------------------------------------------
# CLI：mode 非法值报错、真实 sidecar 门控、报告渲染
# ---------------------------------------------------------------------------


class TestCliMode:
    def test_invalid_mode_argparse_exit_2(self, entry, capsys):
        with pytest.raises(SystemExit):
            entry.parse_args(["--retrieval-mode", "nope"])

    def test_default_mode_hybrid(self, entry):
        assert entry.parse_args([]).retrieval_mode == "hybrid"
        assert entry.parse_args([]).agentic_trigger is None

    def test_agentic_mode_real_backend_gated_exit_2(self, entry, tmp_path, capsys):
        # 默认配置 backend=sirchmunk_http 且未设 RAG_AGENTIC_INTEGRATION → 拒绝。
        test_set = _write_test_set(tmp_path, [{"query": "q", "expected_chunk_ids": ["a"]}])
        code = entry.main(
            ["--retrieval-mode", "agentic", "--test-set", str(test_set)],
            out=print,
        )
        assert code == 2
        assert "RAG_AGENTIC_INTEGRATION" in capsys.readouterr().out

    def test_render_mode_report_fields(self, entry, capsys):
        mode_report = entry.ModeEvalReport(
            mode="hybrid+agentic",
            total=1,
            evaluated=1,
            failed=0,
            metrics={"chunk_hit_rate": 1.0, "source_hit_rate": 1.0, "source_mrr": 1.0, "avg_latency_ms": 12.0},
            per_query=[
                entry.ModeQueryDetail(
                    query="q1",
                    retrieved_ids=["chunk_0"],
                    retrieved_sources=["/docs/target.pdf"],
                    expected_chunk_ids=["chunk_0"],
                    expected_sources=["target.pdf"],
                    chunk_metrics={"hit_rate": 1.0, "mrr": 1.0, "hit_at_k": 1.0},
                    source_metrics={"source_hit_rate": 1.0, "source_mrr": 1.0},
                    latency_ms=12.0,
                    total_llm_tokens=100,
                )
            ],
            total_latency_ms=12.0,
            total_llm_tokens=100,
        )
        entry.render_mode_report(mode_report, out=print)
        rendered = capsys.readouterr().out
        assert "[hybrid+agentic]" in rendered
        assert "source_hit_rate" in rendered
        assert "chunk_hit_rate" in rendered
        assert "口径注明" in rendered  # chunk 级指标无意义声明
        assert "total_llm_tokens=100" in rendered
        assert "latency=12.0ms" in rendered
