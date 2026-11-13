"""Azure OpenAI LLM 实现（阶段 B7.1）。

Azure 使用专属的 endpoint / api-version / deployment_name 配置，
请求路径与鉴权头与 OpenAI 原生不同，但响应体兼容。
"""

from __future__ import annotations

from typing import Any

from core.settings import LLMConfig
from libs.llm.openai_llm import OpenAICompatibleLLM

__all__ = ["AzureLLM", "register_azure_provider"]


class AzureLLM(OpenAICompatibleLLM):
    """Azure OpenAI 实现（GPT-4o 等）。"""

    provider = "azure"

    def _build_url(self) -> str:
        endpoint = self.llm_config.azure_endpoint
        if not endpoint:
            from libs.llm.openai_llm import LLMRequestError
            raise LLMRequestError(self.provider, "azure_endpoint is required")
        deployment = self.llm_config.deployment_name or self.llm_config.model
        api_version = self.llm_config.extra.get("api_version", "2024-10-01-preview")
        base = endpoint.rstrip("/")
        return f"{base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self.llm_config.api_key
        if api_key:
            headers["api-key"] = api_key
        return headers


def register_azure_provider() -> None:
    """向 ``LLMFactory`` 注册 Azure provider。"""
    from libs.llm.llm_factory import LLMFactory

    @LLMFactory.register("azure")
    def _ctor(llm_config: LLMConfig, **kwargs: Any) -> AzureLLM:
        return AzureLLM(llm_config, **kwargs)
