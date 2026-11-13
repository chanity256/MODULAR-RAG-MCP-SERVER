"""OpenAI / Azure / DeepSeek LLM provider 冒烟测试（阶段 B7.1）。

通过注入 fake transport（mock HTTP，不走真实网络）验证：
- 配置不同 provider 时工厂路由正确。
- ``chat(messages)`` 对输入 shape 校验清晰，异常信息可读（包含 provider 与错误类型）。
- 请求 URL / headers / body 按各 provider 规范构建。
- 响应解析正确，错误响应抛出可读错误。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.llm.base_llm import ChatMessage, ChatResponse
from libs.llm.llm_factory import LLMFactory
from libs.llm.openai_llm import LLMRequestError, OpenAICompatibleLLM

_VALID_YAML = dedent(
    """\
    llm:
      provider: openai
      model: gpt-4o
      api_key: sk-test
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


def _settings(tmp_path: Path, **llm_overrides):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if llm_overrides:
        settings = replace(settings, llm=replace(settings.llm, **llm_overrides))
    return settings


def _ok_response(content: str = "hello", model: str = "gpt-4o") -> dict:
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    }


class _FakeTransport:
    """记录最后一次请求并返回预设响应。"""

    def __init__(self, response: dict | None = None, raise_exc: Exception | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.last_url: str | None = None
        self.last_headers: dict | None = None
        self.last_body: dict | None = None

    def __call__(self, url, headers, body, *, timeout=None):
        self.last_url = url
        self.last_headers = headers
        self.last_body = body
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


@pytest.fixture
def registered_providers():
    """确保真实 provider 已注册（导入 libs.llm 即注册）。"""
    import libs.llm  # noqa: F401
    yield LLMFactory


# ---------------------------------------------------------------------------
# 工厂路由
# ---------------------------------------------------------------------------


def test_factory_routes_openai(tmp_path, registered_providers):
    settings = _settings(tmp_path, provider="openai")
    llm = registered_providers.create(settings, transport=_FakeTransport(_ok_response()))
    assert llm.__class__.__name__ == "OpenAILLM"


def test_factory_routes_azure(tmp_path, registered_providers):
    settings = _settings(
        tmp_path, provider="azure", azure_endpoint="https://example.openai.azure.com",
        deployment_name="gpt-4o-deploy", api_key="az-key",
    )
    llm = registered_providers.create(settings, transport=_FakeTransport(_ok_response()))
    assert llm.__class__.__name__ == "AzureLLM"


def test_factory_routes_deepseek(tmp_path, registered_providers):
    settings = _settings(tmp_path, provider="deepseek", api_key="ds-key")
    llm = registered_providers.create(settings, transport=_FakeTransport(_ok_response()))
    assert llm.__class__.__name__ == "DeepSeekLLM"


# ---------------------------------------------------------------------------
# chat 行为 + 请求构建
# ---------------------------------------------------------------------------


def test_openai_chat_builds_request_and_parses_response(tmp_path, registered_providers):
    transport = _FakeTransport(_ok_response(content="hi there", model="gpt-4o"))
    settings = _settings(tmp_path, provider="openai", api_key="sk-test")
    llm = registered_providers.create(settings, transport=transport)
    resp = llm.chat([ChatMessage("user", "hello")])
    assert isinstance(resp, ChatResponse)
    assert resp.content == "hi there"
    assert resp.model == "gpt-4o"
    assert resp.usage == {"prompt_tokens": 5, "completion_tokens": 1}
    # URL
    assert transport.last_url == "https://api.openai.com/v1/chat/completions"
    # headers
    assert transport.last_headers["Authorization"] == "Bearer sk-test"
    assert transport.last_headers["Content-Type"] == "application/json"
    # body
    assert transport.last_body["model"] == "gpt-4o"
    assert transport.last_body["messages"] == [{"role": "user", "content": "hello"}]


def test_openai_chat_uses_custom_base_url(tmp_path, registered_providers):
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path, provider="openai", base_url="https://proxy.example.com/v1")
    llm = registered_providers.create(settings, transport=transport)
    llm.chat([ChatMessage("user", "hi")])
    assert transport.last_url == "https://proxy.example.com/v1/chat/completions"


def test_deepseek_chat_uses_deepseek_base_url(tmp_path, registered_providers):
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path, provider="deepseek", api_key="ds-key")
    llm = registered_providers.create(settings, transport=transport)
    llm.chat([ChatMessage("user", "hi")])
    assert transport.last_url == "https://api.deepseek.com/v1/chat/completions"
    assert transport.last_headers["Authorization"] == "Bearer ds-key"


def test_azure_chat_builds_azure_specific_url_and_headers(tmp_path, registered_providers):
    transport = _FakeTransport(_ok_response())
    settings = _settings(
        tmp_path, provider="azure",
        azure_endpoint="https://example.openai.azure.com",
        deployment_name="gpt-4o-deploy", api_key="az-key",
    )
    llm = registered_providers.create(settings, transport=transport)
    llm.chat([ChatMessage("user", "hi")])
    url = transport.last_url
    assert "example.openai.azure.com" in url
    assert "/openai/deployments/gpt-4o-deploy/chat/completions" in url
    assert "api-version=" in url
    # Azure 用 api-key 头而非 Bearer
    assert transport.last_headers["api-key"] == "az-key"
    assert "Authorization" not in transport.last_headers


def test_temperature_and_max_tokens_in_body(tmp_path, registered_providers):
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path, provider="openai", temperature=0.7, max_tokens=128)
    llm = registered_providers.create(settings, transport=transport)
    llm.chat([ChatMessage("user", "hi")])
    assert transport.last_body["temperature"] == 0.7
    assert transport.last_body["max_tokens"] == 128


def test_chat_kwargs_override_body(tmp_path, registered_providers):
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path, provider="openai", temperature=0.7)
    llm = registered_providers.create(settings, transport=transport)
    llm.chat([ChatMessage("user", "hi")], temperature=0.1, max_tokens=10)
    assert transport.last_body["temperature"] == 0.1
    assert transport.last_body["max_tokens"] == 10


# ---------------------------------------------------------------------------
# 输入校验 + 可读错误
# ---------------------------------------------------------------------------


def test_empty_messages_raises_with_provider(tmp_path, registered_providers):
    settings = _settings(tmp_path, provider="openai")
    llm = registered_providers.create(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([])
    assert "openai" in str(exc_info.value)
    assert "non-empty" in str(exc_info.value)


def test_non_chatmessage_raises(tmp_path, registered_providers):
    settings = _settings(tmp_path, provider="openai")
    llm = registered_providers.create(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([{"role": "user", "content": "hi"}])  # type: ignore[list-item]
    assert "ChatMessage" in str(exc_info.value)


def test_empty_role_or_content_raises(tmp_path, registered_providers):
    settings = _settings(tmp_path, provider="openai")
    llm = registered_providers.create(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(LLMRequestError):
        llm.chat([ChatMessage("", "hi")])
    with pytest.raises(LLMRequestError):
        llm.chat([ChatMessage("user", "")])


def test_api_error_response_raises_readable(tmp_path, registered_providers):
    err_resp = {"error": {"message": "invalid_api_key", "type": "invalid_request_error"}}
    settings = _settings(tmp_path, provider="openai", api_key="bad")
    llm = registered_providers.create(settings, transport=_FakeTransport(err_resp))
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([ChatMessage("user", "hi")])
    assert "openai" in str(exc_info.value)
    assert "invalid_api_key" in str(exc_info.value)


def test_malformed_response_raises(tmp_path, registered_providers):
    settings = _settings(tmp_path, provider="openai")
    llm = registered_providers.create(settings, transport=_FakeTransport({"foo": "bar"}))
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([ChatMessage("user", "hi")])
    assert "choices" in str(exc_info.value)


def test_transport_http_error_wrapped_with_provider(tmp_path, registered_providers):
    from libs.llm.openai_llm import LLMRequestError as _Err

    def raising_transport(url, headers, body, *, timeout=None):
        raise _Err("unknown", "HTTP error: 401 Unauthorized", status=401)

    settings = _settings(tmp_path, provider="openai")
    llm = registered_providers.create(settings, transport=raising_transport)
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([ChatMessage("user", "hi")])
    assert "openai" in str(exc_info.value)
    assert exc_info.value.status == 401


def test_azure_missing_endpoint_raises(tmp_path, registered_providers):
    settings = _settings(tmp_path, provider="azure", deployment_name="gpt-4o-deploy")
    llm = registered_providers.create(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([ChatMessage("user", "hi")])
    assert "azure" in str(exc_info.value)
    assert "azure_endpoint" in str(exc_info.value)
