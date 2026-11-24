"""``OpenAICompatibleLLM`` 的 ``llm.extra_headers`` 支持（对齐 ``OpenAICompatibleEmbedding`` 先例）。

背景：OpenCode Go 等 OpenAI 兼容端点要求附加会话头（``x-opencode-session``），
主 venv 的 LLM 客户端此前无法发送任何自定义头。覆盖：

- 默认（无 ``extra_headers``）请求头与既有行为逐键一致；
- ``llm.extra["extra_headers"]``（str→str 映射）原样合并进请求头；
- 认证头不可被覆盖（``Authorization`` 始终以 ``api_key`` 为准）；
- 非法类型（非 dict / 含非 str 值）在构造时即抛 ``LLMRequestError``。
"""

from __future__ import annotations

import pytest

from core.settings import LLMConfig
from libs.llm.base_llm import ChatMessage
from libs.llm.openai_llm import LLMRequestError, OpenAICompatibleLLM

MSGS = [ChatMessage("user", "hi")]


def _ok_response() -> dict:
    return {
        "model": "gpt-4o",
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def _llm(extra_headers: object | None, transport) -> OpenAICompatibleLLM:
    extra: dict = {} if extra_headers is None else {"extra_headers": extra_headers}
    config = LLMConfig(provider="openai", model="gpt-4o", api_key="sk-test", extra=extra)
    return OpenAICompatibleLLM(config, transport=transport)


def test_no_extra_headers_keeps_default_headers():
    captured: dict = {}

    def transport(url, headers, body, *, timeout=None):
        captured["headers"] = headers
        return _ok_response()

    _llm(None, transport).chat(MSGS)
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-test",
    }


def test_extra_headers_merged_into_request():
    captured: dict = {}

    def transport(url, headers, body, *, timeout=None):
        captured["headers"] = headers
        return _ok_response()

    _llm({"x-opencode-session": "modular-rag-chat"}, transport).chat(MSGS)
    assert captured["headers"]["x-opencode-session"] == "modular-rag-chat"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_authorization_cannot_be_overridden():
    captured: dict = {}

    def transport(url, headers, body, *, timeout=None):
        captured["headers"] = headers
        return _ok_response()

    _llm({"Authorization": "Bearer evil", "x-opencode-session": "s"}, transport).chat(MSGS)
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_non_str_value_rejected_at_construction():
    def transport(url, headers, body, *, timeout=None):
        return _ok_response()

    with pytest.raises(LLMRequestError) as exc_info:
        _llm({"x-session": 123}, transport)
    assert "extra_headers" in str(exc_info.value)


def test_non_dict_rejected_at_construction():
    def transport(url, headers, body, *, timeout=None):
        return _ok_response()

    with pytest.raises(LLMRequestError) as exc_info:
        _llm("x-opencode-session: s", transport)
    assert "extra_headers" in str(exc_info.value)
