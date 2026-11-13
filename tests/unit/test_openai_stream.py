"""OpenAI 兼容 SSE 流式（WC5.2）：全部经 ``httpx.MockTransport``，零网络。"""

from __future__ import annotations

import json

import httpx
import pytest

from core.settings import LLMConfig
from libs.llm.azure_llm import AzureLLM
from libs.llm.base_llm import ChatMessage
from libs.llm.deepseek_llm import DeepSeekLLM
from libs.llm.openai_llm import LLMRequestError, OpenAILLM, make_httpx_stream_transport

MSGS = [ChatMessage("user", "hi")]


def _cfg(**kw) -> LLMConfig:
    base = dict(provider="openai", model="gpt-x", api_key="sk-secret", base_url="http://llm.test/v1")
    base.update(kw)
    return LLMConfig(**base)


def _frame(content=None, *, finish=None, usage=None, extra=None) -> str:
    obj: dict = {"choices": [{"index": 0, "delta": {} if content is None else {"content": content}, "finish_reason": finish}]}
    if usage is not None:
        obj = {"choices": [], "usage": usage}
    if extra:
        obj.update(extra)
    return f"data: {json.dumps(obj)}\n\n"


def _llm(handler, cls=OpenAILLM, config=None, **kw):
    transport = make_httpx_stream_transport(httpx_transport=httpx.MockTransport(handler))
    return cls(config or _cfg(), stream_transport=transport, **kw)


def _sse_ok(body: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    return handler


def test_normal_stream_accumulates_deltas_and_stops_at_done():
    body = (
        'data: {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}\n\n'
        + _frame("Hel")
        + _frame("lo ")
        + _frame("world", finish="stop")
        + "data: [DONE]\n\n"
        + _frame("IGNORED")
    )
    llm = _llm(_sse_ok(body))
    assert list(llm.chat_stream(MSGS)) == ["Hel", "lo ", "world"]


def test_request_body_and_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        return httpx.Response(200, content=(_frame("x", finish="stop") + "data: [DONE]\n\n").encode())

    llm = _llm(handler)
    list(llm.chat_stream(MSGS, temperature=0.5))
    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["body"]["stream"] is True
    assert seen["body"]["model"] == "gpt-x"
    assert seen["body"]["temperature"] == 0.5
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert seen["headers"]["authorization"] == "Bearer sk-secret"
    assert seen["headers"]["accept"] == "text/event-stream"


def test_tolerates_keepalive_comments_blank_lines_and_event_fields():
    body = (
        ": keepalive\n\n"
        "\n\n"
        "event: message\n"
        + _frame("a")
        + ": ping\n\n"
        + "data:" + json.dumps({"choices": [{"delta": {"content": "b"}}]}) + "\n\n"  # 无空格的 data:
        + "data: \n\n"
        + _frame(None, finish="stop")
        + "data: [DONE]\n\n"
    )
    assert list(_llm(_sse_ok(body)).chat_stream(MSGS)) == ["a", "b"]


def test_captures_usage_when_provider_returns_it():
    usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
    body = _frame("a") + _frame(None, finish="stop") + _frame(usage=usage) + "data: [DONE]\n\n"
    llm = _llm(_sse_ok(body))
    list(llm.chat_stream(MSGS))
    assert llm.last_usage == usage


def test_usage_none_when_absent_and_reset_between_calls():
    llm = _llm(_sse_ok(_frame("a", finish="stop") + "data: [DONE]\n\n"))
    llm.last_usage = {"stale": 1}
    list(llm.chat_stream(MSGS))
    assert llm.last_usage is None


def test_trace_receives_usage_stage():
    class _Trace:
        def __init__(self):
            self.stages = []

        def record_stage(self, stage, **data):
            self.stages.append((stage, data))

    usage = {"total_tokens": 7}
    body = _frame("a", finish="stop") + _frame(usage=usage) + "data: [DONE]\n\n"
    trace = _Trace()
    list(_llm(_sse_ok(body)).chat_stream(MSGS, trace=trace))
    assert trace.stages[0][0] == "llm_stream"
    assert trace.stages[0][1]["usage"] == usage
    assert trace.stages[0][1]["provider"] == "openai"


def test_stream_without_done_but_with_finish_reason_is_ok():
    assert list(_llm(_sse_ok(_frame("a", finish="stop"))).chat_stream(MSGS)) == ["a"]


def test_eof_without_done_or_finish_is_error():
    llm = _llm(_sse_ok(_frame("partial")))
    gen = llm.chat_stream(MSGS)
    assert next(gen) == "partial"
    with pytest.raises(LLMRequestError, match="ended unexpectedly"):
        next(gen)


class _CutStream(httpx.SyncByteStream):
    def __iter__(self):
        yield _frame("part").encode()
        raise httpx.ReadError("connection reset")


def test_mid_stream_break_raises_llm_request_error_after_partial_output():
    def handler(request):
        return httpx.Response(200, stream=_CutStream())

    gen = _llm(handler).chat_stream(MSGS)
    assert next(gen) == "part"
    with pytest.raises(LLMRequestError) as ei:
        next(gen)
    assert ei.value.provider == "openai"
    assert ei.value.status is None
    assert "ReadError" in str(ei.value)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_non_200_raises_with_status(status):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(LLMRequestError) as ei:
        list(_llm(handler).chat_stream(MSGS))
    assert ei.value.status == status
    assert ei.value.provider == "openai"
    assert "nope" in str(ei.value)
    assert ei.value.body == {"error": {"message": "nope"}}
    assert "sk-secret" not in str(ei.value)
    assert str(ei.value).count("status=") == 1


def test_non_200_non_json_body():
    def handler(request):
        return httpx.Response(502, content=b"<html>bad gateway</html>")

    with pytest.raises(LLMRequestError) as ei:
        list(_llm(handler).chat_stream(MSGS))
    assert ei.value.status == 502
    assert ei.value.body == {"raw": "<html>bad gateway</html>"}


def test_connection_error_maps_to_llm_request_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMRequestError) as ei:
        list(_llm(handler).chat_stream(MSGS))
    assert ei.value.status is None
    assert "ConnectError" in str(ei.value)


def test_error_frame_inside_stream_raises():
    body = _frame("a") + 'data: {"error": {"message": "overloaded"}}\n\n'
    gen = _llm(_sse_ok(body)).chat_stream(MSGS)
    assert next(gen) == "a"
    with pytest.raises(LLMRequestError, match="overloaded"):
        next(gen)


def test_malformed_frame_raises():
    with pytest.raises(LLMRequestError, match="invalid SSE frame"):
        list(_llm(_sse_ok("data: {not json\n\n")).chat_stream(MSGS))


def test_input_validation_matches_chat():
    llm = _llm(_sse_ok(""))
    with pytest.raises(LLMRequestError, match="non-empty"):
        list(llm.chat_stream([]))
    with pytest.raises(LLMRequestError, match="ChatMessage"):
        list(llm.chat_stream([{"role": "user", "content": "x"}]))  # type: ignore[list-item]


def test_chat_stream_does_not_mutate_chat_behavior():
    """同步 chat() 仍走 transport，不带 stream 字段。"""
    seen = {}

    def transport(url, headers, body, *, timeout=None):
        seen.update(body)
        return {"choices": [{"message": {"content": "ok"}}]}

    llm = OpenAILLM(_cfg(), transport=transport)
    assert llm.chat(MSGS).content == "ok"
    assert "stream" not in seen and "stream_options" not in seen


def test_azure_and_deepseek_variants_inherit_streaming():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        return httpx.Response(200, content=(_frame("z", finish="stop") + "data: [DONE]\n\n").encode())

    azure_cfg = _cfg(provider="azure", azure_endpoint="https://az.test", deployment_name="dep", base_url=None)
    azure = _llm(handler, AzureLLM, azure_cfg)
    assert list(azure.chat_stream(MSGS)) == ["z"]
    assert seen["url"].startswith("https://az.test/openai/deployments/dep/chat/completions")
    assert seen["headers"]["api-key"] == "sk-secret"

    ds = _llm(handler, DeepSeekLLM, _cfg(provider="deepseek", base_url=None))
    assert list(ds.chat_stream(MSGS)) == ["z"]
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"


def test_error_provider_is_rewritten_for_variants():
    def handler(request):
        return httpx.Response(429, json={"error": "slow down"})

    ds = _llm(handler, DeepSeekLLM, _cfg(provider="deepseek", base_url=None))
    with pytest.raises(LLMRequestError) as ei:
        list(ds.chat_stream(MSGS))
    assert ei.value.provider == "deepseek"
    assert ei.value.status == 429
