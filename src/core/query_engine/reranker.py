"""Core 层 Reranker 编排与回退（阶段 D6）。

接入 ``libs.reranker`` 可插拔后端（None / CrossEncoder / LLM），对
D5 融合后的 Top-M 候选做精排，并提供稳定回退策略：

- **编排**：``RetrievalResult → RerankCandidate → backend.rerank →
  RetrievalResult``——重排只改变顺序与 score，``text`` / ``metadata``
  保留融合阶段的原始载荷（后端不应改写内容）。
- **fallback（spec D6 验收：后端异常不影响最终返回，标记
  ``fallback=true``）**：
  1. 后端 ``rerank`` 抛异常（网络/超时/解析失败）→ 返回**输入原序**，
     ``last_fallback=True``（等价回退到 RRF 融合排名，系统可用性优先）；
  2. 后端内部回退（B7.7/B7.8 契约：LLM 解析失败 / CrossEncoder scorer
     异常时不抛错而是原序返回并置自身 ``last_fallback=True``）→
     包装层透传该标记。
- **防御**：后端返回输入中不存在的 id 直接跳过（非法输出不进入结果）；
  后端丢弃候选（如 CrossEncoder ``top_m`` 截断）视为合法契约
  （``长度 <= 输入长度``），不强行补齐。
- **trace 打点**：``rerank`` 阶段（method=reranker，details 含 backend/
  candidates/returned/fallback/error/elapsed_ms，对齐 F3 约定）。
"""

from __future__ import annotations

import time
from typing import Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult
from libs.reranker import BaseReranker, RerankCandidate, RerankerFactory
from observability.logger import get_logger

__all__ = ["Reranker"]


class Reranker:
    """Core 层精排编排器：可插拔后端 + 失败回退融合排名。

    Args:
        settings: 全局配置（``rerank.backend`` 驱动默认后端创建）。
        backend: 注入的 ``BaseReranker``（测试 fake）；缺省经
            ``RerankerFactory.create(settings)`` 创建。
    """

    def __init__(self, settings: Settings, *, backend: BaseReranker | None = None) -> None:
        self._settings = settings
        self._logger = get_logger("modular-rag.query_engine.reranker")
        self._backend_name = settings.rerank.backend
        # 注入优先；未注入经工厂创建（导入 libs.reranker 包即触发注册）。
        self._backend = backend if backend is not None else RerankerFactory.create(settings)
        self.last_fallback: bool = False
        self.last_error: str | None = None

    @property
    def backend(self) -> BaseReranker:
        """底层精排后端（只读视图语义）。"""
        return self._backend

    @property
    def backend_name(self) -> str:
        """配置中的后端名（trace/展示用）。"""
        return self._backend_name

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def rerank(
        self,
        query: str,
        results: Sequence[RetrievalResult],
        *,
        trace: TraceContext | None = None,
    ) -> list[RetrievalResult]:
        """对融合候选精排（失败/内部回退时返回输入原序并标记 fallback）。

        Args:
            query: 用户查询。
            results: 融合阶段候选（假定已按相关性降序）。
            trace: 可选追踪上下文，记录 ``rerank`` 阶段。

        Returns:
            精排后的 ``RetrievalResult`` 列表（score 更新为后端分数；
            后端截断时长度可小于输入；回退时与输入完全一致）。

        Raises:
            TypeError: ``query`` 非 str / ``results`` 非列表。
            ValueError: ``query`` 为空/纯空白。
        """
        started = time.perf_counter()
        self.last_fallback = False
        self.last_error = None
        if not isinstance(query, str):
            raise TypeError(f"query must be a string, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be a non-empty, non-whitespace string")
        if not isinstance(results, (list, tuple)):
            raise TypeError(f"results must be a list of RetrievalResult, got {type(results).__name__}")
        candidates_in = list(results)
        if not candidates_in:
            self._record(trace, 0, 0, elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
            return []

        candidates = [
            RerankCandidate(id=r.chunk_id, text=r.text, score=r.score, metadata=r.metadata)
            for r in candidates_in
        ]
        try:
            ranked = self._backend.rerank(query, candidates, trace=trace)
        except Exception as exc:
            return self._fallback(candidates_in, exc, trace, started)

        # 后端内部回退标记透传（B7.7/B7.8 契约：不抛错但 last_fallback=True）。
        internal_fallback = bool(getattr(self._backend, "last_fallback", False))
        if internal_fallback:
            self.last_fallback = True
            self.last_error = f"{type(self._backend).__name__} internal fallback"

        by_id = {r.chunk_id: r for r in candidates_in}
        seen: set[str] = set()
        out: list[RetrievalResult] = []
        for candidate in ranked:
            original = by_id.get(candidate.id)
            if original is None or candidate.id in seen:
                continue  # 非法 id（输入中不存在）：防御性跳过
            seen.add(candidate.id)
            out.append(
                RetrievalResult(
                    chunk_id=candidate.id,
                    score=float(candidate.score),
                    text=original.text,
                    metadata=dict(original.metadata),
                )
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        self._record(
            trace,
            len(candidates_in),
            len(out),
            fallback=self.last_fallback,
            elapsed_ms=elapsed_ms,
            before=candidates_in,
            after=out,
        )
        if self.last_fallback:
            self._logger.warning(
                "rerank backend %s fell back internally: returning backend order", self._backend_name
            )
        else:
            self._logger.info(
                "reranked %d -> %d candidates via %s (%.1fms)",
                len(candidates_in), len(out), self._backend_name, elapsed_ms,
            )
        return out

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _fallback(
        self,
        results: list[RetrievalResult],
        exc: Exception,
        trace: TraceContext | None,
        started: float,
    ) -> list[RetrievalResult]:
        """后端异常回退：返回输入原序（等价融合排名）并标记 fallback。"""
        self.last_fallback = True
        self.last_error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        self._record(
            trace,
            len(results),
            len(results),
            fallback=True,
            error=self.last_error,
            elapsed_ms=elapsed_ms,
            before=results,
            after=results,
        )
        self._logger.warning(
            "rerank backend %s failed, falling back to fusion ranking: %s",
            self._backend_name, self.last_error,
        )
        return list(results)

    def _record(
        self,
        trace: TraceContext | None,
        candidates: int,
        returned: int,
        *,
        fallback: bool = False,
        error: str | None = None,
        elapsed_ms: float = 0.0,
        before: Sequence[RetrievalResult] | None = None,
        after: Sequence[RetrievalResult] | None = None,
    ) -> None:
        if trace is None:
            return
        details: dict = {
            "backend": self._backend_name,
            "candidates": candidates,
            "returned": returned,
            "fallback": fallback,
            "elapsed_ms": elapsed_ms,
        }
        if error is not None:
            details["error"] = error
        if before is not None:
            details["before"] = [
                {"chunk_id": item.chunk_id, "score": float(item.score)}
                for item in list(before)[:8]
            ]
        if after is not None:
            details["after"] = [
                {"chunk_id": item.chunk_id, "score": float(item.score)}
                for item in list(after)[:8]
            ]
        trace.record_stage("rerank", method="reranker", details=details)
