"""HybridSearch：混合检索编排（阶段 D5）。

编排 D1-D4 组件完成完整查询链路（spec 3.1.2 / 5.4.2）::

    query_processor.process()
      ├─ dense.retrieve(pq.query, top_k_dense, filters)   # Pre-filter（Chroma where）
      └─ sparse.retrieve(pq.keywords, top_k_sparse)        # 无前置过滤能力
    → [可选] agentic_retriever.retrieve(query, top_k)      # 第三路（S2.3，触发时）
    → fusion.fuse(dense, sparse[, agentic])                 # RRF（触发时走 fuse_routes）
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
- **可选第三路（S2.3）**：注入 ``agentic_retriever``（鸭子类型：
  ``retrieve(query, top_k, trace=...) -> list[RetrievalResult]``，见
  ``libs/agentic_search/base_agentic_search.BaseAgenticSearch`` ——真实
  后端签名是 ``search(request)``，由上层适配为 ``retrieve`` 后注入）且
  ``agentic_search.enabled=true`` 时激活。是否触发由
  :class:`~core.query_engine.agentic_trigger.AgenticTrigger` 判定：
  ``tool_only`` 永不触发；``fallback`` 依据主路（dense+sparse）融合信号
  （RRF 融合为纯函数，先融合一次取 top 分数判定，不记 trace）；``always``
  每次触发。触发后三路统一走 :meth:`RRFFusion.fuse_routes` 融合，第三路
  结果随融合输出进入与 dense/sparse 相同的 rerank 管线（rerank 在调用链
  上层 MCP tool 中对 ``HybridSearch.search`` 输出统一执行，不绕过）。
  第三路失败降级为 N-1 路（记入 route_failures，即 trace ``degraded``），
  仍只有 dense+sparse 全失败才抛 :class:`HybridSearchError`。
  **回归红线**：未注入 / ``enabled=false`` / 未触发时，主链路输出
  （trace details 形态、``last_*`` 属性、日志）与基线逐字节一致——
  未触发的 ``hybrid`` trace details 不含任何 agentic 字段。
"""

from __future__ import annotations

import time
from typing import Any

from core.query_engine.agentic_dedup import dedup_and_expand
from core.query_engine.agentic_trigger import TRIGGER_FALLBACK, AgenticTrigger, TriggerDecision
from core.query_engine.dense_retriever import DenseRetriever
from core.query_engine.fusion import RRFFusion
from core.query_engine.query_processor import QueryProcessor
from core.query_engine.sparse_retriever import SparseRetriever
from core.settings import Settings
from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext
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
        agentic_retriever: Any | None = None,
    ) -> None:
        """Args:
            agentic_retriever: 可选第三路 agentic 检索器（S2.3，鸭子类型：
                ``retrieve(query, top_k, trace=...) -> list[RetrievalResult]``）。
                ``None``（或 ``agentic_search.enabled=false``）= 无第三路，
                主链路与基线逐字节一致。
        """
        self._settings = settings
        self._logger = get_logger("modular-rag.query_engine.hybrid_search")
        # 注入优先（同 C14 pick() 纪律：不得用真值判断丢弃合法注入实例）。
        self._query_processor = query_processor if query_processor is not None else QueryProcessor(settings)
        self._dense_retriever = dense_retriever if dense_retriever is not None else DenseRetriever(settings)
        self._sparse_retriever = sparse_retriever if sparse_retriever is not None else SparseRetriever(settings)
        self._fusion = fusion if fusion is not None else RRFFusion(settings)
        # 第三路 agentic 检索（S2.3）：注入且 enabled 时才激活；触发策略
        # 从 settings.agentic_search 读取（fail-fast 构造 AgenticTrigger）。
        self._agentic_retriever = agentic_retriever
        self._agentic_active = agentic_retriever is not None and settings.agentic_search.enabled
        self._trigger: AgenticTrigger | None = None
        if self._agentic_active:
            cfg = settings.agentic_search
            self._trigger = AgenticTrigger(
                cfg.trigger,
                score_threshold=cfg.trigger_score_threshold,
                min_results=cfg.trigger_min_results,
            )
        # 供 --verbose / 测试观察的中间结果（每次 search 重置）。
        self.last_dense_results: list[RetrievalResult] = []
        self.last_sparse_results: list[RetrievalResult] = []
        self.last_fused_results: list[RetrievalResult] = []
        self.last_route_failures: dict[str, str] = {}
        self.last_agentic_results: list[RetrievalResult] = []
        self.last_trigger_decision: TriggerDecision | None = None
        # S2.4：宿主文件白名单守卫（懒构造，仅扩展 agentic 上下文时需要；
        # allowed_roots 为空或构造失败时为 None → 扩展 fail-closed 跳过）。
        self._agentic_path_guard: Any | None = None
        self._path_guard_built = False

    @property
    def dense_retriever(self) -> DenseRetriever:
        return self._dense_retriever

    @property
    def sparse_retriever(self) -> SparseRetriever:
        return self._sparse_retriever

    @property
    def fusion(self) -> RRFFusion:
        return self._fusion

    def _get_agentic_path_guard(self) -> Any | None:
        """懒构造 agentic 宿主文件白名单守卫（S2.4；失败降级为不扩展）。"""
        if not self._path_guard_built:
            self._path_guard_built = True
            cfg = self._settings.agentic_search
            if cfg.allowed_roots:
                try:
                    from libs.agentic_search.path_guard import PathGuard

                    self._agentic_path_guard = PathGuard(cfg.allowed_roots, cfg.denied_substrings)
                except Exception as exc:  # 白名单根不存在等：fail-closed，仅跳过扩展
                    self._logger.warning("agentic path guard unavailable, expansion disabled: %s", exc)
        return self._agentic_path_guard

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
        trace = trace if trace is not None else TraceContext(
            trace_type=TRACE_TYPE_QUERY, metadata={"query": query}
        )
        self.last_dense_results = []
        self.last_sparse_results = []
        self.last_fused_results = []
        self.last_route_failures = {}
        self.last_agentic_results = []
        self.last_trigger_decision = None

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

        # ---- 第三路 agentic 检索（S2.3，可选）----
        # 仅在注入 agentic_retriever 且 enabled=true 时激活；tool_only 永不
        # 触发（主链路与基线逐字节一致）。fallback 用主路（dense+sparse）
        # 融合信号判定——RRF 融合是纯函数，先无 trace 融合一次取 top 分数，
        # 触发后再把三路统一 fuse_routes（rerank 在上层对最终融合输出统一
        # 执行一次，不因二次融合翻倍）。
        agentic_results: list[RetrievalResult] = []
        trigger_decision: TriggerDecision | None = None
        if self._agentic_active:
            assert self._trigger is not None
            if self._trigger.strategy == TRIGGER_FALLBACK:
                prelim = self._fusion.fuse_routes(
                    {"dense": dense_results, "sparse": sparse_results}
                )
                top_score = prelim[0].score if prelim else None
                trigger_decision = self._trigger.should_trigger(top_score, len(prelim))
            else:
                # tool_only / always：无需主路信号。
                trigger_decision = self._trigger.should_trigger(None, 0)
            self.last_trigger_decision = trigger_decision
            if trigger_decision.triggered:
                self._logger.info(
                    "agentic route triggered (strategy=%s): %s",
                    self._trigger.strategy, trigger_decision.reason,
                )
                try:
                    agentic_results = self._agentic_retriever.retrieve(
                        processed.query,
                        self._settings.retrieval.top_k_dense,
                        trace=trace,
                    )
                except Exception as exc:  # 第三路失败降级为 N-1 路，不中断查询
                    failures["agentic"] = f"{type(exc).__name__}: {exc}"
                    agentic_results = []
                    self._logger.warning(
                        "agentic route failed, degrading to %d route(s): %s",
                        2 - sum(1 for r in ("dense", "sparse") if r in failures),
                        failures["agentic"],
                    )
                self.last_agentic_results = list(agentic_results)

        triggered = trigger_decision is not None and trigger_decision.triggered
        if triggered:
            # 触发：三路统一 N 路融合（fuse_routes trace details 为
            # routes: {...} 嵌套形态；agentic 路失败时为空列表计入计数 0）。
            fused = self._fusion.fuse_routes(
                {"dense": dense_results, "sparse": sparse_results, "agentic": agentic_results},
                trace=trace,
            )
        elif dense_results and sparse_results:
            fused = self._fusion.fuse(dense_results, sparse_results, trace=trace)
        else:
            # 单路降级（一路失败或空）：保留存活路原始分数与排序。
            fused = list(dense_results or sparse_results)
            if failures:
                self._logger.info(
                    "hybrid search degraded to single route (%s)", ", ".join(failures)
                )

        # ---- S2.4 后置去重与上下文扩展 ----
        # 仅第三路触发且融合结果含 agentic 条目时才有开销；未触发第三路时
        # 此分支整体跳过（主链路路径零改动，回归基线逐字节一致）。
        if triggered and any(
            result.metadata.get("retrieval_route") == "agentic" for result in fused
        ):
            fused = dedup_and_expand(
                fused,
                chunk_size=self._settings.splitter.chunk_size,
                path_guard=self._get_agentic_path_guard(),
            )

        filtered = self._apply_metadata_filters(fused, processed.filters)
        results = filtered[:top_k]

        self.last_dense_results = dense_results
        self.last_sparse_results = sparse_results
        self.last_fused_results = fused
        self.last_route_failures = failures

        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        details_routes: dict[str, int] = {"dense": len(dense_results), "sparse": len(sparse_results)}
        details: dict[str, Any] = {
            "routes": details_routes,
            "degraded": sorted(failures),
            "fused": len(fused),
            "filtered": len(filtered),
            "returned": len(results),
            "top_k": top_k,
            "elapsed_ms": elapsed_ms,
        }
        if triggered:
            # 触发时才追加 agentic 字段；未触发主链路 details 与基线逐字节一致。
            details_routes["agentic"] = len(agentic_results)
            details["agentic_trigger"] = {
                "strategy": self._trigger.strategy if self._trigger is not None else None,
                "triggered": True,
                "reason": trigger_decision.reason if trigger_decision is not None else None,
            }
        trace.record_stage("hybrid", method="hybrid_search", details=details)
        self._logger.info(
            "hybrid search %r: dense=%d sparse=%d fused=%d -> %d results (%.1fms%s)",
            query, len(dense_results), len(sparse_results), len(fused), len(results),
            elapsed_ms, ", degraded" if failures else "",
        )
        trace.finish()
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
