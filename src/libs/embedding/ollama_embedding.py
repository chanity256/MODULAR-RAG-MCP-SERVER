"""Ollama Embedding 实现（阶段 B7.4，本地后端）。

通过 Ollama 原生 HTTP API（``POST {base_url}/api/embed``）调用本地部署的
Embedding 模型（如 ``nomic-embed-text`` / ``mxbai-embed-large``），实现批量向量化。

设计要点：
- 与其它 Embedding 实现一致，采用 **可注入的 ``transport``** 抽象 HTTP 边界。
- 支持批量 ``embed(texts)``：Ollama ``/api/embed`` 原生支持 ``input`` 为列表。
- 输出向量维度由模型决定，接口契约返回 ``list[list[float]]``。
- 空输入 / 超长输入有明确行为（报错或截断，由配置 ``on_too_long`` 决定）。
- 连接失败 / 超时 / 错误响应抛出可读错误（含 provider），不泄露敏感配置。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from core.settings import EmbeddingConfig
from libs.embedding.base_embedding import BaseEmbedding
from libs.embedding.openai_embedding import EmbeddingRequestError

__all__ = ["OllamaEmbedding", "register_ollama_provider"]

Transport = Callable[..., dict[str, Any]]


def _default_transport(url: str, headers: dict[str, str], body: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
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
        raise EmbeddingRequestError("ollama", f"HTTP error: {raw[:200]}", status=status, body=parsed) from exc
    except urllib.error.URLError as exc:
        raise EmbeddingRequestError("ollama", f"network error: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EmbeddingRequestError("ollama", f"non-JSON response: {raw[:200]}", status=status) from exc


class OllamaEmbedding(BaseEmbedding):
    """Ollama 本地 Embedding 实现。"""

    provider = "ollama"

    def __init__(self, emb_config: EmbeddingConfig, *, transport: Transport | None = None, **kwargs: Any):
        self.emb_config = emb_config
        self._transport: Transport = transport or _default_transport
        self._timeout: float | None = kwargs.pop("timeout", None)
        self._extra_kwargs = kwargs

    def _build_url(self) -> str:
        base = self.emb_config.base_url or "http://localhost:11434"
        return f"{base.rstrip('/')}/api/embed"

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self.emb_config.api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _prepare_texts(self, texts: Sequence[str]) -> list[str]:
        if not isinstance(texts, (list, tuple)):
            raise EmbeddingRequestError(self.provider, f"texts must be a list, got {type(texts).__name__}")
        if len(texts) == 0:
            raise EmbeddingRequestError(self.provider, "texts must be non-empty")
        out: list[str] = []
        max_len = self.emb_config.extra.get("max_input_length")
        on_too_long = self.emb_config.extra.get("on_too_long", "error")
        for i, t in enumerate(texts):
            if not isinstance(t, str):
                raise EmbeddingRequestError(self.provider, f"texts[{i}] must be str, got {type(t).__name__}")
            if t == "":
                raise EmbeddingRequestError(self.provider, f"texts[{i}] must be non-empty string")
            if max_len is not None and len(t) > max_len:
                if on_too_long == "truncate":
                    t = t[:max_len]
                else:
                    raise EmbeddingRequestError(
                        self.provider, f"texts[{i}] length {len(t)} exceeds max_input_length={max_len}"
                    )
            out.append(t)
        return out

    def embed(
        self,
        texts: Sequence[str],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[list[float]]:
        prepared = self._prepare_texts(texts)
        body: dict[str, Any] = {
            "model": kwargs.pop("model", self.emb_config.model),
            "input": prepared,
        }
        body.update(kwargs)

        url = self._build_url()
        headers = self._build_headers()
        try:
            resp = self._transport(url, headers, body, timeout=self._timeout)
        except EmbeddingRequestError:
            raise
        except Exception as exc:  # 网络/超时等
            raise EmbeddingRequestError(self.provider, f"{type(exc).__name__}: {exc}") from exc

        return self._parse_response(resp, expected=len(prepared))

    def _parse_response(self, resp: dict[str, Any], *, expected: int) -> list[list[float]]:
        if not isinstance(resp, dict):
            raise EmbeddingRequestError(self.provider, f"response must be a JSON object, got {type(resp).__name__}")
        if "error" in resp:
            raise EmbeddingRequestError(self.provider, f"API error: {resp['error']}", body=resp)
        # 新版 /api/embed 返回 {"embeddings": [[...], ...]}
        embeddings = resp.get("embeddings")
        if embeddings is None:
            # 旧版 /api/embeddings 返回单个 {"embedding": [...]}，仅支持单条。
            single = resp.get("embedding")
            if single is not None:
                embeddings = [single]
        if not isinstance(embeddings, list) or not embeddings:
            raise EmbeddingRequestError(
                self.provider, f"invalid response: missing 'embeddings', got {str(resp)[:200]}", body=resp
            )
        vectors: list[list[float]] = []
        for vec in embeddings:
            if not isinstance(vec, list):
                raise EmbeddingRequestError(self.provider, f"embedding must be a list, got {type(vec).__name__}")
            vectors.append([float(x) for x in vec])
        if len(vectors) != expected:
            raise EmbeddingRequestError(
                self.provider, f"response count {len(vectors)} != input count {expected}", body=resp
            )
        return vectors


def register_ollama_provider() -> None:
    """向 ``EmbeddingFactory`` 注册 Ollama provider。"""
    from libs.embedding.embedding_factory import EmbeddingFactory

    @EmbeddingFactory.register("ollama")
    def _ctor(emb_config: EmbeddingConfig, **kwargs: Any) -> OllamaEmbedding:
        return OllamaEmbedding(emb_config, **kwargs)
