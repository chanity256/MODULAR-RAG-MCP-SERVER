"""LLM 抽象层包。

导入本包即触发各 provider 的注册（``openai`` / ``azure`` / ``deepseek``）。
"""

from libs.llm.base_llm import BaseLLM, ChatMessage, ChatResponse
from libs.llm.llm_factory import LLMFactory

# 触发 provider 注册（顺序无关，注册到 LLMFactory 全局注册表）。
from libs.llm import azure_llm as _azure_llm  # noqa: F401
from libs.llm import deepseek_llm as _deepseek_llm  # noqa: F401
from libs.llm import ollama_llm as _ollama_llm  # noqa: F401
from libs.llm import openai_llm as _openai_llm  # noqa: F401

_azure_llm.register_azure_provider()
_deepseek_llm.register_deepseek_provider()
_ollama_llm.register_ollama_provider()
_openai_llm.register_openai_provider()

__all__ = ["BaseLLM", "ChatMessage", "ChatResponse", "LLMFactory"]
