"""HybridSearch：混合检索编排（阶段 D5）。

编排 D1-D4 组件完成完整查询链路（spec 3.1.2 / 5.4.2）::

    query_processor.process()
      ├─ dense.retrieve(pq.query, top_k_dense, filters)   # Pre-filter（Chroma where）
      └─ sparse.retrieve(pq.keywords, top_k_sparse)        # 无前置过滤能力
    → fusion.fuse(dense, sparse)                            # RRF
    → _apply_metadata_filters(fused, filters)               # Post-filter 兜底
    → [:top_k_final]

设计要点：
- **组件全可注入**（测试 fake / 未来替换实现）；未注入时按 ``Settings``
  创建默认实现（构造期即创建，与 D2/D3 纪律一致）。
- **降级策略**：Dense/Sparse 任一路径抛异常时记录失败、降级到另一路
  的原始结果（保留该路分数与排序，不做单路 RRF 重打分；存活路零命中
  属于合法结果，返回空列表）；**两路均抛异常**才抛
  :class:`HybridSearchError`（无可用召回路径，不可静默返回空）。
- **Metadata 过滤**（先解析、能前置则前置、无法前置则后置兜底）：
  Dense 路透传 ``filters`` 给 store 做 Pre-filter；融合后统一跑
  ``_apply_metadata_filters`` Post-filter（覆盖 Sparse 路——BM25 无
  metadata）。**缺失字段默认宽松包含**（``missing -> include``），
  避免 metadata 质量不稳时误杀召回。
- **中间结果**：``last_dense_results`` / ``last_sparse_results`` /
  ``last_fused_results`` / ``last_route_failures`` 供 D7 CLI ``--verbose``
  展示各阶段中间产物（正式追踪走 trace 阶段记录）。
- **trace 打点**：组件各自记录 ``query_processing`` / ``dense_retrieval``
  / ``sparse_retrieval`` / ``fusion`` 阶段（F3 约定）；本类额外记录
  ``hybrid`` 编排阶段（routes/degraded/counts/top_k/elapsed_ms）。
"""

from __future__ import annotations

import time
from typing import Any

from core.query_engine.dense_retriever import DenseRetriever
from core.query_engine.fusion import RRFFusion
from core.query_engine.query_processor import QueryProcessor
from core.query_engine.sparse_retriever import SparseRetriever
from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult
from observability.logger import get_logger

__all__ = ["HybridSearch", "HybridSearchError"]


class HybridSearchError(RuntimeError):
    """混合检索失败（两路召回均不可用，属于不可静默的运行时故障）。"""


class HybridSearch:
    """混合检索编排器：QueryProcessor + Dense + Sparse + RRF Fusion。

    Args:
        settings: 全局配置（``retrieval.top_k_dense/top_k_sparse/
            top_k_final`` 驱动各级候选数量）。
        query_processor: 查询预处理器（默认 ``QueryProcessor(settings)``）。
        dense_retriever: Dense 召回器（默认 ``DenseRetriever(settings)``）。
        sparse_retriever: Sparse 召回器（默认 ``SparseRetriever(settings)``）。
        fusion: 融合器（默认 ``RRFFusion(settings)``）。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        query_processor: QueryProcessor | None = None,
        dense_retriever: DenseRetriever | None = None,
        sparse_retriever: SparseRetriever | None = None,
        fusion: RRFFusion | None = None,
    ) -> None:
        self._settings = settings
        self._logger = get_logger("modular-rag.query_engine.hybrid_search")
        # 注入优先（同 C14 pick() 纪律：不得用真值判断丢弃合法注入实例）。
        self._query_processor = query_processor if query_processor is not None else QueryProcessor(settings)
        self._dense_retriever = dense_retriever if dense_retriever is not None else DenseRetriever(settings)
        self._sparse_retriever = sparse_retriever if sparse_retriever is not None else SparseRetriever(settings)
        self._fusion = fusion if fusion is not None else RRFFusion(settings)
        # 供 --verbose / 测试观察的中间结果（每次 search 重置）。
        self.last_dense_results: list[RetrievalResult] = []
        self.last_sparse_results: list[RetrievalResult] = []
        self.last_fused_results: list[RetrievalResult] = []
        self.last_route_failures: dict[str, str] = {}

    @property
    def dense_retriever(self) -> DenseRetriever:
        return self._dense_retriever

    @property
    def sparse_retriever(self) -> SparseRetriever:
        return self._sparse_retriever

    @property
    def fusion(self) -> RRFFusion:
        return self._fusion

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        *,
        trace: TraceContext | None = None,
    ) -> list[RetrievalResult]:
        """执行完整混合检索，返回 Top-K 结果。

        Args:
            query: 用户查询（原样保留给 Dense 路，提取 keywords 给 Sparse 路）。
            top_k: 最终返回数量；``None`` 取 ``retrieval.top_k_final``。
                注意最终结果受 ``top_k_dense`` / ``top_k_sparse`` 候选上限约束。
            filters: 可选结构化过滤（Dense 路 Pre-filter + 全路 Post-filter 兜底）。
            trace: 可选追踪上下文；``None`` 时内部新建（记录完整阶段链）。

        Returns:
            ``RetrievalResult`` 列表（融合分数降序；单路降级时保留该路
            原始分数与排序）。

        Raises:
            TypeError / QueryProcessingError: 查询预处理契约错误（透传 D1）。
            ValueError: ``top_k`` 非法。
            HybridSearchError: Dense 与 Sparse 两路召回均失败。
        """
        started = time.perf_counter()
        if top_k is None:
            top_k = self._settings.retrieval.top_k_final
        elif not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise ValueError(f"top_k must be a positive integer or None, got {top_k!r}")
        trace = trace if trace is not None else TraceContext(metadata={"query": query})
        self.last_dense_results = []
        self.last_sparse_results = []
        self.last_fused_results = []
        self.last_route_failures = {}

        processed = self._query_processor.process(query, filters=filters, trace=trace)
        dense_filters = processed.filters if processed.filters else None

        dense_results: list[RetrievalResult] = []
        sparse_results: list[RetrievalResult] = []
        failures: dict[str, str] = {}

        try:
            dense_results = self._dense_retriever.retrieve(
                processed.query,
                self._settings.retrieval.top_k_dense,
                dense_filters,
                trace=trace,
            )
        except Exception as exc:  # 单路失败降级，不中断查询
            failures["dense"] = f"{type(exc).__name__}: {exc}"
            self._logger.warning("dense route failed, degrading to sparse: %s", failures["dense"])

        try:
            sparse_results = self._sparse_retriever.retrieve(
                processed.keywords,
                self._settings.retrieval.top_k_sparse,
                trace=trace,
            )
        except Exception as exc:
            failures["sparse"] = f"{type(exc).__name__}: {exc}"
            self._logger.warning("sparse route failed, degrading to dense: %s", failures["sparse"])

        if "dense" in failures and "sparse" in failures:
            raise HybridSearchError(
                "both retrieval routes failed — "
                f"dense: {failures['dense']}; sparse: {failures['sparse']}"
            )

        if dense_results and sparse_results:
            fused = self._fusion.fuse(dense_results, sparse_results, trace=trace)
        else:
            # 单路降级（一路失败或空）：保留存活路原始分数与排序。
            fused = list(dense_results or sparse_results)
            if failures:
                self._logger.info(
                    "hybrid search degraded to single route (%s)", ", ".join(failures)
                )

        filtered = self._apply_metadata_filters(fused, processed.filters)
        results = filtered[:top_k]

        self.last_dense_results = dense_results
        self.last_sparse_results = sparse_results
        self.last_fused_results = fused
        self.last_route_failures = failures

        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        trace.record_stage(
            "hybrid",
            method="hybrid_search",
            details={
                "routes": {"dense": len(dense_results), "sparse": len(sparse_results)},
                "degraded": sorted(failures),
                "fused": len(fused),
                "filtered": len(filtered),
                "returned": len(results),
                "top_k": top_k,
                "elapsed_ms": elapsed_ms,
            },
        )
        self._logger.info(
            "hybrid search %r: dense=%d sparse=%d fused=%d -> %d results (%.1fms%s)",
            query, len(dense_results), len(sparse_results), len(fused), len(results),
            elapsed_ms, ", degraded" if failures else "",
        )
        return results

    # ------------------------------------------------------------------
    # 后置过滤
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_metadata_filters(
        candidates: list[RetrievalResult],
        filters: dict[str, Any] | None,
    ) -> list[RetrievalResult]:
        """Post-filter 兜底（spec 3.1.2 Metadata Filtering Strategy）。

        - 值语义为等值匹配（``metadata[key] == value``）；
        - **缺失字段宽松包含**（``missing -> include``）：metadata 中不
          存在该键的候选直接保留，避免 metadata 缺失/质量不稳时误杀召回
          （硬约束应尽量前置到 Dense 路 Pre-filter）；
        - ``filters`` 为空时原样返回候选列表副本。
        """
        if not filters:
            return list(candidates)
        kept: list[RetrievalResult] = []
        for candidate in candidates:
            included = True
            for key, expected in filters.items():
                if key not in candidate.metadata:
                    continue  # 缺失字段 → 宽松包含
                if candidate.metadata[key] != expected:
                    included = False
                    break
            if included:
                kept.append(candidate)
        return kept
