"""VectorStore 抽象基类与数据契约（阶段 B4）。

定义全链路共用的 ``BaseVectorStore`` 接口与 ``VectorRecord`` / ``QueryResult``
数据契约，屏蔽不同向量数据库后端（Chroma / Qdrant / Pinecone）的差异。

阶段 B4 仅定义契约与工厂，不接真实 DB；真实实现见 B7.6（ChromaStore）。

设计要点：
- ``upsert(records)``：批量写入（向量 + 正文 + metadata），幂等。
- ``query(vector, top_k, filters)``：向量检索，返回带 score 的结果列表。
- 预留 ``trace: TraceContext | None`` 参数（阶段 F 打点时使用，当前仅占位）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

__all__ = ["VectorRecord", "QueryResult", "BaseVectorStore"]


@dataclass
class VectorRecord:
    """向向量库写入的一条记录。

    Attributes:
        id: 记录唯一标识（通常为确定性 chunk_id）。
        vector: 稠密向量（Dense Embedding）。
        text: chunk 原文（便于命中后直接取回，无需额外查库）。
        metadata: 元数据（source_path / page / title / tags / image_refs 等）。
    """

    id: str
    vector: list[float]
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryResult:
    """向量检索返回的单条结果。

    Attributes:
        id: 命中记录的 id。
        score: 相似度分数（越高越相关，具体语义由后端决定）。
        text: 命中记录的原文。
        metadata: 命中记录的元数据。
    """

    id: str
    score: float
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "score": self.score, "text": self.text, "metadata": self.metadata}


class BaseVectorStore(ABC):
    """VectorStore 抽象基类，所有后端实现需继承并实现 ``upsert`` / ``query``。"""

    @abstractmethod
    def upsert(
        self,
        records: Sequence[VectorRecord],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> int:
        """批量幂等写入记录。

        Args:
            records: 待写入的记录序列。
            trace: 可选的 ``TraceContext``（当前占位）。
            **kwargs: 后端特定的扩展参数（如 ``collection`` 覆盖）。

        Returns:
            实际写入/更新的记录数。
        """
        raise NotImplementedError

    @abstractmethod
    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        *,
        filters: dict[str, Any] | None = None,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[QueryResult]:
        """向量检索，返回 Top-K 结果。

        Args:
            vector: 查询向量。
            top_k: 返回结果数量。
            filters: 可选的 metadata 过滤条件（如 ``{"collection": "x"}``）。
            trace: 可选的 ``TraceContext``（当前占位）。
            **kwargs: 后端特定的扩展参数。

        Returns:
            按 score 降序的 ``QueryResult`` 列表，长度 <= ``top_k``。
        """
        raise NotImplementedError

    @abstractmethod
    def get_by_ids(
        self,
        ids: Sequence[str],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """按 id 批量获取记录（阶段 D3：SparseRetriever 组装 text/metadata）。

        Args:
            ids: 待获取的记录 id 序列（通常为 BM25 命中的 chunk_id 列表）。
            trace: 可选的 ``TraceContext``（当前占位）。
            **kwargs: 后端特定的扩展参数。

        Returns:
            命中记录的 dict 列表，每条形如 ``{"id", "text", "metadata"}``；
            不存在的 id 直接跳过（不占位）；返回顺序与请求顺序一致
            （重复 id 只返回一次），保证确定性合并。
        """
        raise NotImplementedError
