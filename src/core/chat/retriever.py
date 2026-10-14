"""Chat 检索协议（WC6.4，WEBUI_CHAT_SPEC D8）。

``src/core/chat/`` 不得依赖 ``mcp_server`` / ``observability``，因此检索能力以协议形式注入；
hybrid / agentic 两个实现位于应用服务层（``services/chat_retrievers.py``），此处不含任何实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.types import RetrievalResult

__all__ = [
    "ChatRetrievalResult",
    "ChatRetriever",
    "DeepDisabledError",
    "RETRIEVAL_HYBRID",
    "RETRIEVAL_AGENTIC_FAST",
    "RETRIEVAL_AGENTIC_DEEP",
    "RETRIEVAL_MODES",
]

RETRIEVAL_HYBRID = "hybrid"
RETRIEVAL_AGENTIC_FAST = "agentic_fast"
RETRIEVAL_AGENTIC_DEEP = "agentic_deep"
RETRIEVAL_MODES = (RETRIEVAL_HYBRID, RETRIEVAL_AGENTIC_FAST, RETRIEVAL_AGENTIC_DEEP)


class DeepDisabledError(Exception):
    """DEEP 检索被 ``agentic_search.allow_deep`` 门控拒绝；ChatEngine 映射为 ``DEEP_DISABLED``，不重试、不降级。"""


@dataclass
class ChatRetrievalResult:
    """一次检索的产物。

    Attributes:
        results: 检索结果（hybrid 路径供 ``AnswerGenerator`` 生成答案）。
        citations: ``Citation.to_dict()`` 形态的引用列表。hybrid 路径下 ChatEngine 以
            ``AnswerGenerator.citations(results)`` 为准（编号须与上下文同源同序，且受
            ``max_context_chunks`` 截断），此字段仅 agentic 路径使用。
        answer: sidecar 已生成的答案；非 ``None`` 时 ChatEngine 不再调用 ``AnswerGenerator``，
            整块作为一个 ``stream`` 帧下发。
        metadata: 供 trace 使用的补充信息（可选）。
    """

    results: list[RetrievalResult] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    answer: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ChatRetriever(Protocol):
    """同步检索接口（API 层在线程中调用）。

    约定：
    - ``trace`` 是 ChatEngine 创建的当轮 trace；实现只可 ``record_stage``，**不得**
      ``finish()`` 或落盘（D7：每轮恰好一条 trace）。
    - DEEP 被门控拒绝时抛 :class:`DeepDisabledError`；其余失败抛原始异常，
      ChatEngine 统一分类（``LLMRequestError`` / ``AgenticSearchError`` / ``httpx`` / 超时等）。
    """

    def retrieve(
        self,
        query: str,
        *,
        collection: str | None = None,
        top_k: int | None = None,
        trace: Any | None = None,
    ) -> ChatRetrievalResult: ...
