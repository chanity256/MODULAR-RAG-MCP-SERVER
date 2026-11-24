"""Chat 检索协议（WC6.4，WEBUI_CHAT_SPEC D8）。

``src/core/chat/`` 不得依赖 ``mcp_server`` / ``observability``，因此检索能力以协议形式注入；
hybrid / agentic 两个实现位于应用服务层（``services/chat_retrievers.py``），此处不含任何实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from core.types import RetrievalResult

__all__ = [
    "ChatRetrievalResult",
    "ChatRetriever",
    "DeepDisabledError",
    "RETRIEVAL_HYBRID",
    "RETRIEVAL_AGENTIC_FAST",
    "RETRIEVAL_AGENTIC_DEEP",
    "RETRIEVAL_LONGDOC",
    "RETRIEVAL_MODES",
    "RETRIEVAL_AUTO",
    "RoutingDecision",
    "ModeRouter",
    "SearchLogCallback",
]

RETRIEVAL_HYBRID = "hybrid"
RETRIEVAL_AGENTIC_FAST = "agentic_fast"
RETRIEVAL_AGENTIC_DEEP = "agentic_deep"
# PAGEINDEX_TREE_RETRIEVAL_SPEC PI5：第四档真实模式 ``longdoc``（sidecar 沿文档
# 树选节点）；注册与可用性仍由装配层按 ``tree_search.enabled`` 门控。
RETRIEVAL_LONGDOC = "longdoc"
RETRIEVAL_MODES = (RETRIEVAL_HYBRID, RETRIEVAL_AGENTIC_FAST, RETRIEVAL_AGENTIC_DEEP, RETRIEVAL_LONGDOC)

# MODE_ROUTER_SPEC MR1：请求值 ``auto`` 表示由决策模型定档。它不是检索器，
# 不得混入 ``RETRIEVAL_MODES``（该元组同时用于引擎校验与 capabilities 过滤），
# 由 ChatEngine 在检索前解析为三档之一。
RETRIEVAL_AUTO = "auto"


@dataclass
class RoutingDecision:
    """决策模型的一次定档结果。

    Attributes:
        mode: 选中的检索模式，必须是 ``decide`` 收到的 ``candidates`` 之一。
        confidence: 决策模型给出的置信度；后端不提供时为 ``None``。
        probabilities: 各候选的模式概率；后端不提供时为 ``None``。
    """

    mode: str
    confidence: float | None = None
    probabilities: dict[str, float] | None = None


@runtime_checkable
class ModeRouter(Protocol):
    """检索模式决策接口（MODE_ROUTER_SPEC MR1）。

    约定：
    - ``candidates`` 由装配层计算后注入（已注册检索器 ∩ 门控可用，与
      ``compute_capabilities`` 同一口径）；返回的 ``RoutingDecision.mode``
      必须属于 ``candidates``，实现不得自作主张。
    - 决策失败（超时 / 不可达 / 非法输出 / 低置信度）由实现以异常表达，
      ChatEngine 统一回退 ``fallback_retrieval``，不中断会话。
    - 实现位于 ``libs/mode_router/``（MR2）；core/chat 保持零实现依赖。
    """

    def decide(self, query: str, candidates: tuple[str, ...]) -> RoutingDecision: ...


#: 检索过程日志回调 ``(level, message, timestamp)``；timestamp 为 sidecar 侧 epoch 秒，可缺失。
SearchLogCallback = Callable[[str, str, Optional[float]], None]


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
    - ``on_log`` 是**可选**能力：仅声明了该形参（或 ``**kwargs``）的实现才会收到，
      且仅当 ``chat.agentic_progress_stream`` 开启、检索模式为 agentic 时由 ChatEngine 传入；
      实现可在检索线程中任意次调用它，ChatEngine 负责转成 ``search_log`` 帧。
      不声明 ``on_log`` 的旧实现保持兼容。
    """

    def retrieve(
        self,
        query: str,
        *,
        collection: str | None = None,
        top_k: int | None = None,
        trace: Any | None = None,
        on_log: SearchLogCallback | None = None,
    ) -> ChatRetrievalResult: ...
