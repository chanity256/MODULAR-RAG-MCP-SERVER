"""``BaseLLM.chat_stream`` 默认实现契约（WC5.1，fake LLM，零网络）。"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

from libs.llm.base_llm import BaseLLM, ChatMessage, ChatResponse
from libs.llm.openai_llm import LLMRequestError


class _FakeLLM(BaseLLM):
    def __init__(self, content: str = "hello world", usage: dict | None = None, error: Exception | None = None):
        self.content = content
        self.usage = usage
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages: Sequence[ChatMessage], *, trace: Any | None = None, **kwargs: Any) -> ChatResponse:
        self.calls.append({"messages": list(messages), "trace": trace, "kwargs": kwargs})
        if self.error:
            raise self.error
        return ChatResponse(content=self.content, model="fake", usage=self.usage)


MSGS = [ChatMessage("user", "hi")]


def test_default_chat_stream_yields_whole_content_once():
    llm = _FakeLLM("hello world")
    assert list(llm.chat_stream(MSGS)) == ["hello world"]
    assert len(llm.calls) == 1


def test_default_chat_stream_forwards_trace_and_kwargs():
    llm = _FakeLLM()
    sentinel = object()
    list(llm.chat_stream(MSGS, trace=sentinel, temperature=0.3))
    assert llm.calls[0]["trace"] is sentinel
    assert llm.calls[0]["kwargs"] == {"temperature": 0.3}


def test_default_chat_stream_is_lazy():
    llm = _FakeLLM()
    gen = llm.chat_stream(MSGS)
    assert llm.calls == []
    next(gen)
    assert len(llm.calls) == 1


def test_default_chat_stream_records_usage():
    usage = {"prompt_tokens": 3, "completion_tokens": 2}
    llm = _FakeLLM(usage=usage)
    assert llm.last_usage is None
    list(llm.chat_stream(MSGS))
    assert llm.last_usage == usage


def test_default_chat_stream_empty_content_yields_nothing():
    assert list(_FakeLLM("").chat_stream(MSGS)) == []


def test_default_chat_stream_propagates_llm_error():
    llm = _FakeLLM(error=LLMRequestError("fake", "boom", status=500))
    with pytest.raises(LLMRequestError) as ei:
        list(llm.chat_stream(MSGS))
    assert ei.value.status == 500


def test_llm_request_error_exposes_status_and_detail():
    err = LLMRequestError("p", "bad", status=429)
    assert err.status == 429
    assert err.detail == "bad"
    assert LLMRequestError("p", "x").status is None
