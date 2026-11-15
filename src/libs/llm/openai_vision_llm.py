"""OpenAI-compatible Vision LLM 实现（G14 收口补强）。

面向任何 OpenAI 兼容视觉端点（智谱 GLM-4V/GLM-5 系列、DashScope 兼容模式、
Ollama OpenAI 路由等），经 ``POST {base_url}/chat/completions`` + Bearer
认证调用，请求体为 OpenAI Vision 消息格式（与 AzureVisionLLM 一致）。

设计要点：
- 继承 ``AzureVisionLLM``（B9），仅覆盖 Azure 特有的 URL 拼接与认证头；
  图片加载 / 预处理压缩 / payload 构建 / 响应解析全部复用。
- ``base_url`` 必填（VisionLLMConfig 已有字段，与 embedding 同款配置方式）。
- ``api_key`` 可选：本地网关等场景允许匿名。
- 注册名为 ``openai``：任何 OpenAI 兼容视觉端点都走这一个实现，
  通过 ``base_url`` / ``model`` 配置区分具体服务。
"""

from __future__ import annotations

from typing import Any

from libs.llm.azure_vision_llm import AzureVisionLLM, Transport, _default_transport
from libs.llm.openai_llm import LLMRequestError

__all__ = ["OpenAICompatibleVisionLLM", "register_openai_vision_provider"]


def _openai_vision_transport(
    url: str, headers: dict[str, str], body: dict[str, Any], *, timeout: float | None = None
) -> dict[str, Any]:
    """默认传输：复用 Azure 的 urllib 实现，仅把错误标签换成本 provider。"""
    try:
        return _default_transport(url, headers, body, timeout=timeout)
    except LLMRequestError as exc:
        raise LLMRequestError(
            "openai-vision",
            str(exc).replace("[azure-vision] ", ""),
            status=exc.status,
            body=exc.body,
        ) from exc


class OpenAICompatibleVisionLLM(AzureVisionLLM):
    """OpenAI 兼容 Vision LLM 实现（GLM / DashScope 兼容端点 / 本地网关等）。"""

    # Note: 为何继承 AzureVisionLLM 而非独立实现 — 见 .agents/notes/implemented/feature/2026-10-05-openai-compatible-vision-provider.md
    provider = "openai"

    def __init__(self, vision_config: Any, *, transport: Transport | None = None, **kwargs: Any):
        super().__init__(
            vision_config,
            transport=transport or _openai_vision_transport,
            **kwargs,
        )

    # -- 覆盖 Azure 特有的 URL 与认证 -------------------------------------

    def _build_url(self) -> str:
        base = self.vision_config.base_url
        if not base:
            raise LLMRequestError(
                self.provider, "base_url is required for openai-compatible vision LLM"
            )
        return base.rstrip("/") + "/chat/completions"

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.vision_config.api_key:
            headers["Authorization"] = f"Bearer {self.vision_config.api_key}"
        return headers


def register_openai_vision_provider() -> None:
    """向 ``LLMFactory`` 注册 openai-compatible Vision provider。"""
    from libs.llm.llm_factory import LLMFactory

    @LLMFactory.register_vision("openai")
    def _ctor(vision_config: Any, **kwargs: Any) -> OpenAICompatibleVisionLLM:
        return OpenAICompatibleVisionLLM(vision_config, **kwargs)
