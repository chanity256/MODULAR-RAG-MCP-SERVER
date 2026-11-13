"""Vision LLM 抽象接口与工厂集成测试（阶段 B8）。

验证：
- ``BaseVisionLLM`` 抽象接口清晰定义多模态输入（文本+图片路径/base64）。
- 工厂方法 ``create_vision_llm`` 能根据配置路由到不同 provider。
- 图片辅助方法：load_image（文件路径/bytes）、encode_image_base64、prepare_image。
- 图片预处理扩展点（_preprocess_image 默认 no-op，子类可覆盖）。
- 未知 provider 抛出可读错误。
- ``chat_with_image`` 返回 ``ChatResponse``。
"""

from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.llm.base_llm import ChatMessage, ChatResponse
from libs.llm.base_vision_llm import BaseVisionLLM
from libs.llm.llm_factory import LLMFactory, LLMProviderError

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


def _settings(tmp_path: Path, **vision_overrides):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if vision_overrides:
        settings = replace(settings, vision_llm=replace(settings.vision_llm, **vision_overrides))
    return settings


# --- Fake Vision LLM --------------------------------------------------------


class _FakeVisionLLM(BaseVisionLLM):
    """Fake Vision LLM for testing."""

    provider = "fake-vision"

    def __init__(self, vision_config, **kwargs):
        self.vision_config = vision_config
        self._kwargs = kwargs
        self.last_text: str | None = None
        self.last_image_b64: str | None = None
        self.last_media_type: str | None = None

    def chat(self, messages, *, trace=None, **kwargs):
        return ChatResponse(content="chat response", model="fake-vision")

    def chat_with_image(self, text, image, *, trace=None, **kwargs):
        self.last_text = text
        b64, media_type = self.prepare_image(image, max_size=kwargs.get("max_image_size"))
        self.last_image_b64 = b64
        self.last_media_type = media_type
        return ChatResponse(content=f"caption for: {text}", model=self.vision_config.model)


class _PreprocessingVisionLLM(_FakeVisionLLM):
    """Fake Vision LLM with actual preprocessing override."""

    def _preprocess_image(self, image_bytes, max_size, media_type):
        # 模拟预处理：返回标记字节
        return b"PREPROCESSED:" + image_bytes


@pytest.fixture
def registered_fake():
    """注册 fake-vision provider 并在测试后清理。"""
    @LLMFactory.register_vision("fake-vision")
    def _ctor(vision_config, **kwargs):
        return _FakeVisionLLM(vision_config, **kwargs)

    yield LLMFactory
    # 清理：恢复 none（如果存在），移除 fake-vision
    LLMFactory._vision_registry.pop("fake-vision", None)


# ---------------------------------------------------------------------------
# 工厂路由
# ---------------------------------------------------------------------------


def test_create_vision_llm_routes_to_registered_provider(tmp_path, registered_fake):
    settings = _settings(tmp_path, provider="fake-vision")
    vllm = LLMFactory.create_vision_llm(settings)
    assert isinstance(vllm, _FakeVisionLLM)
    assert isinstance(vllm, BaseVisionLLM)


def test_create_vision_llm_unknown_provider_raises(tmp_path):
    settings = _settings(tmp_path, provider="nonexistent-vision")
    with pytest.raises(LLMProviderError) as exc_info:
        LLMFactory.create_vision_llm(settings)
    assert "nonexistent-vision" in str(exc_info.value)
    assert "registered" in str(exc_info.value)


def test_vision_registry_separate_from_llm_registry(registered_fake):
    """Vision registry 和普通 LLM registry 是独立的。"""
    assert "fake-vision" in LLMFactory.available_vision_providers()
    assert "fake-vision" not in LLMFactory.available_providers()


def test_is_vision_registered(registered_fake):
    assert LLMFactory.is_vision_registered("fake-vision") is True
    assert LLMFactory.is_vision_registered("nonexistent") is False


# ---------------------------------------------------------------------------
# chat_with_image 接口
# ---------------------------------------------------------------------------


def test_chat_with_image_returns_chat_response(tmp_path, registered_fake):
    settings = _settings(tmp_path, provider="fake-vision")
    vllm = LLMFactory.create_vision_llm(settings)
    # 用 bytes 作为图片输入
    resp = vllm.chat_with_image("describe this", b"\x89PNG fake image data")
    assert isinstance(resp, ChatResponse)
    assert "describe this" in resp.content


def test_chat_with_image_with_file_path(tmp_path, registered_fake):
    settings = _settings(tmp_path, provider="fake-vision")
    vllm = LLMFactory.create_vision_llm(settings)
    # 创建临时图片文件
    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG fake image data")
    resp = vllm.chat_with_image("describe this", str(img_file))
    assert isinstance(resp, ChatResponse)
    assert vllm.last_text == "describe this"


def test_chat_with_image_with_path_object(tmp_path, registered_fake):
    settings = _settings(tmp_path, provider="fake-vision")
    vllm = LLMFactory.create_vision_llm(settings)
    img_file = tmp_path / "test.jpg"
    img_file.write_bytes(b"\xff\xd8 fake jpeg")
    resp = vllm.chat_with_image("caption", img_file)
    assert isinstance(resp, ChatResponse)


def test_chat_with_image_nonexistent_file_raises(tmp_path, registered_fake):
    settings = _settings(tmp_path, provider="fake-vision")
    vllm = LLMFactory.create_vision_llm(settings)
    with pytest.raises(FileNotFoundError):
        vllm.chat_with_image("describe", "/nonexistent/image.png")


# ---------------------------------------------------------------------------
# 图片辅助方法
# ---------------------------------------------------------------------------


def test_load_image_from_bytes():
    raw, media_type = BaseVisionLLM.load_image(b"\x89PNG data")
    assert raw == b"\x89PNG data"
    assert media_type == "image/png"  # default for bytes


def test_load_image_from_file(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8 jpeg data")
    raw, media_type = BaseVisionLLM.load_image(str(img))
    assert raw == b"\xff\xd8 jpeg data"
    assert media_type == "image/jpeg"


def test_load_image_from_path_object(tmp_path):
    img = tmp_path / "pic.webp"
    img.write_bytes(b"webp data")
    raw, media_type = BaseVisionLLM.load_image(img)
    assert raw == b"webp data"
    assert media_type == "image/webp"


def test_load_image_unknown_extension_defaults_png(tmp_path):
    img = tmp_path / "pic.xyz"
    img.write_bytes(b"data")
    _, media_type = BaseVisionLLM.load_image(img)
    assert media_type == "image/png"


def test_encode_image_base64():
    b64 = BaseVisionLLM.encode_image_base64(b"hello")
    assert b64 == base64.b64encode(b"hello").decode("ascii")
    # 可解码还原
    assert base64.b64decode(b64) == b"hello"


def test_prepare_image_returns_base64_and_media_type(tmp_path, registered_fake):
    settings = _settings(tmp_path, provider="fake-vision")
    vllm = LLMFactory.create_vision_llm(settings)
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG data")
    b64, media_type = vllm.prepare_image(str(img))
    assert base64.b64decode(b64) == b"\x89PNG data"
    assert media_type == "image/png"


# ---------------------------------------------------------------------------
# 图片预处理扩展点
# ---------------------------------------------------------------------------


def test_preprocess_image_default_is_noop():
    """默认 _preprocess_image 不修改字节。"""
    vllm = _FakeVisionLLM.__new__(_FakeVisionLLM)
    result = BaseVisionLLM._preprocess_image(vllm, b"original", 1024, "image/png")
    assert result == b"original"


def test_preprocess_image_override_called(tmp_path):
    """子类覆盖 _preprocess_image 后，prepare_image 使用处理后的字节。"""
    from core.settings import VisionLLMConfig

    cfg = VisionLLMConfig(provider="azure", model="gpt-4o")
    vllm = _PreprocessingVisionLLM(cfg)
    b64, media_type = vllm.prepare_image(b"raw image", max_size=512)
    decoded = base64.b64decode(b64)
    assert decoded == b"PREPROCESSED:raw image"


def test_prepare_image_without_max_size_skips_preprocessing(tmp_path, registered_fake):
    settings = _settings(tmp_path, provider="fake-vision")
    vllm = LLMFactory.create_vision_llm(settings)
    b64, _ = vllm.prepare_image(b"raw bytes")  # no max_size
    assert base64.b64decode(b64) == b"raw bytes"


# ---------------------------------------------------------------------------
# BaseVisionLLM 继承 BaseLLM
# ---------------------------------------------------------------------------


def test_vision_llm_is_base_llm(registered_fake):
    """BaseVisionLLM 继承 BaseLLM，可调用 chat/chat_text。"""
    from core.settings import VisionLLMConfig

    cfg = VisionLLMConfig(provider="fake-vision", model="gpt-4o")
    vllm = _FakeVisionLLM(cfg)
    # chat_text 继承自 BaseLLM
    text = vllm.chat_text("hello")
    assert text == "chat response"


# ---------------------------------------------------------------------------
# 工厂 kwargs 透传
# ---------------------------------------------------------------------------


def test_factory_passes_kwargs_to_vision_constructor(tmp_path, registered_fake):
    settings = _settings(tmp_path, provider="kwarg-test")

    @LLMFactory.register_vision("kwarg-test")
    def _ctor(vision_config, **kwargs):
        llm = _FakeVisionLLM(vision_config, **kwargs)
        llm._injected_kwarg = kwargs.get("custom_param")
        return llm

    vllm = LLMFactory.create_vision_llm(settings, custom_param="injected!")
    assert vllm._injected_kwarg == "injected!"
    LLMFactory._vision_registry.pop("kwarg-test", None)
