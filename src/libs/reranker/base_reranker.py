"""Reranker 抽象基类与 None 回退实现（阶段 B5）。

定义全链路共用的 ``BaseReranker`` 接口与 ``RerankCandidate`` 数据契约，
并提供 ``NoneReranker`` 作为默认安全回退（保持原顺序不变）。

设计要点：
- ``rerank(query, candidates)`` 为统一入口，返回重排后的候选列表。
- ``NoneReranker``：不改变顺序，作为 ``backend=none`` 的实现与失败回退兜底。
- 预留 ``trace: TraceContext | None`` 参数（阶段 F 打点时使用，当前仅占位）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

__all__ = ["RerankCandidate", "BaseReranker", "NoneReranker"]


@dataclass
class RerankCandidate:
    """重排候选的统一数据契约。

    Attributes:
        id: 候选唯一标识（通常为 chunk_id）。
        text: 候选文本（供 LLM/Cross-Encoder 打分）。
        score: 原始分数（来自召回阶段），重排后会被更新。
        metadata: 元数据（source/page 等）。
    """

    id: str
    text: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseReranker(ABC):
    """Reranker 抽象基类，所有后端实现需继承并实现 ``rerank``。"""

    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[RerankCandidate]:
        """对候选集按与 query 的相关性重排。

        Args:
            query: 用户查询。
            candidates: 召回阶段产出的候选序列。
            trace: 可选的 ``TraceContext``（当前占位）。
            **kwargs: 后端特定的扩展参数。

        Returns:
            重排后的候选列表（长度 <= 输入长度）。
        """
        raise NotImplementedError


class NoneReranker(BaseReranker):
    """``backend=none`` 的实现：保持原顺序不变，作为安全默认/回退兜底。"""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[RerankCandidate]:
        # 返回新列表，保持原顺序与原 score 不变。
        return list(candidates)
