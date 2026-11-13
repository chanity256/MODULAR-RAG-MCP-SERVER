"""``EmbeddingFactory`` 路由逻辑测试（阶段 B2）。

用测试内 stub 的 Fake embedding 验证工厂按 ``settings.embedding.provider`` 路由，
并断言 Fake embedding 返回稳定向量。不依赖真实网络/SDK。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.embedding.base_embedding import BaseEmbedding
from libs.embedding.embedding_factory import EmbeddingFactory, EmbeddingProviderError

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


def _settings(tmp_path: Path, provider: str | None = None):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if provider is not None:
        settings = replace(settings, embedding=replace(settings.embedding, provider=provider))
    return settings


# ---------------------------------------------------------------------------
# Fake embedding（测试内 stub，返回稳定向量）
# ---------------------------------------------------------------------------


class _FakeEmbedding(BaseEmbedding):
    def __init__(self, emb_config, dim: int = 4, **kwargs):
        self.emb_config = emb_config
        self._dim = dim
        self.kwargs = kwargs

    @property
    def dimension(self) -> int | None:
        return self._dim

    def embed(self, texts, *, trace=None, **kwargs):
        # 稳定向量：对每个文本生成基于哈希的确定性向量。
        out = []
        for t in texts:
            if not isinstance(t, str):
                raise ValueError("embedding input must be str")
            h = hash(t)
            out.append([((h >> i) & 0xF) / 15.0 for i in range(self._dim)])
        return out


def _make_fake(dim: int = 4):
    def ctor(emb_config, **kwargs):
        return _FakeEmbedding(emb_config, dim=dim, **kwargs)
    return ctor


@pytest.fixture
def clean_registry():
    saved = dict(EmbeddingFactory._registry)
    EmbeddingFactory._registry.clear()
    yield EmbeddingFactory
    EmbeddingFactory._registry.clear()
    EmbeddingFactory._registry.update(saved)


# ---------------------------------------------------------------------------
# 路由测试
# ---------------------------------------------------------------------------


def test_register_and_route_to_fake(tmp_path, clean_registry):
    clean_registry.register("openai")(_make_fake(dim=4))
    settings = _settings(tmp_path, provider="openai")
    emb = clean_registry.create(settings)
    assert isinstance(emb, _FakeEmbedding)
    assert emb.dimension == 4


def test_route_to_different_provider(tmp_path, clean_registry):
    clean_registry.register("ollama")(_make_fake(dim=8))
    settings = _settings(tmp_path, provider="ollama")
    emb = clean_registry.create(settings)
    assert emb.dimension == 8


def test_unknown_provider_raises(tmp_path, clean_registry):
    settings = _settings(tmp_path, provider="nonexistent")
    with pytest.raises(EmbeddingProviderError) as exc_info:
        clean_registry.create(settings)
    assert "nonexistent" in str(exc_info.value)


def test_unknown_provider_error_lists_registered(tmp_path, clean_registry):
    clean_registry.register("openai")(_make_fake())
    settings = _settings(tmp_path, provider="bad")
    with pytest.raises(EmbeddingProviderError) as exc_info:
        clean_registry.create(settings)
    msg = str(exc_info.value)
    assert "bad" in msg
    assert "openai" in msg


def test_kwargs_passthrough_to_ctor(tmp_path, clean_registry):
    clean_registry.register("openai")(_make_fake())
    settings = _settings(tmp_path, provider="openai")
    emb = clean_registry.create(settings, extra_flag=True)
    assert emb.kwargs["extra_flag"] is True


def test_is_registered_and_available_providers(clean_registry):
    assert not clean_registry.is_registered("openai")
    clean_registry.register("openai")(_make_fake())
    assert clean_registry.is_registered("openai")
    assert "openai" in clean_registry.available_providers()


# ---------------------------------------------------------------------------
# 稳定向量契约
# ---------------------------------------------------------------------------


def test_fake_embedding_returns_stable_vectors(tmp_path, clean_registry):
    clean_registry.register("openai")(_make_fake(dim=4))
    settings = _settings(tmp_path, provider="openai")
    emb = clean_registry.create(settings)
    v1 = emb.embed(["hello", "world"])
    v2 = emb.embed(["hello", "world"])
    assert v1 == v2  # 确定性
    assert len(v1) == 2
    assert all(len(vec) == 4 for vec in v1)


def test_embed_one_helper(tmp_path, clean_registry):
    clean_registry.register("openai")(_make_fake(dim=3))
    settings = _settings(tmp_path, provider="openai")
    emb = clean_registry.create(settings)
    vec = emb.embed_one("hello")
    assert isinstance(vec, list)
    assert len(vec) == 3


def test_embed_one_rejects_empty(tmp_path, clean_registry):
    clean_registry.register("openai")(_make_fake())
    settings = _settings(tmp_path, provider="openai")
    emb = clean_registry.create(settings)
    with pytest.raises(ValueError):
        emb.embed_one("")


def test_embed_rejects_non_string(tmp_path, clean_registry):
    clean_registry.register("openai")(_make_fake())
    settings = _settings(tmp_path, provider="openai")
    emb = clean_registry.create(settings)
    with pytest.raises(ValueError):
        emb.embed(["ok", 123])  # type: ignore[list-item]
