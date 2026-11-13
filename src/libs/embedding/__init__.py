"""Embedding 抽象层包。

导入本包即触发各 provider 的注册（``openai`` / ``azure``）。
"""

from libs.embedding.base_embedding import BaseEmbedding
from libs.embedding.embedding_factory import EmbeddingFactory

# 触发 provider 注册。
from libs.embedding import azure_embedding as _azure_embedding  # noqa: F401
from libs.embedding import ollama_embedding as _ollama_embedding  # noqa: F401
from libs.embedding import openai_embedding as _openai_embedding  # noqa: F401

_azure_embedding.register_azure_provider()
_ollama_embedding.register_ollama_provider()
_openai_embedding.register_openai_provider()

__all__ = ["BaseEmbedding", "EmbeddingFactory"]
