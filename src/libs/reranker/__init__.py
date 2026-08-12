"""Reranker 抽象层包。

导入本包即触发 ``none`` 和 ``llm`` backend 的注册。
``none`` 在工厂中始终注册；``llm`` 在此处补充注册。
"""

from libs.reranker.base_reranker import BaseReranker, NoneReranker, RerankCandidate
from libs.reranker.reranker_factory import RerankerFactory

# 触发 provider 注册。
from libs.reranker import llm_reranker as _llm_reranker  # noqa: F401

_llm_reranker.register_llm_provider()

__all__ = ["BaseReranker", "NoneReranker", "RerankCandidate", "RerankerFactory"]
