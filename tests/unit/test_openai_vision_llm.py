"""OpenAI-compatible Vision LLM 单元测试（G14，mock HTTP）。

通过注入 fake transport 验证：
- provider=openai 时 LLMFactory.create_vision_llm() 可创建 OpenAICompatibleVisionLLM。
- 请求 URL / headers / body 符合 OpenAI 兼容规范（区别于 Azure 的
  deployment URL 与 api-key 头）。
- 继承自 AzureVisionLLM 的能力（payload 格式 / 图片预处理 / 响应解析）原样可用。
- 错误场景：缺 base_url、API 错误、网络错误，且不泄露 api_key。
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
from libs.llm.openai_vision_llm import OpenAICompatibleVisionLLM

_VALID_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
    vision_llm:
      provider: openai
      model: glm-5.3-flash
      base_url: https://open.bigmodel.cn/api/paas/v4
      api_key: zai-key
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


def _ok_response(content: str = "红色", model: str = "glm-5.3-flash") -> dict:
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


def test_factory_creates_openai_vision(tmp_path, registered):
    settings = _settings(tmp_path)
    vllm = registered.create_vision_llm(settings, transport=_FakeTransport(_ok_response()))
    assert isinstance(vllm, OpenAICompatibleVisionLLM)
    assert isinstance(vllm, AzureVisionLLM)  # 继承复用，不是平行实现
    assert isinstance(vllm, BaseVisionLLM)


def test_openai_vision_registered(registered):
    assert "openai" in LLMFactory.available_vision_providers()
    assert "azure" in LLMFactory.available_vision_providers()  # 既有注册不受影响


# ---------------------------------------------------------------------------
# 请求构建：URL / headers / body
# ---------------------------------------------------------------------------


def test_url_is_base_url_plus_chat_completions(tmp_path, registered):
    transport = _FakeTransport(_ok_response())
    vllm = registered.create_vision_llm(settings=_settings(tmp_path), transport=transport)
    vllm.chat_with_image("describe", b"\x89PNG data")
    assert transport.last_url == "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def test_url_strips_trailing_slash(tmp_path, registered):
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path, base_url="https://open.bigmodel.cn/api/paas/v4/")
    vllm = registered.create_vision_llm(settings, transport=transport)
    vllm.chat_with_image("describe", b"\x89PNG data")
    assert transport.last_url == "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def test_no_azure_deployment_url_leakage(tmp_path, registered):
    """不得残留 Azure 特有的 deployment / api-version URL 结构。"""
    transport = _FakeTransport(_ok_response())
    vllm = registered.create_vision_llm(settings=_settings(tmp_path), transport=transport)
    vllm.chat_with_image("describe", b"\x89PNG data")
    assert "/openai/deployments/" not in transport.last_url
    assert "api-version" not in transport.last_url


def test_auth_is_bearer_not_api_key_header(tmp_path, registered):
    transport = _FakeTransport(_ok_response())
    vllm = registered.create_vision_llm(settings=_settings(tmp_path), transport=transport)
    vllm.chat_with_image("describe", b"\x89PNG data")
    assert transport.last_headers["Authorization"] == "Bearer zai-key"
    assert "api-key" not in transport.last_headers


def test_missing_api_key_omits_auth_header(tmp_path, registered):
    """本地网关等匿名场景：api_key 缺省时不带认证头，不报错。"""
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path, api_key=None)
    vllm = registered.create_vision_llm(settings, transport=transport)
    vllm.chat_with_image("describe", b"\x89PNG data")
    assert "Authorization" not in transport.last_headers


def test_vision_payload_format_inherited(tmp_path, registered):
    """请求体与 Azure 实现一致：OpenAI Vision 消息格式 + model 字段。"""
    transport = _FakeTransport(_ok_response())
    vllm = registered.create_vision_llm(settings=_settings(tmp_path), transport=transport)
    vllm.chat_with_image("describe this image", b"\x89PNG data")
    assert transport.last_body["model"] == "glm-5.3-flash"
    msg = transport.last_body["messages"][0]
    assert msg["role"] == "user"
    text_parts = [p for p in msg["content"] if p["type"] == "text"]
    image_parts = [p for p in msg["content"] if p["type"] == "image_url"]
    assert text_parts and text_parts[0]["text"] == "describe this image"
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_missing_base_url_raises(tmp_path, registered):
    settings = _settings(tmp_path, base_url=None)
    vllm = registered.create_vision_llm(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(LLMRequestError, match="base_url"):
        vllm.chat_with_image("describe", b"\x89PNG data")


# ---------------------------------------------------------------------------
# 错误场景
# ---------------------------------------------------------------------------


def test_api_error_raises_readable(tmp_path, registered):
    err_resp = {"error": {"message": "图片输入格式/解析错误", "code": "1210"}}
    transport = _FakeTransport(err_resp)
    vllm = registered.create_vision_llm(settings=_settings(tmp_path), transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        vllm.chat_with_image("describe", b"not an image")
    assert "1210" in str(exc_info.value)
    assert "图片输入格式/解析错误" in str(exc_info.value)


def test_default_transport_relabels_provider(tmp_path, registered):
    """默认传输的网络错误标签为本 provider，不残留 azure-vision。"""
    vllm = registered.create_vision_llm(settings=_settings(tmp_path))
    assert vllm._transport is not None
    # 不注入 transport，走默认 urllib（必然连接失败），断言错误可读且不含 api key
    settings_yaml = _settings(tmp_path, base_url="https://localhost:1/v4")
    vllm2 = registered.create_vision_llm(settings_yaml)
    with pytest.raises(LLMRequestError) as exc_info:
        vllm2.chat_with_image("describe", b"\x89PNG data")
    assert "zai-key" not in str(exc_info.value)


def test_error_does_not_leak_api_key(tmp_path, registered):
    transport = _FakeTransport(raise_exc=LLMRequestError("openai-vision", "network error: boom"))
    settings = _settings(tmp_path, api_key="super-secret-vision-key")
    vllm = registered.create_vision_llm(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        vllm.chat_with_image("describe", b"\x89PNG data")
    assert "super-secret-vision-key" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 继承能力抽查：文本 chat 与响应解析
# ---------------------------------------------------------------------------


def test_chat_text_inherited(tmp_path, registered):
    transport = _FakeTransport(_ok_response("text reply"))
    vllm = registered.create_vision_llm(settings=_settings(tmp_path), transport=transport)
    assert vllm.chat_text("hello") == "text reply"
    assert transport.last_body["messages"][0]["content"] == "hello"


def test_response_includes_usage(tmp_path, registered):
    transport = _FakeTransport(_ok_response())
    vllm = registered.create_vision_llm(settings=_settings(tmp_path), transport=transport)
    resp = vllm.chat_with_image("describe", b"\x89PNG data")
    assert resp.content == "红色"
    assert resp.usage is not None
    assert resp.usage["total_tokens"] == 15
