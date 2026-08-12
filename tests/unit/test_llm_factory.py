"""``LLMFactory`` 路由逻辑测试（阶段 B1）。

用测试内 stub 的 Fake provider 验证工厂按 ``settings.llm.provider`` 路由，
不依赖真实网络/SDK。
"""

from __future__ import annotations

from dataclasses import replace
from textwrap import dedent
from pathlib import Path

import pytest

from core.settings import load_settings
from libs.llm.base_llm import BaseLLM, ChatMessage, ChatResponse
from libs.llm.llm_factory import LLMFactory, LLMProviderError

# 供测试的最小合法配置（provider=azure）。
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


def _settings(tmp_path: Path, provider: str | None = None) -> "Settings":
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if provider is not None:
        settings = replace(settings, llm=replace(settings.llm, provider=provider))
    return settings


# ---------------------------------------------------------------------------
# Fake providers（测试内 stub）
# ---------------------------------------------------------------------------


class _FakeLLM(BaseLLM):
    def __init__(self, llm_config, marker: str = "fake", **kwargs):
        self.llm_config = llm_config
        self.marker = marker
        self.kwargs = kwargs

    def chat(self, messages, *, trace=None, **kwargs):
        return ChatResponse(content=f"[{self.marker}] {self.llm_config.model}", model=self.llm_config.model)


def _make_fake(marker: str):
    def ctor(llm_config, **kwargs):
        return _FakeLLM(llm_config, marker=marker, **kwargs)
    return ctor


@pytest.fixture
def clean_registry():
    """每个用例前后清理 ``LLMFactory`` 注册表，避免跨用例污染。"""
    saved = dict(LLMFactory._registry)
    LLMFactory._registry.clear()
    yield LLMFactory
    LLMFactory._registry.clear()
    LLMFactory._registry.update(saved)


# ---------------------------------------------------------------------------
# 路由测试
# ---------------------------------------------------------------------------


def test_register_and_route_to_fake(tmp_path, clean_registry):
    clean_registry.register("azure")(_make_fake("azure"))
    settings = _settings(tmp_path, provider="azure")
    llm = clean_registry.create(settings)
    assert isinstance(llm, _FakeLLM)
    assert llm.marker == "azure"
    resp = llm.chat([ChatMessage("user", "hi")])
    assert resp.content == "[azure] gpt-4o"
    assert resp.model == "gpt-4o"


def test_route_to_different_provider(tmp_path, clean_registry):
    clean_registry.register("ollama")(_make_fake("ollama"))
    settings = _settings(tmp_path, provider="ollama")
    llm = clean_registry.create(settings)
    assert llm.marker == "ollama"


def test_unknown_provider_raises(tmp_path, clean_registry):
    settings = _settings(tmp_path, provider="nonexistent")
    with pytest.raises(LLMProviderError) as exc_info:
        clean_registry.create(settings)
    assert "nonexistent" in str(exc_info.value)


def test_unknown_provider_error_lists_registered(tmp_path, clean_registry):
    clean_registry.register("azure")(_make_fake("azure"))
    settings = _settings(tmp_path, provider="bad")
    with pytest.raises(LLMProviderError) as exc_info:
        clean_registry.create(settings)
    msg = str(exc_info.value)
    assert "bad" in msg
    assert "azure" in msg  # 错误信息列出已注册 provider


def test_kwargs_passthrough_to_ctor(tmp_path, clean_registry):
    clean_registry.register("azure")(_make_fake("azure"))
    settings = _settings(tmp_path, provider="azure")
    llm = clean_registry.create(settings, extra_flag=True)
    assert llm.kwargs["extra_flag"] is True


def test_is_registered_and_available_providers(clean_registry):
    assert not clean_registry.is_registered("azure")
    clean_registry.register("azure")(_make_fake("azure"))
    assert clean_registry.is_registered("azure")
    assert "azure" in clean_registry.available_providers()


def test_chat_text_helper(tmp_path, clean_registry):
    clean_registry.register("azure")(_make_fake("azure"))
    settings = _settings(tmp_path, provider="azure")
    llm = clean_registry.create(settings)
    text = llm.chat_text("hello", system="be concise")
    assert text.startswith("[azure]")


def test_chat_message_to_dict():
    msg = ChatMessage("system", "you are helpful")
    assert msg.to_dict() == {"role": "system", "content": "you are helpful"}
