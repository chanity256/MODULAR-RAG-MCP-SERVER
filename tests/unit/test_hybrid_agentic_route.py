"""HybridSearch 可选第三路 agentic 检索（S2.3）单测。

全离线确定性：fake dense/sparse/agentic 检索器 + 真 QueryProcessor /
RRFFusion / AgenticTrigger（零网络零子进程，不 import sirchmunk）。

覆盖（S2.3 验收标准）：
- 三策略触发矩阵（tool_only 永不触发 / fallback 信号判定 / always 恒触发）。
- ``agentic_retriever=None`` / ``enabled=false`` 不激活第三路。
- 第三路失败降级为 N-1 路（记入 route_failures / degraded），不抛错；
  dense+sparse 全失败仍抛 ``HybridSearchError``。
- 触发后三路统一走 ``fuse_routes``：agentic 结果进入 ``last_fused_results``
  （即上层 MCP tool rerank 管线的统一输入，不绕过）。
- trace ``hybrid`` details：``routes.agentic`` 计数与 ``agentic_trigger``
  （strategy/reason）；失败计入 ``degraded``。
- **回归红线**：相同输入下，未注入 / enabled=false / 未触发的实例与
  基线（无第三路实现）trace 阶段记录与输出结果完全一致。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine.agentic_trigger import (
    TRIGGER_ALWAYS,
    TRIGGER_FALLBACK,
    TRIGGER_TOOL_ONLY,
)
from core.query_engine.hybrid_search import HybridSearch, HybridSearchError
from core.query_engine.fusion import RRFFusion
from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult

_VALID_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
    vision_llm:
      provider: azure
      model: gpt-4o
    vector_store:
      backend: chroma
      persist_path: ./data/db/chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
      top_k_dense: 20
      top_k_sparse: 20
      top_k_final: 10
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _r(chunk_id: str, score: float) -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id, score=score, text=chunk_id, metadata={"src": chunk_id})


class _FakeDense:
    """确定性 Dense fake：两路固定命中（信号充足：RRF 融合分可预期）。"""

    def __init__(self, results: list[RetrievalResult] | None = None) -> None:
        self._results = results if results is not None else [_r("d1", 0.9), _r("d2", 0.8)]

    def retrieve(self, query, top_k, filters=None, *, trace=None):
        return list(self._results)[:top_k]


class _FakeSparse:
    """确定性 Sparse fake。"""

    def __init__(self, results: list[RetrievalResult] | None = None) -> None:
        self._results = results if results is not None else [_r("s1", 12.0), _r("s2", 9.0)]

    def retrieve(self, keywords, top_k, *, trace=None):
        return list(self._results)[:top_k]


class _ExplodingRetriever:
    """总是抛错的 fake（降级测试用）。"""

    def __init__(self, label: str) -> None:
        self._label = label

    def retrieve(self, *args, **kwargs):
        raise RuntimeError(f"{self._label} backend offline")


class _FakeAgenticRetriever:
    """鸭子类型第三路 fake（对齐注入契约：retrieve(query, top_k, trace=...)）。

    对齐 ``BaseAgenticSearch`` 的真实签名说明：真实后端是
    ``search(request) -> AgenticSearchResponse``，由上层适配为
    ``retrieve`` 后注入（见 hybrid_search 模块 docstring）；本 fake
    直接模拟适配后的形态。
    """

    def __init__(
        self,
        results: list[RetrievalResult] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._results = results if results is not None else [_r("agentic_1", 0.7), _r("agentic_2", 0.6)]
        self._error = error
        self.calls = 0
        self.last_query: str | None = None
        self.last_top_k: int | None = None

    def retrieve(self, query, top_k, trace=None):
        self.calls += 1
        self.last_query = query
        self.last_top_k = top_k
        if self._error is not None:
            raise self._error
        return list(self._results)[:top_k]


@pytest.fixture
def settings(tmp_path: Path):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    return load_settings(path)


def _enabled(settings, *, trigger: str, threshold: float = 0.5, min_results: int = 1):
    return replace(
        settings,
        agentic_search=replace(
            settings.agentic_search,
            enabled=True,
            trigger=trigger,
            trigger_score_threshold=threshold,
            trigger_min_results=min_results,
        ),
    )


def _make(settings, *, agentic=None, dense=None, sparse=None) -> HybridSearch:
    return HybridSearch(
        settings,
        dense_retriever=dense if dense is not None else _FakeDense(),
        sparse_retriever=sparse if sparse is not None else _FakeSparse(),
        fusion=RRFFusion(settings),
        agentic_retriever=agentic,
    )


def _trace_stages_normalized(trace: TraceContext) -> list[dict]:
    """trace 阶段记录（剥离时变性字段 elapsed_ms），供逐字节一致断言。"""
    stages = [dict(s) for s in trace.to_dict()["stages"]]
    for stage in stages:
        stage.pop("elapsed_ms", None)  # 时变性字段
        if isinstance(stage.get("details"), dict):
            stage["details"] = {k: v for k, v in stage["details"].items() if k != "elapsed_ms"}
    return stages


# ---------------------------------------------------------------------------
# 触发矩阵：三策略
# ---------------------------------------------------------------------------


def test_tool_only_never_triggers_even_when_enabled(settings):
    agentic = _FakeAgenticRetriever()
    hybrid = _make(_enabled(settings, trigger=TRIGGER_TOOL_ONLY), agentic=agentic)
    results = hybrid.search("query", 5)
    assert agentic.calls == 0
    assert hybrid.last_trigger_decision is not None
    assert hybrid.last_trigger_decision.triggered is False
    assert all(r.chunk_id in {"d1", "d2", "s1", "s2"} for r in results)


def test_fallback_signal_sufficient_does_not_trigger(settings):
    # RRF 融合分远低于默认阈值 0.5（~0.033），阈值取 0.0 表示"信号充足"。
    agentic = _FakeAgenticRetriever()
    hybrid = _make(_enabled(settings, trigger=TRIGGER_FALLBACK, threshold=0.0), agentic=agentic)
    hybrid.search("query", 5)
    assert agentic.calls == 0
    assert hybrid.last_trigger_decision.triggered is False


def test_fallback_low_score_triggers(settings):
    # 两路 RRF 融合 top 分 ~0.033 < 默认阈值 0.5 → 触发。
    agentic = _FakeAgenticRetriever()
    hybrid = _make(_enabled(settings, trigger=TRIGGER_FALLBACK), agentic=agentic)
    results = hybrid.search("query", 10)
    assert agentic.calls == 1
    assert agentic.last_query == "query"
    assert agentic.last_top_k == 20  # 取 top_k_dense 作候选上限
    assert hybrid.last_trigger_decision.triggered is True
    fused_ids = {r.chunk_id for r in hybrid.last_fused_results}
    assert {"agentic_1", "agentic_2"} <= fused_ids
    assert any(r.chunk_id == "agentic_1" for r in results)


def test_fallback_empty_main_results_triggers(settings):
    agentic = _FakeAgenticRetriever()
    hybrid = _make(
        _enabled(settings, trigger=TRIGGER_FALLBACK),
        agentic=agentic,
        dense=_FakeDense(results=[]),
        sparse=_FakeSparse(results=[]),
    )
    hybrid.search("query", 5)
    assert agentic.calls == 1
    assert hybrid.last_trigger_decision.triggered is True


def test_always_triggers_every_time(settings):
    agentic = _FakeAgenticRetriever()
    hybrid = _make(_enabled(settings, trigger=TRIGGER_ALWAYS), agentic=agentic)
    for _ in range(2):
        hybrid.search("query", 5)
    assert agentic.calls == 2
    assert hybrid.last_trigger_decision.triggered is True


@pytest.mark.parametrize(
    "trigger,threshold,expected_calls",
    [
        (TRIGGER_TOOL_ONLY, 0.5, 0),
        (TRIGGER_FALLBACK, 0.0, 0),   # 信号充足
        (TRIGGER_FALLBACK, 0.5, 1),   # RRF 融合分不足
        (TRIGGER_ALWAYS, 0.5, 1),
    ],
)
def test_trigger_matrix(settings, trigger, threshold, expected_calls):
    agentic = _FakeAgenticRetriever()
    hybrid = _make(_enabled(settings, trigger=trigger, threshold=threshold), agentic=agentic)
    hybrid.search("query", 5)
    assert agentic.calls == expected_calls


# ---------------------------------------------------------------------------
# 未激活路径（回归红线）
# ---------------------------------------------------------------------------


def test_none_retriever_baseline_identical(settings):
    baseline = _make(settings)
    hybrid = _make(settings, agentic=None)  # 显式 None
    t1, t2 = TraceContext(), TraceContext()
    r1 = baseline.search("query", 5, trace=t1)
    r2 = hybrid.search("query", 5, trace=t2)
    assert r1 == r2
    assert _trace_stages_normalized(t1) == _trace_stages_normalized(t2)
    assert hybrid.last_trigger_decision is None
    assert hybrid.last_agentic_results == []
    assert baseline.last_fused_results == hybrid.last_fused_results
    assert baseline.last_route_failures == hybrid.last_route_failures == {}


def test_enabled_false_ignores_injected_retriever(settings):
    # settings 默认 enabled=False：即使注入 retriever 且策略 always 也不激活。
    agentic = _FakeAgenticRetriever()
    hybrid = _make(settings, agentic=agentic)  # settings.agentic_search.enabled is False
    baseline = _make(settings)
    t1, t2 = TraceContext(), TraceContext()
    r1 = baseline.search("query", 5, trace=t1)
    r2 = hybrid.search("query", 5, trace=t2)
    assert agentic.calls == 0
    assert r1 == r2
    assert _trace_stages_normalized(t1) == _trace_stages_normalized(t2)


def test_untriggered_trace_details_have_no_agentic_fields(settings):
    # enabled + tool_only（永不触发）：hybrid details 不含任何 agentic 字段。
    agentic = _FakeAgenticRetriever()
    hybrid = _make(_enabled(settings, trigger=TRIGGER_TOOL_ONLY), agentic=agentic)
    trace = TraceContext()
    hybrid.search("query", 5, trace=trace)
    stage = trace.get_stage("hybrid")
    assert "agentic" not in stage["details"]["routes"]
    assert "agentic_trigger" not in stage["details"]
    assert stage["details"]["degraded"] == []


# ---------------------------------------------------------------------------
# 触发路径：trace / 融合 / 降级
# ---------------------------------------------------------------------------


def test_triggered_trace_records_agentic_count_and_trigger_info(settings):
    agentic = _FakeAgenticRetriever()
    hybrid = _make(_enabled(settings, trigger=TRIGGER_ALWAYS), agentic=agentic)
    trace = TraceContext()
    results = hybrid.search("query", 10, trace=trace)
    stage = trace.get_stage("hybrid")
    assert stage["details"]["routes"]["agentic"] == 2
    info = stage["details"]["agentic_trigger"]
    assert info["strategy"] == TRIGGER_ALWAYS
    assert info["triggered"] is True
    assert info["reason"]
    # 融合阶段走 fuse_routes：details 为 routes 嵌套形态且含 agentic 计数。
    fusion_stage = trace.get_stage("fusion")
    assert fusion_stage["details"]["routes"]["agentic"] == 2
    # agentic 结果与 dense/sparse 一起进入统一融合输出（上层 rerank 管线输入）。
    fused_ids = {r.chunk_id for r in hybrid.last_fused_results}
    assert {"d1", "s1", "agentic_1"} <= fused_ids
    assert set(r.chunk_id for r in results) <= fused_ids


def test_agentic_failure_degrades_to_two_routes(settings):
    agentic = _FakeAgenticRetriever(error=RuntimeError("sidecar down"))
    hybrid = _make(_enabled(settings, trigger=TRIGGER_ALWAYS), agentic=agentic)
    trace = TraceContext()
    results = hybrid.search("query", 10, trace=trace)  # 不抛错：降级为两路
    assert agentic.calls == 1
    assert hybrid.last_route_failures["agentic"] == "RuntimeError: sidecar down"
    assert hybrid.last_agentic_results == []
    stage = trace.get_stage("hybrid")
    assert stage["details"]["routes"]["agentic"] == 0
    assert "agentic" in stage["details"]["degraded"]
    assert stage["details"]["agentic_trigger"]["triggered"] is True
    # 存活两路正常融合。
    fused_ids = {r.chunk_id for r in hybrid.last_fused_results}
    assert {"d1", "d2", "s1", "s2"} <= fused_ids
    assert results


def test_agentic_failure_with_single_surviving_main_route(settings):
    agentic = _FakeAgenticRetriever(error=RuntimeError("sidecar down"))
    hybrid = _make(
        _enabled(settings, trigger=TRIGGER_ALWAYS),
        agentic=agentic,
        sparse=_ExplodingRetriever("bm25"),
    )
    results = hybrid.search("query", 10)  # dense 存活 + agentic 失败 → 单路
    assert set(hybrid.last_route_failures) == {"agentic", "sparse"}
    assert [r.chunk_id for r in results] == ["d1", "d2"]


def test_both_main_routes_fail_raise_even_with_always(settings):
    agentic = _FakeAgenticRetriever()
    hybrid = _make(
        _enabled(settings, trigger=TRIGGER_ALWAYS),
        agentic=agentic,
        dense=_ExplodingRetriever("dense"),
        sparse=_ExplodingRetriever("bm25"),
    )
    with pytest.raises(HybridSearchError):
        hybrid.search("query", 5)
    # 主路全失败在触发判定之前即抛错：第三路不发起调用。
    assert agentic.calls == 0
