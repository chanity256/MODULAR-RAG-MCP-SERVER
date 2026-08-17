"""ChromaStore — Chroma 向量数据库适配器（阶段 B7.6）。

封装 ``chromadb`` 的嵌入式 API，实现 ``BaseVectorStore`` 契约。

设计要点：
- 使用 ``PersistentClient`` 本地持久化，目录由 ``VectorStoreConfig.persist_path`` 驱动。
- ``upsert`` 幂等：相同 id 覆盖，Chroma 原生支持。
- ``query`` 向量检索，返回 ``QueryResult`` 列表（按 score 降序）。
- distance → score 转换：cosine 空间下 ``score = 1 - distance``（越高越相关）。
- metadata filters 直接透传给 Chroma ``where`` 子句。
- 距离度量默认 cosine，可通过 ``vs_config.extra["distance"]`` 覆盖。
- 集合名由 ``vs_config.collection`` 或 kwargs ``collection`` 或默认 ``knowledge_hub`` 决定。
"""

from __future__ import annotations

from typing import Any, Sequence

import chromadb

from core.settings import VectorStoreConfig
from libs.vector_store.base_vector_store import BaseVectorStore, QueryResult, VectorRecord

__all__ = ["ChromaStore", "register_chroma_provider"]


_DEFAULT_COLLECTION = "knowledge_hub"


class ChromaStore(BaseVectorStore):
    """Chroma 向量库实现。"""

    def __init__(self, vs_config: VectorStoreConfig, *, collection: str | None = None, **kwargs: Any):
        self.vs_config = vs_config
        self._collection_name = collection or vs_config.collection or _DEFAULT_COLLECTION
        self._distance = vs_config.extra.get("distance", "cosine")
        persist_path = vs_config.persist_path or "./data/db/chroma"
        self._client = chromadb.PersistentClient(path=persist_path)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": self._distance},
        )

    # -- BaseVectorStore 实现 ---------------------------------------------

    def upsert(
        self,
        records: Sequence[VectorRecord],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> int:
        if not isinstance(records, (list, tuple)):
            raise ValueError(f"records must be a list, got {type(records).__name__}")
        if len(records) == 0:
            return 0
        ids = [r.id for r in records]
        embeddings = [r.vector for r in records]
        documents = [r.text for r in records]
        # Chroma 拒绝空 dict metadata，用 None 替代。
        metadatas = [r.metadata if r.metadata else None for r in records]
        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        return len(records)

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        *,
        filters: dict[str, Any] | None = None,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[QueryResult]:
        if not isinstance(vector, list):
            raise ValueError(f"vector must be a list, got {type(vector).__name__}")
        if top_k <= 0:
            return []
        where = filters if filters else None
        results = self._collection.query(
            query_embeddings=[vector],
            n_results=top_k,
            where=where,
        )
        # Chroma 返回嵌套列表：每条 query 对应一个列表
        ids_batch = results.get("ids", [[]])
        distances_batch = results.get("distances", [[]])
        documents_batch = results.get("documents", [[]])
        metadatas_batch = results.get("metadatas", [[]])
        ids = ids_batch[0] if ids_batch else []
        distances = distances_batch[0] if distances_batch else []
        documents = documents_batch[0] if documents_batch else []
        metadatas = metadatas_batch[0] if metadatas_batch else []
        out: list[QueryResult] = []
        for i, rid in enumerate(ids):
            dist = distances[i] if i < len(distances) else 0.0
            score = self._distance_to_score(dist)
            text = documents[i] if i < len(documents) else ""
            meta = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
            out.append(QueryResult(id=rid, score=score, text=text, metadata=meta))
        return out

    # -- 辅助 -------------------------------------------------------------

    def get_by_ids(
        self,
        ids: Sequence[str],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """按 id 批量获取记录（D3 SparseRetriever 契约）。

        Args:
            ids: 待获取的记录 id 序列。

        Returns:
            命中记录的 ``{"id", "text", "metadata"}`` 列表；不存在的 id
            跳过；顺序与请求顺序一致（重复 id 去重）。
        """
        if not isinstance(ids, (list, tuple)):
            raise ValueError(f"ids must be a list, got {type(ids).__name__}")
        requested: list[str] = []
        for item in ids:
            if not isinstance(item, str) or not item:
                raise ValueError(f"ids must contain non-empty strings, got {item!r}")
            requested.append(item)
        if not requested:
            return []
        # Chroma 的 get 拒绝重复 id：先按首次出现顺序去重再请求。
        unique_requested = list(dict.fromkeys(requested))
        results = self._collection.get(ids=unique_requested, include=["documents", "metadatas"])
        got_ids = results.get("ids") or []
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        by_id: dict[str, dict[str, Any]] = {}
        for i, rid in enumerate(got_ids):
            text = documents[i] if i < len(documents) and documents[i] else ""
            metadata = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
            by_id[rid] = {"id": rid, "text": text, "metadata": metadata}
        out: list[dict[str, Any]] = []
        for rid in unique_requested:
            record = by_id.get(rid)
            if record is not None:
                out.append(record)
        return out

    def _distance_to_score(self, distance: float) -> float:
        """将 Chroma distance 转换为相似度 score（越高越相关）。

        - cosine: distance = 1 - cosine_similarity → score = 1 - distance
        - l2:     distance = squared L2 → score = 1 / (1 + distance)
        """
        if self._distance == "cosine":
            return 1.0 - distance
        return 1.0 / (1.0 + distance)

    def count(self) -> int:
        """返回当前集合中的记录数。"""
        return self._collection.count()

    def delete_all(self) -> None:
        """删除当前集合并重新创建（测试辅助）。"""
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": self._distance},
        )


def register_chroma_provider() -> None:
    """向 ``VectorStoreFactory`` 注册 chroma backend。"""
    from libs.vector_store.vector_store_factory import VectorStoreFactory

    @VectorStoreFactory.register("chroma")
    def _ctor(vs_config: VectorStoreConfig, **kwargs: Any) -> ChromaStore:
        return ChromaStore(vs_config, **kwargs)
