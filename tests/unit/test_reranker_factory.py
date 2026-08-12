"""``RerankerFactory`` 路由逻辑与 ``NoneReranker`` 行为测试（阶段 B5）。

验证：
- ``backend=none`` 时返回 ``NoneReranker`` 且不改变排序。
- 未知 backend 明确报错。
- 工厂按 ``settings.rerank.backend`` 路由。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.reranker.base_reranker import BaseReranker, NoneReranker, RerankCandidate
from libs.reranker.reranker_factory import RerankerBackendError, RerankerFactory

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


def _settings(tmp_path: Path, backend: str | None = None):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if backend is not None:
        settings = replace(settings, rerank=replace(settings.rerank, backend=backend))
    return settings


class _FakeReranker(BaseReranker):
    def __init__(self, rerank_config, marker="fake", **kwargs):
        self.rerank_config = rerank_config
        self.marker = marker
        self.kwargs = kwargs

    def rerank(self, query, candidates, *, trace=None, **kwargs):
        # 反转顺序，便于断言"确实重排了"。
        return list(reversed(candidates))


def _make_fake(marker="fake"):
    def ctor(rerank_config, **kwargs):
        return _FakeReranker(rerank_config, marker=marker, **kwargs)
    return ctor


@pytest.fixture
def clean_registry():
    saved = dict(RerankerFactory._registry)
    # 保留默认 none 注册，清理其它。
    RerankerFactory._registry = {"none": saved["none"]}
    yield RerankerFactory
    RerankerFactory._registry = saved


# ---------------------------------------------------------------------------
# NoneReranker 行为
# ---------------------------------------------------------------------------


def test_none_backend_returns_none_reranker(tmp_path, clean_registry):
    settings = _settings(tmp_path, backend="none")
    rr = clean_registry.create(settings)
    assert isinstance(rr, NoneReranker)


def test_none_reranker_preserves_order(tmp_path):
    rr = NoneReranker()
    candidates = [
        RerankCandidate(id="c1", text="a", score=0.9),
        RerankCandidate(id="c2", text="b", score=0.5),
        RerankCandidate(id="c3", text="c", score=0.3),
    ]
    out = rr.rerank("query", candidates)
    assert [c.id for c in out] == ["c1", "c2", "c3"]
    # score 不变
    assert [c.score for c in out] == [0.9, 0.5, 0.3]


def test_none_reranker_returns_new_list(tmp_path):
    rr = NoneReranker()
    candidates = [RerankCandidate(id="c1"), RerankCandidate(id="c2")]
    out = rr.rerank("q", candidates)
    assert out is not candidates
    assert len(out) == 2


def test_none_reranker_empty_candidates(tmp_path):
    rr = NoneReranker()
    assert rr.rerank("q", []) == []


# ---------------------------------------------------------------------------
# 工厂路由
# ---------------------------------------------------------------------------


def test_route_to_fake_backend(tmp_path, clean_registry):
    clean_registry.register("cross_encoder")(_make_fake("ce"))
    settings = _settings(tmp_path, backend="cross_encoder")
    rr = clean_registry.create(settings)
    assert isinstance(rr, _FakeReranker)
    assert rr.marker == "ce"


def test_unknown_backend_raises(tmp_path, clean_registry):
    settings = _settings(tmp_path, backend="nonexistent")
    with pytest.raises(RerankerBackendError) as exc_info:
        clean_registry.create(settings)
    assert "nonexistent" in str(exc_info.value)


def test_unknown_backend_error_lists_registered(tmp_path, clean_registry):
    settings = _settings(tmp_path, backend="bad")
    with pytest.raises(RerankerBackendError) as exc_info:
        clean_registry.create(settings)
    msg = str(exc_info.value)
    assert "bad" in msg
    assert "none" in msg  # 默认注册的 none 一定在列表里


def test_kwargs_passthrough_to_ctor(tmp_path, clean_registry):
    clean_registry.register("cross_encoder")(_make_fake())
    settings = _settings(tmp_path, backend="cross_encoder")
    rr = clean_registry.create(settings, extra_flag=True)
    assert rr.kwargs["extra_flag"] is True


def test_none_always_registered(clean_registry):
    assert clean_registry.is_registered("none")
    assert "none" in clean_registry.available_backends()


# ---------------------------------------------------------------------------
# 契约：rerank 确实改变顺序（fake），none 不改变
# ---------------------------------------------------------------------------


def test_fake_reranker_changes_order(tmp_path, clean_registry):
    clean_registry.register("cross_encoder")(_make_fake())
    settings = _settings(tmp_path, backend="cross_encoder")
    rr = clean_registry.create(settings)
    cands = [RerankCandidate(id="c1"), RerankCandidate(id="c2"), RerankCandidate(id="c3")]
    out = rr.rerank("q", cands)
    assert [c.id for c in out] == ["c3", "c2", "c1"]
