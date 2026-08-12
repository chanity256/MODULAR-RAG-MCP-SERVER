"""Azure Vision LLM 单元测试（阶段 B9，mock HTTP）。

通过注入 fake transport 验证：
- provider=azure 时 LLMFactory.create_vision_llm() 可创建 AzureVisionLLM。
- chat_with_image 支持图片路径和 bytes 两种输入。
- 请求 URL / headers / body 符合 Azure Vision 规范。
- 图片预处理扩展点（_preprocess_image 可覆盖，默认 no-op）。
- API 调用失败抛出清晰错误（含 Azure 错误码）。
- mock 覆盖：正常调用、认证失败、超时、空文本。
- chat（文本）方法也可正常工作。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.llm.azure_vision_llm import AzureVisionLLM
from libs.llm.base_vision_llm import BaseVisionLLM
from libs.llm.llm_factory import LLMFactory
from libs.llm.openai_llm import LLMRequestError

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
      azure_endpoint: https://example.openai.azure.com
      api_key: az-key
      api_version: 2024-10-01-preview
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


def _ok_response(content: str = "A photo of a cat", model: str = "gpt-4o") -> dict:
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class _FakeTransport:
    def __init__(self, response: dict | None = None, raise_exc: Exception | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.last_url = None
        self.last_headers = None
        self.last_body = None

    def __call__(self, url, headers, body, *, timeout=None):
        self.last_url = url
        self.last_headers = headers
        self.last_body = body
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


@pytest.fixture
def registered():
    import libs.llm  # noqa: F401
    yield LLMFactory


# ---------------------------------------------------------------------------
# 工厂创建
# ---------------------------------------------------------------------------


def test_factory_creates_azure_vision(tmp_path, registered):
    settings = _settings(tmp_path, provider="azure")
    vllm = registered.create_vision_llm(settings, transport=_FakeTransport(_ok_response()))
    assert isinstance(vllm, AzureVisionLLM)
    assert isinstance(vllm, BaseVisionLLM)


def test_azure_vision_registered(registered):
    assert "azure" in LLMFactory.available_vision_providers()


def test_factory_unknown_vision_provider_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="nonexistent")
    with pytest.raises(Exception, match="nonexistent"):
        registered.create_vision_llm(settings)


# ---------------------------------------------------------------------------
# chat_with_image — 请求构建
# ---------------------------------------------------------------------------


def test_chat_with_image_bytes_input(tmp_path, registered):
    transport = _FakeTransport(_ok_response("a cat on a mat"))
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)
    resp = vllm.chat_with_image("describe this image", b"\x89PNG fake image data")
    assert resp.content == "a cat on a mat"
    # 验证 URL
    assert "example.openai.azure.com" in transport.last_url
    assert "/openai/deployments/gpt-4o/chat/completions" in transport.last_url
    assert "api-version=2024-10-01-preview" in transport.last_url
    # 验证 headers
    assert transport.last_headers["api-key"] == "az-key"
    # 验证 body 结构（OpenAI Vision 消息格式）
    msg = transport.last_body["messages"][0]
    assert msg["role"] == "user"
    content_parts = msg["content"]
    assert any(p["type"] == "text" and p["text"] == "describe this image" for p in content_parts)
    image_part = [p for p in content_parts if p["type"] == "image_url"][0]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_chat_with_image_file_path(tmp_path, registered):
    transport = _FakeTransport(_ok_response("a landscape"))
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)
    img_file = tmp_path / "photo.jpg"
    img_file.write_bytes(b"\xff\xd8 fake jpeg data")
    resp = vllm.chat_with_image("what is this?", str(img_file))
    assert resp.content == "a landscape"
    # 验证 image_url 使用 jpeg media type
    msg = transport.last_body["messages"][0]
    image_part = [p for p in msg["content"] if p["type"] == "image_url"][0]
    assert "image/jpeg" in image_part["image_url"]["url"]


def test_chat_with_image_path_object(tmp_path, registered):
    transport = _FakeTransport(_ok_response("a diagram"))
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)
    img_file = tmp_path / "diagram.png"
    img_file.write_bytes(b"\x89PNG data")
    resp = vllm.chat_with_image("explain", img_file)
    assert resp.content == "a diagram"


def test_chat_with_image_nonexistent_file_raises(tmp_path, registered):
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(FileNotFoundError):
        vllm.chat_with_image("describe", "/nonexistent/img.png")


def test_chat_with_image_empty_text_raises(tmp_path, registered):
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(LLMRequestError, match="non-empty"):
        vllm.chat_with_image("", b"\x89PNG data")


# ---------------------------------------------------------------------------
# Azure 特有配置
# ---------------------------------------------------------------------------


def test_missing_azure_endpoint_raises(tmp_path, registered):
    settings = _settings(tmp_path, azure_endpoint=None)
    vllm = registered.create_vision_llm(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(LLMRequestError, match="azure_endpoint"):
        vllm.chat_with_image("describe", b"\x89PNG data")


def test_deployment_name_from_config(tmp_path, registered):
    transport = _FakeTransport(_ok_response("ok"))
    settings = _settings(tmp_path, deployment_name="gpt-4o-vision-deploy")
    vllm = registered.create_vision_llm(settings, transport=transport)
    vllm.chat_with_image("describe", b"\x89PNG data")
    assert "/openai/deployments/gpt-4o-vision-deploy/chat/completions" in transport.last_url


def test_deployment_name_falls_back_to_model(tmp_path, registered):
    transport = _FakeTransport(_ok_response("ok"))
    settings = _settings(tmp_path, deployment_name=None, model="gpt-4o")
    vllm = registered.create_vision_llm(settings, transport=transport)
    vllm.chat_with_image("describe", b"\x89PNG data")
    assert "/openai/deployments/gpt-4o/chat/completions" in transport.last_url


def test_custom_api_version(tmp_path, registered):
    transport = _FakeTransport(_ok_response("ok"))
    settings = _settings(tmp_path, api_version="2024-08-01-preview")
    vllm = registered.create_vision_llm(settings, transport=transport)
    vllm.chat_with_image("describe", b"\x89PNG data")
    assert "api-version=2024-08-01-preview" in transport.last_url


# ---------------------------------------------------------------------------
# 图片预处理
# ---------------------------------------------------------------------------


def test_preprocess_image_default_noop(tmp_path, registered):
    """Pillow 不可用时，_preprocess_image 不修改字节。"""
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=_FakeTransport(_ok_response()))
    result = vllm._preprocess_image(b"original bytes", 2048, "image/png")
    assert result == b"original bytes"


def test_preprocess_image_override_called(tmp_path, registered):
    """子类覆盖 _preprocess_image 后，chat_with_image 使用处理后的图片。"""
    transport = _FakeTransport(_ok_response("ok"))
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)

    # Monkey-patch _preprocess_image
    original_preprocess = vllm._preprocess_image

    def mock_preprocess(image_bytes, max_size, media_type):
        return b"COMPRESSED:" + image_bytes

    vllm._preprocess_image = mock_preprocess
    vllm.chat_with_image("describe", b"raw image", max_image_size=1024)

    # 验证 body 中 base64 解码后包含 COMPRESSED 前缀
    import base64

    msg = transport.last_body["messages"][0]
    image_part = [p for p in msg["content"] if p["type"] == "image_url"][0]
    data_url = image_part["image_url"]["url"]
    # 去掉 data:image/png;base64, 前缀
    b64_part = data_url.split(",", 1)[1]
    decoded = base64.b64decode(b64_part)
    assert decoded == b"COMPRESSED:raw image"


def test_max_image_size_from_config(tmp_path, registered):
    """max_image_size 从 VisionLLMConfig 读取。"""
    settings = _settings(tmp_path, max_image_size=1024)
    vllm = registered.create_vision_llm(settings, transport=_FakeTransport(_ok_response()))

    called_args: dict = {}

    def spy_preprocess(image_bytes, max_size, media_type):
        called_args["max_size"] = max_size
        return image_bytes

    vllm._preprocess_image = spy_preprocess
    vllm.chat_with_image("describe", b"image data")
    assert called_args["max_size"] == 1024


# ---------------------------------------------------------------------------
# 错误场景
# ---------------------------------------------------------------------------


def test_auth_failure_raises_readable_error(tmp_path, registered):
    err_resp = {"error": {"message": "Access denied", "code": "401"}}
    transport = _FakeTransport(err_resp)
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        vllm.chat_with_image("describe", b"\x89PNG data")
    assert "azure" in str(exc_info.value) or "API error" in str(exc_info.value)
    assert "Access denied" in str(exc_info.value)
    assert "401" in str(exc_info.value)


def test_network_error_raises_readable(tmp_path, registered):
    from libs.llm.openai_llm import LLMRequestError as _LE

    transport = _FakeTransport(raise_exc=_LE("azure-vision", "network error: Connection refused"))
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        vllm.chat_with_image("describe", b"\x89PNG data")
    assert "Connection refused" in str(exc_info.value)


def test_malformed_response_raises(tmp_path, registered):
    transport = _FakeTransport({"foo": "bar"})
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        vllm.chat_with_image("describe", b"\x89PNG data")
    assert "choices" in str(exc_info.value)


def test_error_does_not_leak_api_key(tmp_path, registered):
    from libs.llm.openai_llm import LLMRequestError as _LE

    transport = _FakeTransport(raise_exc=_LE("azure-vision", "network error: boom"))
    settings = _settings(tmp_path, api_key="super-secret-vision-key")
    vllm = registered.create_vision_llm(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        vllm.chat_with_image("describe", b"\x89PNG data")
    assert "super-secret-vision-key" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 文本 chat 方法（继承自 BaseLLM）
# ---------------------------------------------------------------------------


def test_chat_text_only_works(tmp_path, registered):
    transport = _FakeTransport(_ok_response("text reply"))
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)
    resp = vllm.chat_text("hello")
    assert resp == "text reply"
    # 验证 body 是标准文本消息
    msg = transport.last_body["messages"][0]
    assert msg["role"] == "user"
    assert msg["content"] == "hello"


def test_chat_returns_chat_response(tmp_path, registered):
    from libs.llm.base_llm import ChatMessage

    transport = _FakeTransport(_ok_response("response"))
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)
    resp = vllm.chat([ChatMessage("user", "hi")])
    assert resp.content == "response"
    assert resp.model == "gpt-4o"


# ---------------------------------------------------------------------------
# 响应解析
# ---------------------------------------------------------------------------


def test_response_includes_usage(tmp_path, registered):
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=transport)
    resp = vllm.chat_with_image("describe", b"\x89PNG data")
    assert resp.usage is not None
    assert resp.usage["total_tokens"] == 15
