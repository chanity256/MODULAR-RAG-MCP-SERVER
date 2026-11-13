"""``OllamaLLM`` 冒烟测试（阶段 B7.2，mock HTTP）。

通过注入 fake transport 验证：
- provider=ollama 时可由 ``LLMFactory`` 创建。
- 请求 URL / body 符合 Ollama 原生 ``/api/chat`` 规范。
- 连接失败 / 超时 / 错误响应等场景抛出可读错误，且不泄露敏感配置（api_key）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.llm.base_llm import ChatMessage, ChatResponse
from libs.llm.llm_factory import LLMFactory
from libs.llm.openai_llm import LLMRequestError

_VALID_YAML = dedent(
    """\
    llm:
      provider: ollama
      model: llama3
      base_url: http://localhost:11434
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


def _ok_response(content: str = "hi", model: str = "llama3") -> dict:
    return {"model": model, "message": {"role": "assistant", "content": content}, "done": True}


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
# 工厂创建 + 请求构建
# ---------------------------------------------------------------------------


def test_factory_creates_ollama(tmp_path, registered):
    settings = _settings(tmp_path, provider="ollama")
    llm = registered.create(settings, transport=_FakeTransport(_ok_response()))
    assert llm.__class__.__name__ == "OllamaLLM"


def test_ollama_chat_builds_native_request(tmp_path, registered):
    transport = _FakeTransport(_ok_response(content="hello world", model="llama3"))
    settings = _settings(tmp_path, provider="ollama", model="llama3", base_url="http://localhost:11434")
    llm = registered.create(settings, transport=transport)
    resp = llm.chat([ChatMessage("user", "hi")])
    assert isinstance(resp, ChatResponse)
    assert resp.content == "hello world"
    assert resp.model == "llama3"
    # URL: Ollama 原生 /api/chat
    assert transport.last_url == "http://localhost:11434/api/chat"
    # body
    assert transport.last_body["model"] == "llama3"
    assert transport.last_body["messages"] == [{"role": "user", "content": "hi"}]
    assert transport.last_body["stream"] is False


def test_ollama_custom_base_url(tmp_path, registered):
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path, provider="ollama", base_url="http://gpu-box:11434")
    llm = registered.create(settings, transport=transport)
    llm.chat([ChatMessage("user", "hi")])
    assert transport.last_url == "http://gpu-box:11434/api/chat"


def test_ollama_default_base_url_when_unspecified(tmp_path, registered):
    transport = _FakeTransport(_ok_response())
    # 通过 dataclass 覆盖把 base_url 置空，触发默认值兜底。
    settings = _settings(tmp_path, provider="ollama", base_url=None)
    llm = registered.create(settings, transport=transport)
    llm.chat([ChatMessage("user", "hi")])
    assert transport.last_url == "http://localhost:11434/api/chat"


def test_ollama_temperature_in_options(tmp_path, registered):
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path, provider="ollama", temperature=0.3)
    llm = registered.create(settings, transport=transport)
    llm.chat([ChatMessage("user", "hi")])
    assert transport.last_body["options"]["temperature"] == 0.3


def test_ollama_stream_false_always_set(tmp_path, registered):
    transport = _FakeTransport(_ok_response())
    settings = _settings(tmp_path, provider="ollama")
    llm = registered.create(settings, transport=transport)
    llm.chat([ChatMessage("user", "hi")])
    assert transport.last_body["stream"] is False


# ---------------------------------------------------------------------------
# 错误处理：可读且不泄露敏感配置
# ---------------------------------------------------------------------------


def test_api_error_raises_readable(tmp_path, registered):
    transport = _FakeTransport({"error": "model 'llama3' not found"})
    settings = _settings(tmp_path, provider="ollama")
    llm = registered.create(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([ChatMessage("user", "hi")])
    assert "ollama" in str(exc_info.value)
    assert "not found" in str(exc_info.value)


def test_malformed_response_raises(tmp_path, registered):
    transport = _FakeTransport({"foo": "bar"})
    settings = _settings(tmp_path, provider="ollama")
    llm = registered.create(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([ChatMessage("user", "hi")])
    assert "ollama" in str(exc_info.value)


def test_connection_failure_raises_readable(tmp_path, registered):
    transport = _FakeTransport(raise_exc=ConnectionError("Connection refused"))
    settings = _settings(tmp_path, provider="ollama")
    llm = registered.create(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([ChatMessage("user", "hi")])
    assert "ollama" in str(exc_info.value)
    assert "Connection refused" in str(exc_info.value)


def test_timeout_raises_readable(tmp_path, registered):
    import socket

    transport = _FakeTransport(raise_exc=socket.timeout("timed out"))
    settings = _settings(tmp_path, provider="ollama")
    llm = registered.create(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([ChatMessage("user", "hi")])
    assert "ollama" in str(exc_info.value)


def test_error_does_not_leak_api_key(tmp_path, registered):
    transport = _FakeTransport(raise_exc=ConnectionError("boom"))
    settings = _settings(tmp_path, provider="ollama", api_key="super-secret-key")
    llm = registered.create(settings, transport=transport)
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([ChatMessage("user", "hi")])
    assert "super-secret-key" not in str(exc_info.value)


def test_empty_messages_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="ollama")
    llm = registered.create(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(LLMRequestError) as exc_info:
        llm.chat([])
    assert "ollama" in str(exc_info.value)
    assert "non-empty" in str(exc_info.value)


def test_non_chatmessage_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="ollama")
    llm = registered.create(settings, transport=_FakeTransport(_ok_response()))
    with pytest.raises(LLMRequestError):
        llm.chat(["hi"])  # type: ignore[list-item]
