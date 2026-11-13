"""``OllamaEmbedding`` 冒烟测试（阶段 B7.4，mock HTTP）。

通过注入 fake transport 验证：
- provider=ollama 时 ``EmbeddingFactory`` 可创建。
- 请求 URL / body 符合 Ollama 原生 ``/api/embed`` 规范（批量 input）。
- 批量 ``embed(texts)`` 返回与输入等长、顺序一致的向量列表。
- 空输入 / 非字符串 / 超长输入有明确行为。
- 正常响应、连接失败、超时、错误响应等场景覆盖。
- 不泄露 api_key。
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
      provider: ollama
      model: llama3
    embedding:
      provider: ollama
      model: nomic-embed-text
      base_url: http://localhost:11434
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


def _ok_response(vectors: list[list[float]], model: str = "nomic-embed-text") -> dict:
    return {"model": model, "embeddings": vectors}


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
# 工厂创建 + 请求构建
# ---------------------------------------------------------------------------


def test_factory_creates_ollama(tmp_path, registered):
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([[0.1, 0.2]])))
    assert emb.__class__.__name__ == "OllamaEmbedding"


def test_ollama_embed_builds_native_request(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1, 0.2], [0.3, 0.4]], model="nomic-embed-text"))
    settings = _settings(tmp_path, provider="ollama", model="nomic-embed-text", base_url="http://localhost:11434")
    emb = registered.create(settings, transport=transport)
    vecs = emb.embed(["hello", "world"])
    assert vecs == [[0.1, 0.2], [0.3, 0.4]]
    assert transport.last_url == "http://localhost:11434/api/embed"
    assert transport.last_body["model"] == "nomic-embed-text"
    assert transport.last_body["input"] == ["hello", "world"]


def test_ollama_custom_base_url(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1]]))
    settings = _settings(tmp_path, provider="ollama", base_url="http://gpu-box:11434")
    emb = registered.create(settings, transport=transport)
    emb.embed(["hi"])
    assert transport.last_url == "http://gpu-box:11434/api/embed"


def test_ollama_default_base_url_when_unspecified(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1]]))
    settings = _settings(tmp_path, provider="ollama", base_url=None)
    emb = registered.create(settings, transport=transport)
    emb.embed(["hi"])
    assert transport.last_url == "http://localhost:11434/api/embed"


def test_ollama_vector_dimension_from_model(tmp_path, registered):
    """输出向量维度由模型决定（nomic-embed-text 为 768 维示例）。"""
    dim = 768
    transport = _FakeTransport(_ok_response([[0.01] * dim]))
    settings = _settings(tmp_path, provider="ollama", model="nomic-embed-text")
    emb = registered.create(settings, transport=transport)
    vecs = emb.embed(["hi"])
    assert len(vecs) == 1
    assert len(vecs[0]) == dim


def test_ollama_batch_embed_order_preserved(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[1.0], [2.0], [3.0]]))
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=transport)
    vecs = emb.embed(["a", "b", "c"])
    assert vecs == [[1.0], [2.0], [3.0]]


def test_ollama_embed_one_helper(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1, 0.2, 0.3]]))
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=transport)
    vec = emb.embed_one("hi")
    assert vec == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# 兼容旧版 /api/embeddings 单条响应
# ---------------------------------------------------------------------------


def test_ollama_legacy_single_embedding_response(tmp_path, registered):
    transport = _FakeTransport({"embedding": [0.1, 0.2]})
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=transport)
    vecs = emb.embed(["hi"])
    assert vecs == [[0.1, 0.2]]


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


def test_empty_texts_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([])))
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed([])
    assert "ollama" in str(exc_info.value)


def test_non_string_input_raises(tmp_path, registered):
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([[0.1]])))
    with pytest.raises(EmbeddingRequestError):
        emb.embed(["ok", 123])  # type: ignore[list-item]


def test_too_long_input_raises_by_default(tmp_path, registered):
    settings = _settings(tmp_path, provider="ollama")
    settings = replace(settings, embedding=replace(settings.embedding, extra={"max_input_length": 5}))
    emb = registered.create(settings, transport=_FakeTransport(_ok_response([[0.1]])))
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["abcdefgh"])
    assert "max_input_length" in str(exc_info.value)


def test_too_long_input_truncate_strategy(tmp_path, registered):
    settings = _settings(tmp_path, provider="ollama")
    settings = replace(
        settings,
        embedding=replace(settings.embedding, extra={"max_input_length": 5, "on_too_long": "truncate"}),
    )
    transport = _FakeTransport(_ok_response([[0.1]]))
    emb = registered.create(settings, transport=transport)
    emb.embed(["abcdefgh"])
    assert transport.last_body["input"] == ["abcde"]


# ---------------------------------------------------------------------------
# 错误场景：连接失败 / 超时 / 错误响应 / 不泄露 api_key
# ---------------------------------------------------------------------------


def test_api_error_raises_readable(tmp_path, registered):
    transport = _FakeTransport({"error": "model 'nomic-embed-text' not found"})
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=transport)
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["hi"])
    assert "ollama" in str(exc_info.value)
    assert "not found" in str(exc_info.value)


def test_malformed_response_raises(tmp_path, registered):
    transport = _FakeTransport({"foo": "bar"})
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=transport)
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["hi"])
    assert "ollama" in str(exc_info.value)
    assert "embeddings" in str(exc_info.value)


def test_connection_failure_raises_readable(tmp_path, registered):
    transport = _FakeTransport(raise_exc=ConnectionError("Connection refused"))
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=transport)
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["hi"])
    assert "ollama" in str(exc_info.value)
    assert "Connection refused" in str(exc_info.value)


def test_timeout_raises_readable(tmp_path, registered):
    import socket

    transport = _FakeTransport(raise_exc=socket.timeout("timed out"))
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=transport)
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["hi"])
    assert "ollama" in str(exc_info.value)


def test_error_does_not_leak_api_key(tmp_path, registered):
    transport = _FakeTransport(raise_exc=ConnectionError("boom"))
    settings = _settings(tmp_path, provider="ollama", api_key="super-secret-key")
    emb = registered.create(settings, transport=transport)
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["hi"])
    assert "super-secret-key" not in str(exc_info.value)


def test_response_count_mismatch_raises(tmp_path, registered):
    transport = _FakeTransport(_ok_response([[0.1]]))
    settings = _settings(tmp_path, provider="ollama")
    emb = registered.create(settings, transport=transport)
    with pytest.raises(EmbeddingRequestError) as exc_info:
        emb.embed(["a", "b"])
    assert "count" in str(exc_info.value)
