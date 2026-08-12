"""DeepSeek LLM 实现（阶段 B7.1）。

DeepSeek 提供 OpenAI 兼容 API，仅默认 base_url 不同。
"""

from __future__ import annotations

from typing import Any

from core.settings import LLMConfig
from libs.llm.openai_llm import OpenAICompatibleLLM

__all__ = ["DeepSeekLLM", "register_deepseek_provider"]


class DeepSeekLLM(OpenAICompatibleLLM):
    """DeepSeek 实现（OpenAI 兼容）。"""

    provider = "deepseek"

    def _build_url(self) -> str:
        base = self.llm_config.base_url or "https://api.deepseek.com/v1"
        return f"{base.rstrip('/')}/chat/completions"


def register_deepseek_provider() -> None:
    """向 ``LLMFactory`` 注册 DeepSeek provider。"""
    from libs.llm.llm_factory import LLMFactory

    @LLMFactory.register("deepseek")
    def _ctor(llm_config: LLMConfig, **kwargs: Any) -> DeepSeekLLM:
        return DeepSeekLLM(llm_config, **kwargs)
