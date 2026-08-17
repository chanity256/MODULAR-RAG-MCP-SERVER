"""``RRFFusion`` 单元测试（阶段 D4）。

覆盖：

- **融合数学**（纯函数 ``rrf_fuse_rankings``，手工计算基准值）：
  两路均命中者得分 = 两路排名倒数之和；只在单路出现者只有单路贡献。
- **确定性**：同输入重复调用输出完全一致；同分按 chunk_id 升序。
- **k 参数可配**：显式参数 > ``settings.retrieval.rrf_k`` > 默认 60。
- **payload 语义**：text/metadata 取首次出现路由（dense 优先）。
- **top_k 截断**与 fail-fast 校验。
- trace 打点 ``fusion`` 阶段。
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
