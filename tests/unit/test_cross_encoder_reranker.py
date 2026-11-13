"""Cross-Encoder Reranker 单元测试（阶段 B7.8，mock scorer）。

通过注入 mock scorer 验证：
- backend=cross_encoder 时 RerankerFactory 可创建。
- 正常打分排序：candidates 按分数降序返回，score 更新为 scorer 输出。
- 确定性：相同输入相同输出。
- 失败回退：scorer 异常/超时 → 原序 + last_fallback=True。
- scorer 返回长度不匹配 → 回退原序。
- top_m 截断。
- 空候选 / 单候选 / 非列表输入。
- 无 scorer 时报错（除非 kwargs 临时注入）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.reranker.base_reranker import RerankCandidate
from libs.reranker.cross_encoder_reranker import CrossEncoderReranker
from libs.reranker.reranker_factory import RerankerFactory

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
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path, **rerank_overrides):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if rerank_overrides:
        settings = replace(settings, rerank=replace(settings.rerank, **rerank_overrides))
    return settings


# --- mock scorers --------------------------------------------------------


def _deterministic_scorer(query: str, texts):
    """基于 query 与 text 的简单重叠度打分，保证 deterministic。"""
    query_words = set(query.lower().split())
    scores = []
    for t in texts:
        if not isinstance(t, str):
            t = str(t)
        text_words = set(t.lower().split())
        overlap = len(query_words & text_words)
        scores.append(float(overlap))
    return scores


def _fixed_scorer(scores):
    """返回预设分数列表的 scorer 工厂。"""

    def _scorer(query, texts):
        return list(scores)

    return _scorer


def _raising_scorer(exc):
    """总是抛出异常的 scorer。"""

    def _scorer(query, texts):
        raise exc

    return _scorer


@pytest.fixture
def registered():
    import libs.reranker  # noqa: F401
    yield RerankerFactory


# ---------------------------------------------------------------------------
# 工厂创建
# ---------------------------------------------------------------------------


def test_factory_creates_cross_encoder(tmp_path, registered):
    settings = _settings(tmp_path, backend="cross_encoder")
    reranker = registered.create(settings, scorer=_deterministic_scorer)
    assert isinstance(reranker, CrossEncoderReranker)


def test_cross_encoder_registered_in_factory(registered):
    assert "cross_encoder" in RerankerFactory.available_backends()


def test_factory_without_scorer_still_creates(tmp_path, registered):
    """工厂允许不传 scorer（延迟到 rerank 时报错或注入）。"""
    settings = _settings(tmp_path, backend="cross_encoder")
    reranker = registered.create(settings)
    assert isinstance(reranker, CrossEncoderReranker)


# ---------------------------------------------------------------------------
# 正常打分排序
# ---------------------------------------------------------------------------


def test_basic_rerank_sorts_by_score_desc():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [
        RerankCandidate(id="a", text="apple banana", score=0.0),
        RerankCandidate(id="b", text="cherry date", score=0.0),
        RerankCandidate(id="c", text="apple cherry", score=0.0),
    ]
    # scorer: "apple" 出现在 a, c → 得分高
    reranker = CrossEncoderReranker(rc, scorer=_deterministic_scorer)
    result = reranker.rerank("apple", candidates)
    assert len(result) == 3
    # a 和 c 都含 "apple"，得分相同但稳定排序保持原序
    assert result[0].id in ("a", "c")
    # b 不含 "apple"，得分最低
    assert result[-1].id == "b"


def test_rerank_scores_updated_from_scorer():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [
        RerankCandidate(id="x", text="text1", score=99.0),
        RerankCandidate(id="y", text="text2", score=88.0),
    ]
    reranker = CrossEncoderReranker(rc, scorer=_fixed_scorer([0.3, 0.7]))
    result = reranker.rerank("query", candidates)
    # score 应被更新为 scorer 输出，而非保留原始
    assert result[0].id == "y"
    assert result[0].score == 0.7
    assert result[1].id == "x"
    assert result[1].score == 0.3


def test_rerank_is_deterministic():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [
        RerankCandidate(id=str(i), text=f"text_{i}", score=0.0) for i in range(5)
    ]
    reranker = CrossEncoderReranker(rc, scorer=_fixed_scorer([0.1, 0.5, 0.3, 0.9, 0.2]))
    r1 = reranker.rerank("query", candidates)
    r2 = reranker.rerank("query", candidates)
    assert [r.id for r in r1] == [r.id for r in r2]
    assert [r.score for r in r1] == [r.score for r in r2]


def test_rerank_preserves_text_and_metadata():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [
        RerankCandidate(id="a", text="hello", score=0.0, metadata={"page": 1}),
    ]
    reranker = CrossEncoderReranker(rc, scorer=_fixed_scorer([0.9]))
    result = reranker.rerank("query", candidates)
    assert result[0].text == "hello"
    assert result[0].metadata == {"page": 1}


# ---------------------------------------------------------------------------
# 失败回退
# ---------------------------------------------------------------------------


def test_scorer_exception_falls_back_to_original_order():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [RerankCandidate(id=str(i), text=f"t{i}") for i in range(3)]
    reranker = CrossEncoderReranker(rc, scorer=_raising_scorer(RuntimeError("model load failed")))
    result = reranker.rerank("query", candidates)
    assert [r.id for r in result] == ["0", "1", "2"]
    assert reranker.last_fallback is True


def test_scorer_timeout_falls_back():
    import socket

    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [RerankCandidate(id="a", text="a"), RerankCandidate(id="b", text="b")]
    reranker = CrossEncoderReranker(rc, scorer=_raising_scorer(socket.timeout("timed out")))
    result = reranker.rerank("query", candidates)
    assert [r.id for r in result] == ["a", "b"]
    assert reranker.last_fallback is True


def test_scorer_wrong_length_falls_back():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [RerankCandidate(id=str(i), text=f"t{i}") for i in range(3)]
    # scorer 返回 2 个分数而非 3 个
    reranker = CrossEncoderReranker(rc, scorer=_fixed_scorer([0.1, 0.2]))
    result = reranker.rerank("query", candidates)
    assert [r.id for r in result] == ["0", "1", "2"]
    assert reranker.last_fallback is True


def test_scorer_returns_non_list_falls_back():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [RerankCandidate(id="a", text="a")]

    def bad_scorer(query, texts):
        return 0.5  # 不是列表

    reranker = CrossEncoderReranker(rc, scorer=bad_scorer)
    result = reranker.rerank("query", candidates)
    assert [r.id for r in result] == ["a"]
    assert reranker.last_fallback is True


# ---------------------------------------------------------------------------
# top_m 截断
# ---------------------------------------------------------------------------


def test_top_m_truncates_candidates():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder", top_m=2)
    candidates = [RerankCandidate(id=str(i), text=f"t{i}") for i in range(5)]
    reranker = CrossEncoderReranker(rc, scorer=_fixed_scorer([0.9, 0.1]))
    result = reranker.rerank("query", candidates)
    assert len(result) == 2
    assert result[0].id == "0"
    assert result[1].id == "1"


# ---------------------------------------------------------------------------
# scorer 注入
# ---------------------------------------------------------------------------


def test_scorer_injected_via_kwargs_overrides_default():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [RerankCandidate(id="a", text="a"), RerankCandidate(id="b", text="b")]
    reranker = CrossEncoderReranker(rc, scorer=_fixed_scorer([0.1, 0.2]))
    # kwargs 中的 scorer 覆盖构造时的 scorer
    result = reranker.rerank("query", candidates, scorer=_fixed_scorer([0.9, 0.1]))
    assert result[0].id == "a"  # score 0.9 > 0.1
    assert result[0].score == 0.9


def test_no_scorer_raises_on_rerank():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    reranker = CrossEncoderReranker(rc)  # no scorer
    with pytest.raises(RuntimeError, match="scorer"):
        reranker.rerank("query", [RerankCandidate(id="a", text="a")])


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_empty():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    reranker = CrossEncoderReranker(rc, scorer=_deterministic_scorer)
    assert reranker.rerank("query", []) == []


def test_single_candidate():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    reranker = CrossEncoderReranker(rc, scorer=_fixed_scorer([0.5]))
    result = reranker.rerank("query", [RerankCandidate(id="only", text="text")])
    assert len(result) == 1
    assert result[0].id == "only"
    assert result[0].score == 0.5


def test_non_list_candidates_raises():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    reranker = CrossEncoderReranker(rc, scorer=_deterministic_scorer)
    with pytest.raises(ValueError):
        reranker.rerank("query", "not a list")  # type: ignore[arg-type]


def test_tie_scores_stable_sort():
    """分数相同时，保持原始顺序（stable sort）。"""
    from core.settings import RerankConfig

    rc = RerankConfig(backend="cross_encoder")
    candidates = [
        RerankCandidate(id="a", text="a"),
        RerankCandidate(id="b", text="b"),
        RerankCandidate(id="c", text="c"),
    ]
    reranker = CrossEncoderReranker(rc, scorer=_fixed_scorer([0.5, 0.5, 0.5]))
    result = reranker.rerank("query", candidates)
    assert [r.id for r in result] == ["a", "b", "c"]  # 原序
