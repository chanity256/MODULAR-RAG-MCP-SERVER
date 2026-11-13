"""Ollama NDJSON 流式（WC5.3）：全部经 ``httpx.MockTransport``，零网络。"""

from __future__ import annotations

import json

import httpx
import pytest

from core.settings import LLMConfig
from libs.llm.base_llm import ChatMessage
from libs.llm.ollama_llm import OllamaLLM
from libs.llm.openai_llm import LLMRequestError, make_httpx_stream_transport

MSGS = [ChatMessage("user", "hi")]


def _cfg() -> LLMConfig:
    return LLMConfig(provider="ollama", model="llama3", base_url="http://ollama.test:11434", api_key="tok-secret")


def _line(content="", *, done=False, **extra) -> str:
    obj = {"model": "llama3", "message": {"role": "assistant", "content": content}, "done": done}
    obj.update(extra)
    return json.dumps(obj) + "\n"


def _llm(handler) -> OllamaLLM:
    transport = make_httpx_stream_transport(httpx_transport=httpx.MockTransport(handler))
    return OllamaLLM(_cfg(), stream_transport=transport)


def _ok(body: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode())

    return handler


def test_ndjson_stream_yields_deltas_until_done():
    body = _line("Hel") + _line("lo") + _line("", done=True, prompt_eval_count=4, eval_count=2) + _line("IGNORED")
    llm = _llm(_ok(body))
    assert list(llm.chat_stream(MSGS)) == ["Hel", "lo"]
    assert llm.last_usage == {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}


def test_request_sets_stream_true():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=_line("x", done=True).encode())

    list(_llm(handler).chat_stream(MSGS, temperature=0.2))
    assert seen["url"] == "http://ollama.test:11434/api/chat"
    assert seen["body"]["stream"] is True
    assert seen["body"]["options"] == {"temperature": 0.2}
    assert seen["auth"] == "Bearer tok-secret"


def test_blank_lines_tolerated_and_usage_none_without_counts():
    body = "\n" + _line("a") + "\n\n" + _line("", done=True)
    llm = _llm(_ok(body))
    assert list(llm.chat_stream(MSGS)) == ["a"]
    assert llm.last_usage is None


def test_eof_without_done_is_error():
    gen = _llm(_ok(_line("part"))).chat_stream(MSGS)
    assert next(gen) == "part"
    with pytest.raises(LLMRequestError, match="ended unexpectedly"):
        next(gen)


class _CutStream(httpx.SyncByteStream):
    def __iter__(self):
        yield _line("part").encode()
        raise httpx.RemoteProtocolError("peer closed connection")


def test_mid_stream_break_raises():
    gen = _llm(lambda r: httpx.Response(200, stream=_CutStream())).chat_stream(MSGS)
    assert next(gen) == "part"
    with pytest.raises(LLMRequestError) as ei:
        next(gen)
    assert ei.value.provider == "ollama"


def test_non_200_raises_with_status():
    def handler(request):
        return httpx.Response(404, json={"error": "model 'llama3' not found"})

    with pytest.raises(LLMRequestError) as ei:
        list(_llm(handler).chat_stream(MSGS))
    assert ei.value.status == 404
    assert ei.value.provider == "ollama"
    assert "not found" in str(ei.value)
    assert "tok-secret" not in str(ei.value)


def test_error_line_inside_stream_raises():
    body = _line("a") + json.dumps({"error": "out of memory"}) + "\n"
    gen = _llm(_ok(body)).chat_stream(MSGS)
    assert next(gen) == "a"
    with pytest.raises(LLMRequestError, match="out of memory"):
        next(gen)


def test_malformed_line_raises():
    with pytest.raises(LLMRequestError, match="invalid NDJSON"):
        list(_llm(_ok("{oops\n")).chat_stream(MSGS))


def test_sync_chat_still_sends_stream_false():
    seen = {}

    def transport(url, headers, body, *, timeout=None):
        seen.update(body)
        return {"message": {"content": "ok"}, "done": True}

    llm = OllamaLLM(_cfg(), transport=transport)
    assert llm.chat(MSGS).content == "ok"
    assert seen["stream"] is False
