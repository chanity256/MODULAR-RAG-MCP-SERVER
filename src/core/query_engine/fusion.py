"""RRF Fusion：排名倒数融合（阶段 D4）。

把 Dense / Sparse 两路召回的**排名**融合为统一排序（spec 3.1.2）：

- **公式**（不依赖各路分数的绝对值，只看排名）::

      Score(id) = Σ_routes 1 / (k + Rank_route(id))

  ``Rank`` 从 1 开始计数；``k`` 为平滑常数（默认 60，spec
  ``retrieval.rrf_k`` 可配），抑制单一模态缺陷导致的漏召回。
- **确定性**：同分（float 精确相等）按 ``chunk_id`` 升序打破平局；
  同一输入重复调用产出完全相同的结果。
- **payload 语义**：融合结果只重排与重打分，``text`` / ``metadata``
  取该 chunk **首次出现**路由的载荷（dense 先于 sparse——dense 路
  已过 Pre-filter，载荷更可信）。
- **纯函数核心**：:func:`rrf_fuse_rankings` 只吃 id 排名列表，便于
  独立单测融合数学；:class:`RRFFusion` 在其上组装 ``RetrievalResult``。
- **N 路融合**（S2.1）：:meth:`RRFFusion.fuse_routes` 支持任意路由名
  （如 dense/sparse/agentic），payload 优先级固定 dense > sparse >
  agentic（与 dict 传入顺序无关）；两路 :meth:`RRFFusion.fuse` 内部
  委托 N 路实现，仅 trace details 字段形态保持旧契约。
- **trace 打点**：``fusion`` 阶段（method=rrf，details 含 k/dense/sparse/
  fused/elapsed_ms，对齐 F3 约定）。
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult

__all__ = ["RRFFusion", "rrf_fuse_rankings", "DEFAULT_RRF_K", "ROUTE_PRIORITY"]

#: 默认 RRF 平滑常数（spec retrieval.rrf_k 缺省值）。
DEFAULT_RRF_K = 60

#: 路由优先级（payload 归属：同 chunk 多路命中时取排序靠前路由的载荷）。
#: 规范固定 dense > sparse > agentic，**与 dict 传入顺序无关**；
#: 未登记的路由名按传入顺序排在已知路由之后。
ROUTE_PRIORITY: tuple[str, ...] = ("dense", "sparse", "agentic")

# 内部引用别名（fuse_routes 排序用）。
_ROUTE_PRIORITY = ROUTE_PRIORITY


def rrf_fuse_rankings(
    rankings: Sequence[Sequence[str]],
    k: int = DEFAULT_RRF_K,
) -> list[tuple[str, float]]:
    """纯函数：多路排名列表 → ``(chunk_id, rrf_score)`` 融合排名。

    Args:
        rankings: 每路的 id 排名列表（位置即排名，从 1 开始）；
            同一路内重复 id 只按首次出现的排名计一次。
        k: 平滑常数（≥ 0）。

    Returns:
        按 score 降序的 ``(chunk_id, score)`` 列表；同分按 chunk_id
        升序（确定性输出）。
    """
    if not isinstance(k, int) or isinstance(k, bool) or k < 0:
        raise ValueError(f"k must be a non-negative integer, got {k!r}")
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen_in_route: set[str] = set()
        for rank, chunk_id in enumerate(ranking, start=1):
            if chunk_id in seen_in_route:
                continue
            seen_in_route.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked


class RRFFusion:
    """RRF 融合器：Dense + Sparse 两路 ``RetrievalResult`` → 统一排名。

    Args:
        settings: 全局配置；``k`` 未显式给出时读取
            ``settings.retrieval.rrf_k``（配置驱动，yaml 可调）。
        k: 显式平滑常数，优先于 ``settings.retrieval.rrf_k``。
    """

    def __init__(self, settings: Settings | None = None, *, k: int | None = None) -> None:
        if k is None:
            k = settings.retrieval.rrf_k if settings is not None else DEFAULT_RRF_K
        if not isinstance(k, int) or isinstance(k, bool) or k < 0:
            raise ValueError(f"k must be a non-negative integer, got {k!r}")
        self._k = k

    @property
    def k(self) -> int:
        """当前平滑常数。"""
        return self._k

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def fuse(
        self,
        dense: Sequence[RetrievalResult],
        sparse: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
        trace: TraceContext | None = None,
    ) -> list[RetrievalResult]:
        """融合两路召回结果（Dense + Sparse，S2.1 起内部委托 :meth:`fuse_routes`）。

        Args:
            dense: Dense 路结果（假定已按相关性降序）。
            sparse: Sparse 路结果（假定已按 BM25 分数降序）。
            top_k: 可选截断（正整数）；``None`` 保留全部融合结果。
            trace: 可选追踪上下文，记录 ``fusion`` 阶段。

        Returns:
            融合后的 ``RetrievalResult`` 列表（rrf score 降序、同分按
            chunk_id 升序）；``score`` 为 RRF 融合分数（保留 6 位小数），
            ``text`` / ``metadata`` 取首次出现路由的载荷。

        Raises:
            TypeError: ``dense`` / ``sparse`` 非列表/元组。
            ValueError: ``top_k`` 给定但非正整数。

        Note:
            委托而非复制的边界：融合数学与 payload 组装完全走
            :meth:`fuse_routes`；唯一本地保留的是 trace details 的
            **字段形态**（dense/sparse 计数平铺在顶层，既有契约），
            与 ``fuse_routes`` 的 ``routes: {...}`` 嵌套形态并存兼容。
        """
        started = time.perf_counter()
        results = self.fuse_routes({"dense": dense, "sparse": sparse}, top_k=top_k)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if trace is not None:
            dense_list = self._validate_route(dense, "dense")
            sparse_list = self._validate_route(sparse, "sparse")
            trace.record_stage(
                "fusion",
                method="rrf",
                details={
                    "k": self._k,
                    "dense": len(dense_list),
                    "sparse": len(sparse_list),
                    "fused": len(results),
                    "elapsed_ms": elapsed_ms,
                    "preview": [
                        {"chunk_id": item.chunk_id, "score": float(item.score)}
                        for item in results[:8]
                    ],
                },
            )
        return results

    def fuse_routes(
        self,
        routes: dict[str, Sequence[RetrievalResult]],
        *,
        top_k: int | None = None,
        trace: TraceContext | None = None,
    ) -> list[RetrievalResult]:
        """N 路融合（S2.1）：按路由名聚合的召回结果 → 统一 RRF 排名。

        Args:
            routes: 路由名 → 该路召回结果的映射（key 如 ``"dense"`` /
                ``"sparse"`` / ``"agentic"``）。payload 归属优先级固定为
                dense > sparse > agentic（:data:`ROUTE_PRIORITY`），
                **不依赖 dict 传入顺序**；未登记的路由名按传入顺序排在
                已知路由之后参与融合。
            top_k: 可选截断（正整数）；``None`` 保留全部融合结果。
            trace: 可选追踪上下文，记录 ``fusion`` 阶段（details 含
                ``routes: {路由名: 计数}``，与既有 k/fused/elapsed_ms/
                preview 字段并存）。

        Returns:
            融合后的 ``RetrievalResult`` 列表（rrf score 降序、同分按
            chunk_id 升序）；``score`` 为 RRF 融合分数（保留 6 位小数），
            ``text`` / ``metadata`` 取该 chunk **首次出现**路由的载荷。

        Raises:
            TypeError: ``routes`` 非字典，或某路结果非列表/元组。
            ValueError: ``top_k`` 给定但非正整数。
        """
        started = time.perf_counter()
        if not isinstance(routes, dict):
            raise TypeError(f"routes must be a dict of route name to list, got {type(routes).__name__}")
        if top_k is not None and (not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1):
            raise ValueError(f"top_k must be a positive integer, got {top_k!r}")
        # 规范优先级排序（稳定）：已知路由按 _ROUTE_PRIORITY，未知路由保持传入顺序垫后。
        ordered = sorted(
            routes.items(),
            key=lambda kv: (_ROUTE_PRIORITY.index(kv[0]) if kv[0] in _ROUTE_PRIORITY else len(_ROUTE_PRIORITY)),
        )
        route_lists: dict[str, list[RetrievalResult]] = {
            name: self._validate_route(route, name) for name, route in ordered
        }
        # 载荷：按优先级顺序首次出现优先（dense > sparse > agentic）。
        payload: dict[str, RetrievalResult] = {}
        for route_list in route_lists.values():
            for result in route_list:
                payload.setdefault(result.chunk_id, result)
        fused = rrf_fuse_rankings(
            [[r.chunk_id for r in route_list] for route_list in route_lists.values()],
            k=self._k,
        )
        results = [
            RetrievalResult(
                chunk_id=chunk_id,
                score=round(score, 6),
                text=payload[chunk_id].text,
                metadata=dict(payload[chunk_id].metadata),
            )
            for chunk_id, score in fused
        ]
        if top_k is not None:
            results = results[:top_k]
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if trace is not None:
            trace.record_stage(
                "fusion",
                method="rrf",
                details={
                    "k": self._k,
                    "routes": {name: len(route_list) for name, route_list in route_lists.items()},
                    "fused": len(results),
                    "elapsed_ms": elapsed_ms,
                    "preview": [
                        {"chunk_id": item.chunk_id, "score": float(item.score)}
                        for item in results[:8]
                    ],
                },
            )
        return results

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_route(route: Any, name: str) -> list[RetrievalResult]:
        if not isinstance(route, (list, tuple)):
            raise TypeError(f"{name} must be a list of RetrievalResult, got {type(route).__name__}")
        return list(route)
