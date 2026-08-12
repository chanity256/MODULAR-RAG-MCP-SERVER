"""Azure Vision LLM 实现（阶段 B9）。

通过 Azure OpenAI 调用 GPT-4o / GPT-4-Vision-Preview 进行图像理解。

设计要点：
- 复用 ``BaseVisionLLM`` 的图片加载 / base64 编码 / 预处理扩展点。
- 通过 **可注入的 ``transport``** 抽象 HTTP 边界（同其它 LLM 实现）。
- Azure 特有配置：``azure_endpoint`` / ``api_version`` / ``deployment_name`` / ``api_key``。
- 请求体使用 OpenAI Vision 消息格式（``content`` 为 ``[{type:text}, {type:image_url}]`` 数组）。
- 图片过大时自动压缩至 ``max_image_size``（默认 2048px），Pillow 可用时实际压缩，
  不可用时优雅降级（no-op）。
- API 调用失败抛出 ``LLMRequestError``（含 provider + Azure 错误码）。
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from core.settings import VisionLLMConfig
from libs.llm.base_llm import BaseLLM, ChatMessage, ChatResponse
from libs.llm.base_vision_llm import BaseVisionLLM
from libs.llm.openai_llm import LLMRequestError

__all__ = ["AzureVisionLLM", "register_azure_vision_provider"]

Transport = Callable[..., dict[str, Any]]


def _default_transport(url: str, headers: dict[str, str], body: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    """默认 HTTP 传输：用 stdlib urllib 发送 POST，返回解析后的 JSON。"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        raise LLMRequestError("azure-vision", f"HTTP error: {raw[:200]}", status=status, body=parsed) from exc
    except urllib.error.URLError as exc:
        raise LLMRequestError("azure-vision", f"network error: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMRequestError("azure-vision", f"non-JSON response: {raw[:200]}", status=status) from exc


class AzureVisionLLM(BaseVisionLLM):
    """Azure OpenAI Vision LLM 实现（GPT-4o / GPT-4-Vision）。"""

    provider = "azure"

    def __init__(self, vision_config: VisionLLMConfig, *, transport: Transport | None = None, **kwargs: Any):
        self.vision_config = vision_config
        self._transport: Transport = transport or _default_transport
        self._timeout: float | None = kwargs.pop("timeout", None)
        self._extra_kwargs = kwargs

    # -- Azure 特有配置 ---------------------------------------------------

    def _build_url(self) -> str:
        endpoint = self.vision_config.azure_endpoint
        if not endpoint:
            raise LLMRequestError(self.provider, "azure_endpoint is required for vision LLM")
        deployment = self.vision_config.deployment_name or self.vision_config.model
        api_version = self.vision_config.api_version or "2024-10-01-preview"
        base = endpoint.rstrip("/")
        return f"{base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self.vision_config.api_key
        if api_key:
            headers["api-key"] = api_key
        return headers

    # -- 图片预处理（压缩） -----------------------------------------------

    def _preprocess_image(self, image_bytes: bytes, max_size: int, media_type: str) -> bytes:
        """图片预处理：Pillow 可用时按 max_size 压缩，不可用时 no-op。"""
        try:
            from PIL import Image  # type: ignore[import-untyped]

            img = Image.open(io.BytesIO(image_bytes))
            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                fmt = img.format or ("PNG" if "png" in media_type else "JPEG")
                img.save(buf, format=fmt)
                return buf.getvalue()
        except ImportError:
            pass  # Pillow 不可用，优雅降级
        except Exception:
            pass  # 预处理失败，使用原图
        return image_bytes

    # -- 文本 chat（继承自 BaseLLM） ---------------------------------------

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if not messages:
            raise LLMRequestError(self.provider, "messages must be non-empty")

        body: dict[str, Any] = {
            "model": kwargs.pop("model", self.vision_config.model),
            "messages": [m.to_dict() for m in messages],
        }
        body.update(kwargs)

        url = self._build_url()
        headers = self._build_headers()
        try:
            resp = self._transport(url, headers, body, timeout=self._timeout)
        except LLMRequestError as exc:
            raise LLMRequestError(
                self.provider, str(exc).replace("[azure-vision] ", ""), status=exc.status, body=exc.body
            ) from exc
        return self._parse_response(resp)

    # -- Vision chat_with_image -------------------------------------------

    def chat_with_image(
        self,
        text: str,
        image: str | Any,
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if not text:
            raise LLMRequestError(self.provider, "text prompt must be non-empty")

        # 加载 + 预处理 + base64 编码图片
        max_image_size = kwargs.pop("max_image_size", self.vision_config.max_image_size)
        b64, media_type = self.prepare_image(image, max_size=max_image_size)

        # 构建 OpenAI Vision 消息格式
        data_url = f"data:{media_type};base64,{b64}"
        body: dict[str, Any] = {
            "model": kwargs.pop("model", self.vision_config.model),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        }
        if "max_tokens" in kwargs:
            body["max_tokens"] = kwargs.pop("max_tokens")
        body.update(kwargs)

        url = self._build_url()
        headers = self._build_headers()
        try:
            resp = self._transport(url, headers, body, timeout=self._timeout)
        except LLMRequestError as exc:
            raise LLMRequestError(
                self.provider, str(exc).replace("[azure-vision] ", ""), status=exc.status, body=exc.body
            ) from exc
        return self._parse_response(resp)

    # -- 响应解析 ---------------------------------------------------------

    def _parse_response(self, resp: dict[str, Any]) -> ChatResponse:
        if not isinstance(resp, dict):
            raise LLMRequestError(self.provider, f"response must be a JSON object, got {type(resp).__name__}")
        if "error" in resp:
            err = resp["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            code = err.get("code", "") if isinstance(err, dict) else ""
            detail = f"API error: {msg}"
            if code:
                detail += f" (code={code})"
            raise LLMRequestError(self.provider, detail, body=resp)
        choices = resp.get("choices")
        if not choices or not isinstance(choices, list):
            raise LLMRequestError(self.provider, f"invalid response: missing 'choices', got {str(resp)[:200]}", body=resp)
        first = choices[0]
        content = ""
        if isinstance(first, dict):
            msg_obj = first.get("message", {})
            content = msg_obj.get("content", "") if isinstance(msg_obj, dict) else ""
        return ChatResponse(
            content=content,
            model=resp.get("model", self.vision_config.model),
            usage=resp.get("usage"),
            raw=resp,
        )


def register_azure_vision_provider() -> None:
    """向 ``LLMFactory`` 注册 Azure Vision provider。"""
    from libs.llm.llm_factory import LLMFactory

    @LLMFactory.register_vision("azure")
    def _ctor(vision_config: VisionLLMConfig, **kwargs: Any) -> AzureVisionLLM:
        return AzureVisionLLM(vision_config, **kwargs)
