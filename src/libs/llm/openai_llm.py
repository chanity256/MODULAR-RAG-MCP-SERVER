"""OpenAI-Compatible LLM 实现（阶段 B7.1）。

覆盖 OpenAI / Azure / DeepSeek 等 OpenAI 兼容 API 的统一实现。

设计要点：
- ``OpenAICompatibleLLM`` 为基类，封装请求构建 / 响应解析 / 可读错误。
- 通过 **可注入的 ``transport``** 抽象 HTTP 边界：``transport(url, headers, body, *, timeout) -> dict``
  返回解析后的 JSON。生产环境用 ``_default_transport``（stdlib ``urllib``，无新依赖），
  测试环境注入 fake transport，实现"mock HTTP，不走真实网络"。
- 子类只覆盖 ``_build_url`` / ``_build_headers`` / provider 名。
- 流式（``chat_stream``）走独立的 **可注入 ``stream_transport``**：
  ``stream_transport(url, headers, body, *, timeout) -> Iterator[str]`` 逐行产出响应文本，
  生产用 httpx（``make_httpx_stream_transport``），测试注入基于 ``httpx.MockTransport`` 的实现。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator, Sequence

import httpx

from core.settings import LLMConfig
from libs.llm.base_llm import BaseLLM, ChatMessage, ChatResponse

__all__ = [
    "LLMRequestError",
    "OpenAICompatibleLLM",
    "OpenAILLM",
    "make_httpx_stream_transport",
    "register_openai_provider",
]

# HTTP 传输层签名：发送 POST 请求并返回解析后的 JSON body。
Transport = Callable[..., dict[str, Any]]
# 流式传输层签名：发送 POST 请求并逐行产出响应文本（SSE / NDJSON 由调用方解析）。
StreamTransport = Callable[..., Iterator[str]]


class LLMRequestError(RuntimeError):
    """LLM HTTP 请求错误，包含 provider 与错误类型，便于定位。"""

    def __init__(self, provider: str, message: str, *, status: int | None = None, body: Any = None):
        self.provider = provider
        self.detail = message
        self.status = status
        self.body = body
        parts = [f"[{provider}]"]
        if status is not None:
            parts.append(f"status={status}")
        parts.append(message)
        super().__init__(" ".join(parts))


def _default_transport(url: str, headers: dict[str, str], body: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    """默认 HTTP 传输：用 stdlib urllib 发送 POST，返回解析后的 JSON。"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 受控的 provider URL
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        raise LLMRequestError("unknown", f"HTTP error: {raw[:200]}", status=status, body=parsed) from exc
    except urllib.error.URLError as exc:
        raise LLMRequestError("unknown", f"network error: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMRequestError("unknown", f"non-JSON response: {raw[:200]}", status=status) from exc


def make_httpx_stream_transport(*, httpx_transport: httpx.BaseTransport | None = None) -> StreamTransport:
    """构造基于 httpx 的流式传输函数。

    非 200 响应与连接/读取中断统一抛 ``LLMRequestError``（provider 记为 ``unknown``，
    由调用方 LLM 改写为真实 provider）；非 200 时带 ``status`` 与解析后的 ``body``。

    Args:
        httpx_transport: 注入 httpx 底层传输（测试用 ``httpx.MockTransport``）；
            ``None`` 时走真实网络。
    """

    def _stream(
        url: str, headers: dict[str, str], body: dict[str, Any], *, timeout: float | None = None
    ) -> Iterator[str]:
        try:
            with httpx.Client(transport=httpx_transport, timeout=timeout) as client:
                with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code != 200:
                        raw = resp.read().decode("utf-8", errors="replace")
                        try:
                            parsed: Any = json.loads(raw)
                        except json.JSONDecodeError:
                            parsed = {"raw": raw}
                        raise LLMRequestError(
                            "unknown", f"HTTP error: {raw[:200]}", status=resp.status_code, body=parsed
                        )
                    yield from resp.iter_lines()
        except httpx.HTTPError as exc:
            raise LLMRequestError("unknown", f"stream interrupted: {type(exc).__name__}: {exc}") from exc

    return _stream


class OpenAICompatibleLLM(BaseLLM):
    """OpenAI 兼容 API 的 LLM 基类。"""

    provider = "openai-compatible"

    def __init__(
        self,
        llm_config: LLMConfig,
        *,
        transport: Transport | None = None,
        stream_transport: StreamTransport | None = None,
        **kwargs: Any,
    ):
        self.llm_config = llm_config
        self._transport: Transport = transport or _default_transport
        self._stream_transport: StreamTransport = stream_transport or make_httpx_stream_transport()
        self._timeout: float | None = kwargs.pop("timeout", None)
        self._extra_kwargs = kwargs

    # -- 子类覆盖点 -------------------------------------------------------

    def _build_url(self) -> str:
        base = self.llm_config.base_url or "https://api.openai.com/v1"
        return f"{base.rstrip('/')}/chat/completions"

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self.llm_config.api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    # -- 主流程 -----------------------------------------------------------

    def _build_body(self, messages: Sequence[ChatMessage], kwargs: dict[str, Any]) -> dict[str, Any]:
        if not messages:
            raise LLMRequestError(self.provider, "messages must be non-empty")
        for m in messages:
            if not isinstance(m, ChatMessage):
                raise LLMRequestError(self.provider, f"each message must be ChatMessage, got {type(m).__name__}")
            if not m.role or not m.content:
                raise LLMRequestError(self.provider, "message role/content must be non-empty")

        body: dict[str, Any] = {
            "model": kwargs.pop("model", self.llm_config.model),
            "messages": [m.to_dict() for m in messages],
        }
        if "temperature" in kwargs:
            body["temperature"] = kwargs.pop("temperature")
        elif self.llm_config.temperature is not None:
            body["temperature"] = self.llm_config.temperature
        if "max_tokens" in kwargs:
            body["max_tokens"] = kwargs.pop("max_tokens")
        elif self.llm_config.max_tokens is not None:
            body["max_tokens"] = self.llm_config.max_tokens
        # 剩余 kwargs 透传为请求体扩展。
        body.update(kwargs)
        return body

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        body = self._build_body(messages, kwargs)

        url = self._build_url()
        headers = self._build_headers()
        try:
            resp = self._transport(url, headers, body, timeout=self._timeout)
        except LLMRequestError as exc:
            # 把 transport 抛出的 unknown provider 改写为真实 provider。
            raise LLMRequestError(self.provider, str(exc).replace("[unknown] ", ""), status=exc.status, body=exc.body) from exc

        return self._parse_response(resp)

    # -- 流式 -------------------------------------------------------------

    def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """SSE 流式生成：逐帧 yield ``choices[].delta.content`` 增量。

        ``[DONE]`` 终止；provider 返回的 ``usage`` 记入 ``last_usage``。
        非 200 / 中途断流 / 帧内 ``error`` 抛 ``LLMRequestError``（带 ``status``）。
        """
        body = self._build_body(messages, kwargs)
        body["stream"] = True
        # 兼容实现通常忽略未知字段；调用方可经 kwargs 覆盖。
        body.setdefault("stream_options", {"include_usage": True})
        headers = self._build_headers()
        headers["Accept"] = "text/event-stream"
        url = self._build_url()

        self.last_usage = None
        try:
            yield from self._parse_sse(self._stream_transport(url, headers, body, timeout=self._timeout))
        except LLMRequestError as exc:
            if exc.provider == self.provider:
                raise
            raise LLMRequestError(self.provider, exc.detail, status=exc.status, body=exc.body) from exc

        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage(
                "llm_stream", provider=self.provider, model=body["model"], usage=self.last_usage
            )

    def _parse_sse(self, lines: Iterator[str]) -> Iterator[str]:
        finished = False
        for raw_line in lines:
            line = raw_line.strip()
            # 空行是帧分隔符；以 ":" 开头的是 SSE 注释（keepalive）；其余字段（event:/id:）与内容无关。
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                return
            if not payload:
                continue
            try:
                frame = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise LLMRequestError(self.provider, f"invalid SSE frame: {payload[:200]}") from exc
            if not isinstance(frame, dict):
                raise LLMRequestError(self.provider, f"invalid SSE frame: {payload[:200]}")
            if "error" in frame:
                err = frame["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                raise LLMRequestError(self.provider, f"API error: {msg}", body=frame)
            usage = frame.get("usage")
            if isinstance(usage, dict):
                self.last_usage = usage
            choices = frame.get("choices")
            if not isinstance(choices, list):
                continue
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                content = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(content, str) and content:
                    yield content
                if choice.get("finish_reason"):
                    finished = True
        # 连接被截断但未触发传输层异常：既无 [DONE] 也无 finish_reason 视为中途断流。
        if not finished:
            raise LLMRequestError(self.provider, "stream ended unexpectedly before [DONE]")

    def _parse_response(self, resp: dict[str, Any]) -> ChatResponse:
        if not isinstance(resp, dict):
            raise LLMRequestError(self.provider, f"response must be a JSON object, got {type(resp).__name__}")
        # OpenAI 兼容错误响应：{"error": {"message": ...}}
        if "error" in resp:
            err = resp["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise LLMRequestError(self.provider, f"API error: {msg}", body=resp)
        choices = resp.get("choices")
        if not choices or not isinstance(choices, list):
            raise LLMRequestError(self.provider, f"invalid response: missing 'choices', got {resp!r:.200}", body=resp)
        first = choices[0]
        content = ""
        if isinstance(first, dict):
            msg_obj = first.get("message", {})
            content = msg_obj.get("content", "") if isinstance(msg_obj, dict) else ""
        return ChatResponse(
            content=content,
            model=resp.get("model", self.llm_config.model),
            usage=resp.get("usage"),
            raw=resp,
        )


class OpenAILLM(OpenAICompatibleLLM):
    """OpenAI 原生 API 实现。"""

    provider = "openai"

    def _build_url(self) -> str:
        base = self.llm_config.base_url or "https://api.openai.com/v1"
        return f"{base.rstrip('/')}/chat/completions"


def register_openai_provider() -> None:
    """向 ``LLMFactory`` 注册 OpenAI provider。"""
    from libs.llm.llm_factory import LLMFactory

    @LLMFactory.register("openai")
    def _ctor(llm_config: LLMConfig, **kwargs: Any) -> OpenAILLM:
        return OpenAILLM(llm_config, **kwargs)
