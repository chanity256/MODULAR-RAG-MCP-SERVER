"""Azure OpenAI Embedding 实现（阶段 B7.3）。

复用 ``OpenAICompatibleEmbedding`` 的核心逻辑，仅覆盖 URL / headers 构建：
- URL：``{azure_endpoint}/openai/deployments/{deployment_name}/embeddings?api-version={api_version}``
- 鉴权：``api-key`` 头（非 Bearer）
"""

from __future__ import annotations

from typing import Any

from core.settings import EmbeddingConfig
from libs.embedding.openai_embedding import EmbeddingRequestError, OpenAICompatibleEmbedding

__all__ = ["AzureEmbedding", "register_azure_provider"]


class AzureEmbedding(OpenAICompatibleEmbedding):
    """Azure OpenAI Embedding 实现。"""

    provider = "azure"

    def _build_url(self) -> str:
        endpoint = self.emb_config.azure_endpoint
        if not endpoint:
            raise EmbeddingRequestError(self.provider, "azure_endpoint is required")
        deployment = self.emb_config.extra.get("deployment_name") or self.emb_config.model
        api_version = self.emb_config.api_version or self.emb_config.extra.get("api_version", "2024-10-01-preview")
        base = endpoint.rstrip("/")
        return f"{base}/openai/deployments/{deployment}/embeddings?api-version={api_version}"

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self.emb_config.api_key
        if api_key:
            headers["api-key"] = api_key
        return headers


def register_azure_provider() -> None:
    """向 ``EmbeddingFactory`` 注册 Azure provider。"""
    from libs.embedding.embedding_factory import EmbeddingFactory

    @EmbeddingFactory.register("azure")
    def _ctor(emb_config: EmbeddingConfig, **kwargs: Any) -> AzureEmbedding:
        return AzureEmbedding(emb_config, **kwargs)
