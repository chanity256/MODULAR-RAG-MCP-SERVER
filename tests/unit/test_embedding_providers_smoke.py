"""OpenAI & Azure Embedding provider 冒烟测试（阶段 B7.3，mock HTTP）。

通过注入 fake transport 验证：
- provider=openai/azure 时 ``EmbeddingFactory`` 可创建。
- 请求 URL / headers / body 符合各自规范（Azure 用 api-key 头 + deployment URL）。
- 批量 ``embed(texts)`` 返回与输入等长、顺序一致的向量列表。
- 空输入 / 非字符串 / 超长输入有明确行为（报错或截断）。
- 错误响应抛出可读错误（含 provider）。
- Azure 复用 OpenAI 核心逻辑，行为一致。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.embedding.embedding_factory import EmbeddingFactory
from libs.embedding.openai_embedding import EmbeddingRequestError

_VALID_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
      api_key: sk-test
    vision_llm:
      provider: azure
      model: gpt-4o
    vector_store:
      backend: chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path, **emb_overrides):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if emb_overrides:
        settings = replace(settings, embedding=replace(settings.embedding, **emb_overrides))
    return settings


def _ok_response(vectors: list[list[float]], model: str = "text-embedding-3-small") -> dict:
    return {
        "model": model,
        "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)],
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


class _FakeTransport:
    def __init__(self, response: dict | None = None, raise_exc: Exception | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.last_url = None
        self.last_headers = None
        self.last_body = None

    def __call__(self, url, headers, body, *, timeout=None):
        self.last_url = url
        self.last_headers = headers
        self.last_body = body
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


@pytest.fixture
def registered():
    import libs.embedding  # noqa: F401
    yield EmbeddingFactory


# ---------------------------------------------------------------------------
# 工厂路由
# ---------------------------------------------------------------------------


def test_factory_routes_openai(tmp_path, registered):
    settings = _settings(tmp_path, provider="openai")
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([[0.1, 0.2]])))
    assert emb.__class__.__name__ == "OpenAIEmbedding"


def test_factory_routes_azure(tmp_path, registered):
    settings = _settings(
        tmp_path, provider="azure",
        azure_endpoint="https://example.openai.azure.com",
        api_key="az-key", api_version="2024-10-01-preview",
    )
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([[0.1, 0.2]])))
    assert emb.__class__.__name__ == "AzureEmbedding"


# ---------------------------------------------------------------------------
# OpenAI 请求构建 + 批量 embed
# ---------------------------------------------------------------------------


def test_openai_embed_builds_request_and_parses(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1, 0.2], [0.3, 0.4]]))
    settings = _settings(tmp_path, provider="openai", model="text-embedding-3-small", api_key="sk-test")
    emb = registered.create(settings, transport=transport)
    vecs = emb.embed(["hello", "world"])
    assert vecs == [[0.1, 0.2], [0.3, 0.4]]
    assert transport.last_url == "https://api.openai.com/v1/embeddings"
    assert transport.last_headers["Authorization"] == "Bearer sk-test"
    assert transport.last_body["model"] == "text-embedding-3-small"
    assert transport.last_body["input"] == ["hello", "world"]


def test_openai_custom_base_url(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1]]))
    settings = _settings(tmp_path, provider="openai", base_url="https://proxy.example.com/v1")
    emb = registered.create(settings, transport=transport)
    emb.embed(["hi"])
    assert transport.last_url == "https://proxy.example.com/v1/embeddings"


def test_openai_dimensions_in_body(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1] * 256]))
    settings = _settings(tmp_path, provider="openai", dimensions=256)
    emb = registered.create(settings, transport=transport)
    emb.embed(["hi"])
    assert transport.last_body["dimensions"] == 256


def test_embed_one_helper_with_provider(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1, 0.2, 0.3]]))
    settings = _settings(tmp_path, provider="openai")
    emb = registered.create(settings, transport=transport)
    vec = emb.embed_one("hi")
    assert vec == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# Azure 请求构建（复用 OpenAI 核心逻辑）
# ---------------------------------------------------------------------------


def test_azure_embed_builds_azure_specific_url_and_headers(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1, 0.2]]))
    settings = _settings(
        tmp_path, provider="azure",
        azure_endpoint="https://example.openai.azure.com",
        api_key="az-key", api_version="2024-10-01-preview",
    )
    # 用 extra 提供 deployment_name
    settings = replace(
        settings,
        embedding=replace(settings.embedding, extra={"deployment_name": "text-embedding-deploy"}),
    )
    emb = registered.create(settings, transport=transport)
    emb.embed(["hi"])
    url = transport.last_url
    assert "example.openai.azure.com" in url
    assert "/openai/deployments/text-embedding-deploy/embeddings" in url
    assert "api-version=2024-10-01-preview" in url
    assert transport.last_headers["api-key"] == "az-key"
    assert "Authorization" not in transport.last_headers


def test_azure_uses_model_as_deployment_when_no_deployment_name(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1]]))
    settings = _settings(
        tmp_path, provider="azure",
        azure_endpoint="https://example.openai.azure.com",
        api_key="az-key", api_version="2024-10-01-preview",
        model="text-embedding-ada-002",
    )
    emb = registered.create(settings, transport=transport)
    emb.embed(["hi"])
    assert "/openai/deployments/text-embedding-ada-002/embeddings" in transport.last_url


def test_azure_missing_endpoint_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="azure", api_key="az-key")
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([[0.1]])))
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["hi"])
    assert "azure" in str(exc_info.value)
    assert "azure_endpoint" in str(exc_info.value)


def test_azure_behavior_consistent_with_openai(tmp_path, registered):
    """Azure 与 OpenAI 对同一响应解析行为一致（返回顺序与维度）。"""
    transport = _FakeTransport(_ok_response([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]))
    settings = _settings(
        tmp_path, provider="azure",
        azure_endpoint="https://example.openai.azure.com",
        api_key="az-key", api_version="2024-10-01-preview",
    )
    emb = registered.create(settings, transport=transport)
    vecs = emb.embed(["a", "b", "c"])
    assert vecs == [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]


# ---------------------------------------------------------------------------
# 输入校验 + 错误处理
# ---------------------------------------------------------------------------


def test_empty_texts_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="openai")
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([])))
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed([])
    assert "openai" in str(exc_info.value)
    assert "non-empty" in str(exc_info.value)


def test_non_string_input_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="openai")
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([[0.1]])))
    with pytest.raises(EmbeddingRequestError):
        emb.embed(["ok", 123])  # type: ignore[list-item]


def test_empty_string_input_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="openai")
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([[0.1]])))
    with pytest.raises(EmbeddingRequestError):
        emb.embed([""])


def test_too_long_input_raises_by_default(tmp_path, registered):
    settings = _settings(tmp_path, provider="openai")
    settings = replace(
        settings, embedding=replace(settings.embedding, extra={"max_input_length": 5}),
    )
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([[0.1]])))
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["abcdefgh"])  # len 8 > 5
    assert "max_input_length" in str(exc_info.value)


def test_too_long_input_truncate_strategy(tmp_path, registered):
    settings = _settings(tmp_path, provider="openai")
    settings = replace(
        settings,
        embedding=replace(settings.embedding, extra={"max_input_length": 5, "on_too_long": "truncate"}),
    )
    transport = _FakeTransport(_ok_response([[0.1]]))
    emb = registered.create(settings, transport=transport)
    emb.embed(["abcdefgh"])
    assert transport.last_body["input"] == ["abcde"]  # 截断到 5


def test_api_error_response_raises_readable(tmp_path, registered):
    err_resp = {"error": {"message": "invalid_api_key"}}
    settings = _settings(tmp_path, provider="openai", api_key="bad")
    emb = registered.create(settings, transport=_FakeTransport(err_resp))
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["hi"])
    assert "openai" in str(exc_info.value)
    assert "invalid_api_key" in str(exc_info.value)


def test_malformed_response_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="openai")
    emb = registered.create(settings, transport=_FakeTransport({"foo": "bar"}))
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["hi"])
    assert "data" in str(exc_info.value)


def test_response_count_mismatch_raises(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1]]))  # 只返回 1 个向量
    settings = _settings(tmp_path, provider="openai")
    emb = registered.create(settings, transport=transport)
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["a", "b"])  # 输入 2 个
    assert "count" in str(exc_info.value)


def test_response_reordered_by_index(tmp_path, registered):
    """data 顺序打乱时按 index 还原输入顺序。"""
    resp = {
        "data": [
            {"index": 1, "embedding": [0.3, 0.4]},
            {"index": 0, "embedding": [0.1, 0.2]},
        ]
    }
    transport = _FakeTransport(resp)
    settings = _settings(tmp_path, provider="openai")
    emb = registered.create(settings, transport=transport)
    vecs = emb.embed(["a", "b"])
    assert vecs == [[0.1, 0.2], [0.3, 0.4]]
