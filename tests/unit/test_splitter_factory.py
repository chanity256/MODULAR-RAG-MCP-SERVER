"""``SplitterFactory`` 路由逻辑测试（阶段 B3）。

用测试内 stub 的 Fake splitter 验证工厂按 ``settings.splitter.provider`` 路由，
并断言不同策略返回不同实例。不依赖真实 LangChain。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.splitter.base_splitter import BaseSplitter
from libs.splitter.splitter_factory import SplitterFactory, SplitterProviderError

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


def _settings(tmp_path: Path, provider: str | None = None, **overrides):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    sp = replace(settings.splitter, provider=provider) if provider else settings.splitter
    if overrides:
        sp = replace(sp, **overrides)
    return replace(settings, splitter=sp)


# ---------------------------------------------------------------------------
# Fake splitters（测试内 stub）
# ---------------------------------------------------------------------------


class _FakeSplitter(BaseSplitter):
    def __init__(self, splitter_config, marker: str = "fake", **kwargs):
        self.splitter_config = splitter_config
        self.marker = marker
        self.kwargs = kwargs

    def split_text(self, text, *, trace=None, **kwargs):
        if not isinstance(text, str):
            raise ValueError("split_text requires a string")
        # 简单按 chunk_size 切分，便于验证配置驱动。
        size = self.splitter_config.chunk_size or len(text)
        return [text[i:i + size] for i in range(0, len(text), size)]


def _make_fake(marker: str):
    def ctor(splitter_config, **kwargs):
        return _FakeSplitter(splitter_config, marker=marker, **kwargs)
    return ctor


@pytest.fixture
def clean_registry():
    saved = dict(SplitterFactory._registry)
    SplitterFactory._registry.clear()
    yield SplitterFactory
    SplitterFactory._registry.clear()
    SplitterFactory._registry.update(saved)


# ---------------------------------------------------------------------------
# 路由测试
# ---------------------------------------------------------------------------


def test_register_and_route_to_fake(tmp_path, clean_registry):
    clean_registry.register("recursive")(_make_fake("recursive"))
    settings = _settings(tmp_path, provider="recursive")
    sp = clean_registry.create(settings)
    assert isinstance(sp, _FakeSplitter)
    assert sp.marker == "recursive"


def test_route_to_different_provider(tmp_path, clean_registry):
    clean_registry.register("semantic")(_make_fake("semantic"))
    settings = _settings(tmp_path, provider="semantic")
    sp = clean_registry.create(settings)
    assert sp.marker == "semantic"


def test_unknown_provider_raises(tmp_path, clean_registry):
    settings = _settings(tmp_path, provider="nonexistent")
    with pytest.raises(SplitterProviderError) as exc_info:
        clean_registry.create(settings)
    assert "nonexistent" in str(exc_info.value)


def test_unknown_provider_error_lists_registered(tmp_path, clean_registry):
    clean_registry.register("recursive")(_make_fake("recursive"))
    settings = _settings(tmp_path, provider="bad")
    with pytest.raises(SplitterProviderError) as exc_info:
        clean_registry.create(settings)
    msg = str(exc_info.value)
    assert "bad" in msg
    assert "recursive" in msg


def test_kwargs_passthrough_to_ctor(tmp_path, clean_registry):
    clean_registry.register("recursive")(_make_fake("recursive"))
    settings = _settings(tmp_path, provider="recursive")
    sp = clean_registry.create(settings, extra_flag=True)
    assert sp.kwargs["extra_flag"] is True


def test_is_registered_and_available_providers(clean_registry):
    assert not clean_registry.is_registered("recursive")
    clean_registry.register("recursive")(_make_fake("recursive"))
    assert clean_registry.is_registered("recursive")
    assert "recursive" in clean_registry.available_providers()


# ---------------------------------------------------------------------------
# 配置驱动 + 契约
# ---------------------------------------------------------------------------


def test_config_driven_chunk_size(tmp_path, clean_registry):
    clean_registry.register("recursive")(_make_fake("recursive"))
    settings = _settings(tmp_path, provider="recursive", chunk_size=5, chunk_overlap=0)
    sp = clean_registry.create(settings)
    chunks = sp.split_text("abcdefghij")
    assert chunks == ["abcde", "fghij"]


def test_split_text_rejects_non_string(tmp_path, clean_registry):
    clean_registry.register("recursive")(_make_fake("recursive"))
    settings = _settings(tmp_path, provider="recursive")
    sp = clean_registry.create(settings)
    with pytest.raises(ValueError):
        sp.split_text(123)  # type: ignore[arg-type]


def test_default_splitter_provider_when_unspecified(tmp_path, clean_registry):
    """settings.yaml 未写 splitter 节时，默认 provider=recursive。"""
    clean_registry.register("recursive")(_make_fake("recursive"))
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    assert settings.splitter.provider == "recursive"
    assert settings.splitter.chunk_size == 1000
