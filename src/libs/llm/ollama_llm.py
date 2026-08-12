"""Ollama LLM 实现（阶段 B7.2，本地后端）。

通过 Ollama 原生 HTTP API（``POST {base_url}/api/chat``）调用本地部署的模型。

设计要点：
- 与 OpenAI-compatible 实现一致，采用 **可注入的 ``transport``** 抽象 HTTP 边界，
  生产用 stdlib ``urllib`` 默认传输，测试注入 fake transport。
- 错误信息包含 provider（``ollama``）与错误类型，**不泄露敏感配置**（如 api_key）。
- 连接失败 / 超时等场景抛出可读错误，便于上层降级。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from core.settings import LLMConfig
from libs.llm.base_llm import BaseLLM, ChatMessage, ChatResponse
from libs.llm.openai_llm import LLMRequestError

__all__ = ["OllamaLLM", "register_ollama_provider"]

Transport = Callable[..., dict[str, Any]]


def _default_transport(url: str, headers: dict[str, str], body: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    """默认 HTTP 传输：用 stdlib urllib 发送 POST，返回解析后的 JSON。"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 受控的本地/配置 URL
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        raise LLMRequestError("ollama", f"HTTP error: {raw[:200]}", status=status, body=parsed) from exc
    except urllib.error.URLError as exc:
        raise LLMRequestError("ollama", f"network error: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMRequestError("ollama", f"non-JSON response: {raw[:200]}", status=status) from exc


class OllamaLLM(BaseLLM):
    """Ollama 本地模型 LLM 实现。"""

    provider = "ollama"

    def __init__(self, llm_config: LLMConfig, *, transport: Transport | None = None, **kwargs: Any):
        self.llm_config = llm_config
        self._transport: Transport = transport or _default_transport
        self._timeout: float | None = kwargs.pop("timeout", None)
        self._extra_kwargs = kwargs

    def _build_url(self) -> str:
        base = self.llm_config.base_url or "http://localhost:11434"
        return f"{base.rstrip('/')}/api/chat"

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # Ollama 可选 Bearer token 鉴权（不回显到错误信息中）。
        api_key = self.llm_config.api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if not messages:
            raise LLMRequestError(self.provider, "messages must be non-empty")
        for m in messages:
            if not isinstance(m, ChatMessage):
                raise LLMRequestError(self.provider, f"each message must be ChatMessage, got {type(m).__name__}")
            if not m.role or not m.content:
                raise LLMRequestError(self.provider, "message role/content must be non-empty")

        options: dict[str, Any] = {}
        if "temperature" in kwargs:
            options["temperature"] = kwargs.pop("temperature")
        elif self.llm_config.temperature is not None:
            options["temperature"] = self.llm_config.temperature

        body: dict[str, Any] = {
            "model": kwargs.pop("model", self.llm_config.model),
            "messages": [m.to_dict() for m in messages],
            "stream": False,
        }
        if options:
            body["options"] = options
        # 剩余 kwargs 透传为请求体扩展。
        body.update(kwargs)

        url = self._build_url()
        headers = self._build_headers()
        try:
            resp = self._transport(url, headers, body, timeout=self._timeout)
        except LLMRequestError:
            raise  # 已带 provider 信息
        except Exception as exc:  # 网络/超时等
            raise LLMRequestError(self.provider, f"{type(exc).__name__}: {exc}") from exc

        return self._parse_response(resp)

    def _parse_response(self, resp: dict[str, Any]) -> ChatResponse:
        if not isinstance(resp, dict):
            raise LLMRequestError(self.provider, f"response must be a JSON object, got {type(resp).__name__}")
        # Ollama 错误响应：{"error": "..."}
        if "error" in resp:
            raise LLMRequestError(self.provider, f"API error: {resp['error']}", body=resp)
        message = resp.get("message")
        content = ""
        if isinstance(message, dict):
            content = message.get("content", "")
        elif isinstance(message, str):
            content = message
        if not content and "response" in resp:  # /api/generate 兼容
            content = resp.get("response", "")
        if not content and "message" not in resp and "response" not in resp:
            raise LLMRequestError(
                self.provider,
                f"invalid response: missing 'message'/'response', got {str(resp)[:200]}",
                body=resp,
            )
        return ChatResponse(
            content=content,
            model=resp.get("model", self.llm_config.model),
            usage=None,
            raw=resp,
        )


def register_ollama_provider() -> None:
    """向 ``LLMFactory`` 注册 Ollama provider。"""
    from libs.llm.llm_factory import LLMFactory

    @LLMFactory.register("ollama")
    def _ctor(llm_config: LLMConfig, **kwargs: Any) -> OllamaLLM:
        return OllamaLLM(llm_config, **kwargs)
