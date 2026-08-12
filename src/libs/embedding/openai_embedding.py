"""OpenAI-Compatible Embedding 实现（阶段 B7.3）。

覆盖 OpenAI 官方 API 与 Azure OpenAI 服务的 Embedding 调用。

设计要点：
- ``OpenAICompatibleEmbedding`` 为基类，封装请求构建 / 响应解析 / 可读错误。
- 通过 **可注入的 ``transport``** 抽象 HTTP 边界（同 LLM 实现），测试注入 fake transport。
- 空输入 / 超长输入有明确行为（报错或截断，由配置 ``on_too_long`` 决定）。
- Azure 实现复用本基类的核心逻辑，仅覆盖 URL / headers 构建。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from core.settings import EmbeddingConfig
from libs.embedding.base_embedding import BaseEmbedding

__all__ = ["EmbeddingRequestError", "OpenAICompatibleEmbedding", "OpenAIEmbedding", "register_openai_provider"]

Transport = Callable[..., dict[str, Any]]


class EmbeddingRequestError(RuntimeError):
    """Embedding HTTP 请求错误，包含 provider 与错误类型。"""

    def __init__(self, provider: str, message: str, *, status: int | None = None, body: Any = None):
        self.provider = provider
        self.status = status
        self.body = body
        parts = [f"[{provider}]"]
        if status is not None:
            parts.append(f"status={status}")
        parts.append(message)
        super().__init__(" ".join(parts))


def _default_transport(url: str, headers: dict[str, str], body: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 受控的 provider URL
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        raise EmbeddingRequestError("unknown", f"HTTP error: {raw[:200]}", status=status, body=parsed) from exc
    except urllib.error.URLError as exc:
        raise EmbeddingRequestError("unknown", f"network error: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EmbeddingRequestError("unknown", f"non-JSON response: {raw[:200]}", status=status) from exc


class OpenAICompatibleEmbedding(BaseEmbedding):
    """OpenAI 兼容 Embedding 基类。"""

    provider = "openai-compatible"

    def __init__(self, emb_config: EmbeddingConfig, *, transport: Transport | None = None, **kwargs: Any):
        self.emb_config = emb_config
        self._transport: Transport = transport or _default_transport
        self._timeout: float | None = kwargs.pop("timeout", None)
        self._extra_kwargs = kwargs

    # -- 子类覆盖点 -------------------------------------------------------

    def _build_url(self) -> str:
        base = self.emb_config.base_url or "https://api.openai.com/v1"
        return f"{base.rstrip('/')}/embeddings"

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self.emb_config.api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    # -- 输入预处理 -------------------------------------------------------

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

    # -- 主流程 -----------------------------------------------------------

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
        if self.emb_config.dimensions is not None:
            body["dimensions"] = self.emb_config.dimensions
        body.update(kwargs)

        url = self._build_url()
        headers = self._build_headers()
        try:
            resp = self._transport(url, headers, body, timeout=self._timeout)
        except EmbeddingRequestError as exc:
            raise EmbeddingRequestError(
                self.provider, str(exc).replace("[unknown] ", ""), status=exc.status, body=exc.body
            ) from exc

        return self._parse_response(resp, expected=len(prepared))

    def _parse_response(self, resp: dict[str, Any], *, expected: int) -> list[list[float]]:
        if not isinstance(resp, dict):
            raise EmbeddingRequestError(self.provider, f"response must be a JSON object, got {type(resp).__name__}")
        if "error" in resp:
            err = resp["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise EmbeddingRequestError(self.provider, f"API error: {msg}", body=resp)
        data = resp.get("data")
        if not isinstance(data, list) or not data:
            raise EmbeddingRequestError(self.provider, f"invalid response: missing 'data', got {str(resp)[:200]}", body=resp)
        indexed: list[tuple[int, list[float]]] = []
        for item in data:
            if not isinstance(item, dict) or "embedding" not in item or "index" not in item:
                raise EmbeddingRequestError(self.provider, f"invalid data item: {str(item)[:200]}", body=resp)
            vec = item["embedding"]
            if not isinstance(vec, list):
                raise EmbeddingRequestError(self.provider, f"embedding must be a list, got {type(vec).__name__}")
            indexed.append((int(item["index"]), [float(x) for x in vec]))
        indexed.sort(key=lambda pair: pair[0])
        vectors = [v for _, v in indexed]
        if len(vectors) != expected:
            raise EmbeddingRequestError(
                self.provider, f"response count {len(vectors)} != input count {expected}", body=resp
            )
        return vectors


class OpenAIEmbedding(OpenAICompatibleEmbedding):
    """OpenAI 原生 Embedding 实现。"""

    provider = "openai"

    def _build_url(self) -> str:
        base = self.emb_config.base_url or "https://api.openai.com/v1"
        return f"{base.rstrip('/')}/embeddings"


def register_openai_provider() -> None:
    """向 ``EmbeddingFactory`` 注册 OpenAI provider。"""
    from libs.embedding.embedding_factory import EmbeddingFactory

    @EmbeddingFactory.register("openai")
    def _ctor(emb_config: EmbeddingConfig, **kwargs: Any) -> OpenAIEmbedding:
        return OpenAIEmbedding(emb_config, **kwargs)
