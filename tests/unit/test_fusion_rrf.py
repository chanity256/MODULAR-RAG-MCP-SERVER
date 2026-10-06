"""``RRFFusion`` 单元测试（阶段 D4）。

覆盖：

- **融合数学**（纯函数 ``rrf_fuse_rankings``，手工计算基准值）：
  两路均命中者得分 = 两路排名倒数之和；只在单路出现者只有单路贡献。
- **确定性**：同输入重复调用输出完全一致；同分按 chunk_id 升序。
- **k 参数可配**：显式参数 > ``settings.retrieval.rrf_k`` > 默认 60。
- **payload 语义**：text/metadata 取首次出现路由（dense 优先）。
- **top_k 截断**与 fail-fast 校验。
- trace 打点 ``fusion`` 阶段。
- **N 路融合**（S2.1 ``fuse_routes``）：两路/三路重叠、单路空、
  agentic-only、payload 优先级（dense>sparse>agentic，与传入顺序无关）、
  top_k 截断、确定性与 trace ``routes`` 计数。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine.fusion import DEFAULT_RRF_K, RRFFusion, rrf_fuse_rankings
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
      rrf_k: 42
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path, rrf_k: int | None = None):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if rrf_k is not None:
        settings = replace(
            settings, retrieval=replace(settings.retrieval, rrf_k=rrf_k)
        )
    return settings


def _result(chunk_id: str, *, text: str = "", metadata: dict | None = None, score: float = 0.5) -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id, score=score, text=text or f"text-{chunk_id}", metadata=metadata or {})


# ---------------------------------------------------------------------------
# 纯函数：融合数学（手工基准）
# ---------------------------------------------------------------------------


def test_pure_function_hand_computed_scores():
    # dense 排名 [a, b]，sparse 排名 [b, a]，k=60：
    #   a: 1/(60+1) + 1/(60+2)；b: 1/(60+2) + 1/(60+1) → 同分，id 升序 a 在前。
    fused = rrf_fuse_rankings([["a", "b"], ["b", "a"]], k=60)
    assert fused[0][0] == "a"
    assert fused[1][0] == "b"
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[1][1] == pytest.approx(1 / 62 + 1 / 61)


def test_both_routes_beats_single_route():
    # c 只在 dense 第 1 名：1/61 ≈ 0.0164；
    # d 在 dense 第 2 + sparse 第 2：2/62 ≈ 0.0323 → d 胜出。
    fused = rrf_fuse_rankings([["c", "d"], ["x", "d"]], k=60)
    assert [cid for cid, _ in fused] == ["d", "c", "x"]


def test_pure_function_k_zero_changes_values():
    fused = rrf_fuse_rankings([["a"]], k=0)
    assert fused == [("a", 1.0)]


def test_pure_function_duplicate_id_in_route_counted_once():
    # 同一路内重复 id 只按首次出现的排名计一次（占位但不重复计分）。
    fused = rrf_fuse_rankings([["a", "a", "b"]], k=60)
    assert dict(fused) == {"a": pytest.approx(1 / 61), "b": pytest.approx(1 / 63)}


def test_pure_function_empty_inputs():
    assert rrf_fuse_rankings([[], []]) == []
    assert rrf_fuse_rankings([]) == []


@pytest.mark.parametrize("bad_k", [-1, 1.5, "60", None, True])
def test_pure_function_invalid_k_raises(bad_k):
    with pytest.raises(ValueError):
        rrf_fuse_rankings([["a"]], k=bad_k)


# ---------------------------------------------------------------------------
# RRFFusion 类：RetrievalResult 组装
# ---------------------------------------------------------------------------


def test_fuse_orders_by_rrf_score_desc():
    dense = [_result("c"), _result("d")]
    sparse = [_result("x"), _result("d")]
    fused = RRFFusion(k=60).fuse(dense, sparse)
    assert [r.chunk_id for r in fused] == ["d", "c", "x"]
    assert fused[0].score == pytest.approx(round(2 / 62, 6))


def test_fuse_tie_breaks_by_chunk_id_ascending():
    dense = [_result("b"), _result("a")]
    sparse = [_result("a"), _result("b")]
    fused = RRFFusion(k=60).fuse(dense, sparse)
    assert [r.chunk_id for r in fused] == ["a", "b"]


def test_fuse_payload_prefers_dense_route():
    dense = [_result("shared", text="dense-text", metadata={"from": "dense"})]
    sparse = [_result("shared", text="sparse-text", metadata={"from": "sparse"})]
    fused = RRFFusion(k=60).fuse(dense, sparse)
    assert len(fused) == 1
    assert fused[0].text == "dense-text"
    assert fused[0].metadata == {"from": "dense"}


def test_fuse_payload_from_sparse_when_only_sparse():
    sparse = [_result("only_sparse", text="sparse-text", metadata={"page": 9})]
    fused = RRFFusion(k=60).fuse([], sparse)
    assert fused[0].text == "sparse-text"
    assert fused[0].metadata == {"page": 9}


def test_fuse_single_route_preserves_order_with_rrf_scores():
    dense = [_result("a"), _result("b"), _result("c")]
    fused = RRFFusion(k=60).fuse(dense, [])
    assert [r.chunk_id for r in fused] == ["a", "b", "c"]
    assert [r.score for r in fused] == [
        pytest.approx(round(1 / 61, 6)),
        pytest.approx(round(1 / 62, 6)),
        pytest.approx(round(1 / 63, 6)),
    ]


def test_fuse_both_empty_returns_empty():
    assert RRFFusion(k=60).fuse([], []) == []


def test_fuse_is_deterministic():
    dense = [_result(f"d{i}") for i in range(5)]
    sparse = [_result(f"s{i}") for i in reversed(range(5))]
    fusion = RRFFusion(k=60)
    assert fusion.fuse(dense, sparse) == fusion.fuse(dense, sparse)


def test_fuse_top_k_truncates():
    dense = [_result(f"d{i}") for i in range(5)]
    fused = RRFFusion(k=60).fuse(dense, [], top_k=3)
    assert [r.chunk_id for r in fused] == ["d0", "d1", "d2"]


def test_fuse_metadata_is_copied_not_shared():
    original = {"page": 1}
    dense = [_result("a", metadata=original)]
    fused = RRFFusion(k=60).fuse(dense, [])
    fused[0].metadata["page"] = 99
    assert original == {"page": 1}  # 融合结果与输入载荷互不影响


# ---------------------------------------------------------------------------
# k 参数解析（显式 > settings.retrieval.rrf_k > 默认 60）
# ---------------------------------------------------------------------------


def test_k_defaults_to_60_without_settings():
    assert RRFFusion().k == DEFAULT_RRF_K == 60


def test_k_reads_from_settings(tmp_path):
    assert RRFFusion(_settings(tmp_path)).k == 42


def test_k_reads_from_settings_override(tmp_path):
    assert RRFFusion(_settings(tmp_path, rrf_k=7)).k == 7


def test_explicit_k_wins_over_settings(tmp_path):
    assert RRFFusion(_settings(tmp_path), k=5).k == 5


@pytest.mark.parametrize("bad_k", [-1, 2.5, "60", True])
def test_invalid_k_raises(bad_k):
    with pytest.raises(ValueError):
        RRFFusion(k=bad_k)


def test_yaml_rrf_k_is_config_driven(tmp_path):
    """settings.yaml 的 retrieval.rrf_k 驱动默认 k（配置驱动验收）。"""
    settings = _settings(tmp_path)
    assert settings.retrieval.rrf_k == 42


# ---------------------------------------------------------------------------
# fail-fast 与 trace
# ---------------------------------------------------------------------------


def test_fuse_rejects_non_list_routes():
    fusion = RRFFusion(k=60)
    with pytest.raises(TypeError, match="dense"):
        fusion.fuse("nope", [])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="sparse"):
        fusion.fuse([], {"a": 1})  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_top_k", [0, -1, 1.5, "3", None if False else False])
def test_fuse_invalid_top_k_raises(bad_top_k):
    with pytest.raises(ValueError):
        RRFFusion(k=60).fuse([_result("a")], [], top_k=bad_top_k)


def test_fuse_records_trace_stage():
    dense = [_result("a"), _result("b")]
    sparse = [_result("b"), _result("c")]
    trace = TraceContext()
    RRFFusion(k=60).fuse(dense, sparse, top_k=2, trace=trace)
    stage = trace.get_stage("fusion")
    assert stage is not None
    assert stage["method"] == "rrf"
    assert stage["details"]["k"] == 60
    assert stage["details"]["dense"] == 2
    assert stage["details"]["sparse"] == 2
    assert stage["details"]["fused"] == 2
    assert "elapsed_ms" in stage["details"]


def test_fuse_no_trace_no_error():
    assert RRFFusion(k=60).fuse([_result("a")], []) is not None


# ---------------------------------------------------------------------------
# fuse_routes：N 路融合（S2.1，手工基准）
# ---------------------------------------------------------------------------


def test_fuse_routes_two_routes_hand_computed():
    # 与纯函数基准同构：dense [a, b]，sparse [b, a]，k=60 → 同分，id 升序。
    fused = RRFFusion(k=60).fuse_routes(
        {"dense": [_result("a"), _result("b")], "sparse": [_result("b"), _result("a")]}
    )
    assert [r.chunk_id for r in fused] == ["a", "b"]
    assert fused[0].score == pytest.approx(round(1 / 61 + 1 / 62, 6))
    assert fused[1].score == pytest.approx(round(1 / 62 + 1 / 61, 6))


def test_fuse_routes_three_routes_hand_computed():
    # 三路重叠，k=60：a 在 dense/sparse 第 1 + agentic 第 2：
    #   1/61 + 1/61 + 1/62 ≈ 0.049155；
    # b 在 dense 第 2 + sparse 第 2：2/62 ≈ 0.032258；
    # c 仅 agentic 第 1：1/61 ≈ 0.016393。
    dense = [_result("a"), _result("b")]
    sparse = [_result("a"), _result("b")]
    agentic = [_result("c"), _result("a")]
    fused = RRFFusion(k=60).fuse_routes({"dense": dense, "sparse": sparse, "agentic": agentic})
    assert [r.chunk_id for r in fused] == ["a", "b", "c"]
    assert fused[0].score == pytest.approx(round(1 / 61 + 1 / 61 + 1 / 62, 6))
    assert fused[1].score == pytest.approx(round(2 / 62, 6))
    assert fused[2].score == pytest.approx(round(1 / 61, 6))


def test_fuse_routes_single_route_empty():
    # 只有 dense 路有结果，sparse/agentic 传空：单路排名 + RRF 分数。
    fused = RRFFusion(k=60).fuse_routes(
        {"dense": [_result("a"), _result("b")], "sparse": [], "agentic": []}
    )
    assert [r.chunk_id for r in fused] == ["a", "b"]
    assert fused[0].score == pytest.approx(round(1 / 61, 6))


def test_fuse_routes_agentic_only():
    # 仅 agentic 路：c 第 1、d 第 2 → 1/61、1/62；payload 取 agentic 载荷。
    agentic = [
        _result("c", text="agent-c", metadata={"route": "agentic"}),
        _result("d", text="agent-d", metadata={"route": "agentic"}),
    ]
    fused = RRFFusion(k=60).fuse_routes({"agentic": agentic})
    assert [r.chunk_id for r in fused] == ["c", "d"]
    assert fused[0].score == pytest.approx(round(1 / 61, 6))
    assert fused[0].text == "agent-c"
    assert fused[0].metadata == {"route": "agentic"}


def test_fuse_routes_payload_priority_not_dict_order():
    # 传入顺序故意打乱（sparse 在前）：payload 仍取 dense 载荷（规范优先级
    # dense > sparse > agentic，不依赖 dict 传入顺序）。
    sparse = [_result("shared", text="sparse-text", metadata={"from": "sparse"})]
    agentic = [_result("shared", text="agentic-text", metadata={"from": "agentic"})]
    dense = [_result("shared", text="dense-text", metadata={"from": "dense"})]
    fused = RRFFusion(k=60).fuse_routes({"sparse": sparse, "agentic": agentic, "dense": dense})
    assert len(fused) == 1
    assert fused[0].text == "dense-text"
    assert fused[0].metadata == {"from": "dense"}
    # 三路同分（各第 1）：1/61 + 1/61 + 1/61。
    assert fused[0].score == pytest.approx(round(3 / 61, 6))


def test_fuse_routes_payload_falls_back_to_sparse_then_agentic():
    # chunk 只在 sparse 与 agentic：payload 取 sparse（优先级居中）。
    sparse = [_result("x", text="sparse-text", metadata={"from": "sparse"})]
    agentic = [_result("x", text="agentic-text", metadata={"from": "agentic"})]
    fused = RRFFusion(k=60).fuse_routes({"sparse": sparse, "agentic": agentic})
    assert fused[0].text == "sparse-text"
    assert fused[0].metadata == {"from": "sparse"}


def test_fuse_routes_top_k_truncates():
    dense = [_result(f"d{i}") for i in range(5)]
    fused = RRFFusion(k=60).fuse_routes({"dense": dense, "sparse": []}, top_k=3)
    assert [r.chunk_id for r in fused] == ["d0", "d1", "d2"]


def test_fuse_routes_tie_breaks_by_chunk_id_ascending():
    # 单路同分不可能（排名不同），用两路交叉制造同分：a/b 均为 1/61+1/62。
    fused = RRFFusion(k=60).fuse_routes(
        {"dense": [_result("b"), _result("a")], "sparse": [_result("a"), _result("b")]}
    )
    assert [r.chunk_id for r in fused] == ["a", "b"]


def test_fuse_routes_is_deterministic():
    routes = {
        "dense": [_result(f"d{i}") for i in range(5)],
        "sparse": [_result(f"s{i}") for i in reversed(range(5))],
        "agentic": [_result("a1"), _result("d0")],
    }
    fusion = RRFFusion(k=60)
    assert fusion.fuse_routes(dict(routes)) == fusion.fuse_routes(dict(routes))


def test_fuse_routes_empty_dict_returns_empty():
    assert RRFFusion(k=60).fuse_routes({}) == []


def test_fuse_routes_records_trace_stage_with_route_counts():
    dense = [_result("a"), _result("b")]
    sparse = [_result("b"), _result("c")]
    agentic = [_result("c"), _result("d"), _result("a")]
    trace = TraceContext()
    RRFFusion(k=60).fuse_routes(
        {"dense": dense, "sparse": sparse, "agentic": agentic}, top_k=3, trace=trace
    )
    stage = trace.get_stage("fusion")
    assert stage is not None
    assert stage["method"] == "rrf"
    assert stage["details"]["k"] == 60
    assert stage["details"]["routes"] == {"dense": 2, "sparse": 2, "agentic": 3}
    assert stage["details"]["fused"] == 3
    assert "elapsed_ms" in stage["details"]
    expected = RRFFusion(k=60).fuse_routes({"dense": dense, "sparse": sparse, "agentic": agentic})
    assert stage["details"]["preview"] == [
        {"chunk_id": r.chunk_id, "score": float(r.score)} for r in expected[:3]
    ]


def test_fuse_routes_rejects_non_dict_and_non_list():
    fusion = RRFFusion(k=60)
    with pytest.raises(TypeError, match="routes"):
        fusion.fuse_routes([("dense", [])])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="agentic"):
        fusion.fuse_routes({"agentic": "nope"})  # type: ignore[arg-type]


def test_fuse_routes_invalid_top_k_raises():
    with pytest.raises(ValueError):
        RRFFusion(k=60).fuse_routes({"dense": []}, top_k=0)


def test_fuse_delegates_to_fuse_routes_math_identical():
    # 委托一致性：fuse 与 fuse_routes({"dense":…, "sparse":…}) 结果逐字段相同。
    dense = [_result("a"), _result("shared", text="dense-text", metadata={"from": "dense"})]
    sparse = [_result("shared", text="sparse-text", metadata={"from": "sparse"}), _result("b")]
    fusion = RRFFusion(k=60)
    assert fusion.fuse(dense, sparse) == fusion.fuse_routes({"dense": dense, "sparse": sparse})
