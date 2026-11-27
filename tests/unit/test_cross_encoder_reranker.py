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


# ---------------------------------------------------------------------------
# CER2：工厂默认惰性装配（不改上方既有用例，只新增）
# ---------------------------------------------------------------------------


def test_factory_default_lazy_assembly_zero_io_then_build_once(tmp_path, registered, monkeypatch):
    """工厂未注入 scorer 时登记默认工厂：构造零 IO、首次 rerank 才构建并缓存。"""
    import libs.reranker.cross_encoder_reranker as cer_module

    factory_calls: list[int] = []

    def fake_make_default(rc):
        def _factory():
            factory_calls.append(1)
            return _fixed_scorer([0.9, 0.1])

        return _factory

    monkeypatch.setattr(cer_module, "_make_default_scorer_factory", fake_make_default)
    settings = _settings(tmp_path, backend="cross_encoder")

    backend = registered.create(settings)
    assert factory_calls == []  # 构造零 IO：默认工厂只登记、未调用

    candidates = [RerankCandidate(id="a", text="a"), RerankCandidate(id="b", text="b")]
    r1 = backend.rerank("q", candidates)
    assert factory_calls == [1]  # 首次 rerank 才构建
    assert [r.id for r in r1] == ["a", "b"]  # 0.9 > 0.1
    assert r1[0].score == 0.9

    r2 = backend.rerank("q", candidates)
    assert factory_calls == [1]  # 已缓存，工厂只调一次


def test_explicit_scorer_beats_default_factory(tmp_path, registered, monkeypatch):
    """显式注入 scorer 时默认工厂不得被创建（三段优先级第 1 段）。"""
    import libs.reranker.cross_encoder_reranker as cer_module

    def boom(rc):
        raise AssertionError("default scorer factory must not be built when scorer is explicit")

    monkeypatch.setattr(cer_module, "_make_default_scorer_factory", boom)
    settings = _settings(tmp_path, backend="cross_encoder")

    backend = registered.create(settings, scorer=_fixed_scorer([0.7, 0.2]))
    result = backend.rerank("q", [RerankCandidate(id="a", text="a"), RerankCandidate(id="b", text="b")])
    assert [r.id for r in result] == ["a", "b"]


def test_explicit_scorer_factory_beats_default_and_caches(tmp_path, registered, monkeypatch):
    """显式注入 scorer_factory 优先于默认工厂，且构建后缓存（只调一次）。"""
    import libs.reranker.cross_encoder_reranker as cer_module

    def boom(rc):
        raise AssertionError("default scorer factory must not be built when scorer_factory is explicit")

    monkeypatch.setattr(cer_module, "_make_default_scorer_factory", boom)

    factory_calls: list[int] = []

    def explicit_factory():
        factory_calls.append(1)
        return _fixed_scorer([0.2, 0.8])

    settings = _settings(tmp_path, backend="cross_encoder")
    backend = registered.create(settings, scorer_factory=explicit_factory)

    candidates = [RerankCandidate(id="a", text="a"), RerankCandidate(id="b", text="b")]
    r1 = backend.rerank("q", candidates)
    r2 = backend.rerank("q", candidates)

    assert factory_calls == [1]  # 只构建一次并缓存
    assert [r.id for r in r1] == ["b", "a"]
    assert [r.id for r in r2] == ["b", "a"]


def test_default_factory_load_failure_walks_d6_channel1(tmp_path, registered, monkeypatch):
    """默认工厂加载失败 → _ensure_scorer 抛错走 D6 通道①：诊断信息完整保留。"""
    from core.query_engine.reranker import Reranker
    from core.trace.trace_context import TraceContext
    from core.types import RetrievalResult
    from libs.reranker.cross_encoder_scorer import CrossEncoderScorer

    hint = 'pip install -e ".[rerank]"'

    def _raise(self):
        raise RuntimeError(f"sentence-transformers is not installed. Install: {hint}")

    monkeypatch.setattr(CrossEncoderScorer, "ensure_loaded", _raise)
    settings = _settings(tmp_path, backend="cross_encoder", model="cross-encoder/test-model")

    trace = TraceContext(trace_type="query")
    results = [RetrievalResult(chunk_id="c1", score=0.5, text="t1", metadata={})]
    out = Reranker(settings).rerank("q", results, trace=trace)

    # D6 通道①：完整输入原序原分 + fallback 标记 + last_error 保留指引
    assert [r.chunk_id for r in out] == ["c1"]
    assert out[0].score == 0.5
    core = Reranker(settings)
    core.rerank("q", results)
    assert core.last_fallback is True
    assert hint in core.last_error

    trace2 = TraceContext(trace_type="query")
    Reranker(settings).rerank("q", results, trace=trace2)
    record = trace2.get_stage("rerank")
    assert record["details"]["fallback"] is True
    assert hint in record["details"]["error"]


def test_default_factory_reads_extra_device_and_batch_size(tmp_path, registered, monkeypatch):
    """rerank.extra 的 device / batch_size 透传给默认适配层。"""
    import libs.reranker.cross_encoder_reranker as cer_module

    captured: dict = {}

    class _FakeScorer:
        def __init__(self, model_name, *, device=None, batch_size=32):
            captured.update(model_name=model_name, device=device, batch_size=batch_size)

        def ensure_loaded(self):
            return self

        def __call__(self, query, texts):
            return [float(len(t)) for t in texts]

    monkeypatch.setattr(cer_module, "CrossEncoderScorer", _FakeScorer)
    settings = _settings(
        tmp_path,
        backend="cross_encoder",
        model="cross-encoder/test-model",
        extra={"device": "cpu", "batch_size": 5},
    )

    backend = registered.create(settings)
    backend.rerank("q", [RerankCandidate(id="a", text="aaa")])

    assert captured == {
        "model_name": "cross-encoder/test-model",
        "device": "cpu",
        "batch_size": 5,
    }


def test_default_factory_invalid_batch_size_falls_back(tmp_path, registered, monkeypatch):
    """extra.batch_size 非正 int 时回退默认 32。"""
    import libs.reranker.cross_encoder_reranker as cer_module

    captured: dict = {}

    class _FakeScorer:
        def __init__(self, model_name, *, device=None, batch_size=32):
            captured["batch_size"] = batch_size

        def ensure_loaded(self):
            return self

        def __call__(self, query, texts):
            return [0.0] * len(texts)

    monkeypatch.setattr(cer_module, "CrossEncoderScorer", _FakeScorer)
    settings = _settings(tmp_path, backend="cross_encoder", extra={"batch_size": 0})

    registered.create(settings).rerank("q", [RerankCandidate(id="a", text="a")])
    assert captured["batch_size"] == 32


def test_inference_failure_after_load_walks_channel2(tmp_path, registered):
    """模型加载成功后的推理期异常走 B7.8 内部回退（通道②），不冒出 backend。"""
    from core.query_engine.reranker import Reranker
    from core.types import RetrievalResult

    settings = _settings(tmp_path, backend="cross_encoder", model="cross-encoder/test-model")
    backend = registered.create(settings, scorer_factory=lambda: _raising_scorer(RuntimeError("predict exploded")))

    candidates = [RerankCandidate(id="a", text="a"), RerankCandidate(id="b", text="b")]
    out = backend.rerank("q", candidates)

    # B7.8 内部回退：截断后原序 + last_fallback=True（不抛错）
    assert [r.id for r in out] == ["a", "b"]
    assert out[0].score == 0.0  # 原分保留（未更新）
    assert backend.last_fallback is True

    # Core 层透传通道②标记：注入同一 backend，last_error 为内部回退说明而非异常原文
    core = Reranker(settings, backend=backend)
    core.rerank("q", [RetrievalResult(chunk_id="c1", score=0.5, text="t", metadata={})])
    assert core.last_fallback is True
    assert core.last_error == "CrossEncoderReranker internal fallback"
