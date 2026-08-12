"""VectorStore 抽象层包。

导入本包即触发 ``chroma`` backend 的注册。
"""

from libs.vector_store.base_vector_store import BaseVectorStore, QueryResult, VectorRecord
from libs.vector_store.vector_store_factory import VectorStoreFactory

# 触发 provider 注册。
from libs.vector_store import chroma_store as _chroma_store  # noqa: F401

_chroma_store.register_chroma_provider()

__all__ = ["BaseVectorStore", "QueryResult", "VectorRecord", "VectorStoreFactory"]
