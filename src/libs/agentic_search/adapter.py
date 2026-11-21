"""Agentic 第三路检索适配器 — 阶段 S3.1（自 S2.5 scripts/query.py 平移）。

把 ``BaseAgenticSearch`` 包装成第三路 ``retrieve`` 形态。

``HybridSearch`` 的 ``agentic_retriever`` 注入契约（S2.3）是鸭子类型
``retrieve(query, top_k, trace=...) -> list[RetrievalResult]``；本适配器
在其下调用 ``backend.search(AgenticSearchRequest)`` 并经
``evidence_to_retrieval_results`` 转换证据。``last_response`` /
``last_elapsed_ms`` 供调用方展示/采集 Token 与耗时。

AC4（AGENTIC_COLLECTION_ISOLATION_SPEC）：注入 ``settings`` 后适配器进入
装配模式——PathGuard / 历史表句柄均懒构造（构建期零 IO）；检索恒发显式
``paths``（无 collection 时为 ``PathGuard.allowed_roots``，修复基线
``paths=None`` 回退 sidecar CWD 绕开调用方白名单的缺口），证据恒经
``filter_evidence_paths`` 白名单过滤，带 collection 时再按定界集（AC1
``CollectionPathResolver``）收窄：空定界集返回 ``[]`` 不调 sidecar（D3）。
未注入 ``settings`` 的裸构造保持基线行为（既有测试语义锁定）。

落点说明：S2.5 时仅 CLI 需要此包装，故收敛在脚本内；S3.1 评估入口
（``scripts/evaluate.py``）成为第二个消费者，按原注释预案平移到
``libs/agentic_search/``（``scripts/query.py`` 改为 re-import，对外行为
零变化）。
"""

from __future__ import annotations

import sys
import time
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from core.types import RetrievalResult
from libs.agentic_search.agentic_search_factory import AgenticSearchFactory
from libs.agentic_search.base_agentic_search import AgenticSearchRequest
from libs.agentic_search.collection_paths import CollectionPathResolver
from libs.agentic_search.converter import evidence_to_retrieval_results
from libs.agentic_search.path_guard import PathGuard, PathGuardError

__all__ = ["AgenticRouteAdapter", "build_agentic_retriever"]


class AgenticRouteAdapter:
    """把 ``BaseAgenticSearch`` 包装成第三路 ``retrieve`` 形态。

    Args:
        backend: ``search(request) -> AgenticSearchResponse`` 后端。
        settings: 全局配置；注入后启用 AC4 装配模式（懒构造 PathGuard 与
            历史句柄、显式 ``paths``、证据过滤）。``None`` = 基线行为。
        history: 历史表访问句柄（测试注入；缺省懒构造
            ``SQLiteIntegrityChecker``，仅 collection 分支触达）。
    """

    def __init__(self, backend: Any, *, settings: Any = None, history: Any = None) -> None:
        self._backend = backend
        self._settings = settings
        self._history = history
        self._path_guard: PathGuard | None = None
        self.last_response: Any | None = None
        self.last_elapsed_ms: float | None = None

    @property
    def backend_name(self) -> str:
        return getattr(self._backend, "backend_name", type(self._backend).__name__)

    def _get_path_guard(self) -> PathGuard | None:
        """懒装配白名单守卫；裸构造（无 settings）返回 None（基线模式）。

        PathGuard 构造失败（如白名单根不存在）原样抛 ``PathGuardError``：
        fail-closed，不调用 sidecar；经 ``HybridSearch`` 注入时由其既有
        异常分支降级为两路并记入 ``failures["agentic"]``。
        """
        if self._path_guard is None and self._settings is not None:
            config = self._settings.agentic_search
            self._path_guard = PathGuard(config.allowed_roots, config.denied_substrings)
        return self._path_guard

    def _get_history(self) -> Any:
        if self._history is None:
            from libs.loader.file_integrity import SQLiteIntegrityChecker

            self._history = SQLiteIntegrityChecker()
        return self._history

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        trace: Any = None,
        collection: str | None = None,
    ) -> list[RetrievalResult]:
        """执行一次 Agentic 检索并转换为 ``RetrievalResult`` 列表。

        ``collection`` 仅装配模式（注入 settings）生效：非空时检索起点
        收窄到该 collection 定界集；空定界集直接返回 ``[]``（D3），不调
        sidecar、零 token 消耗。基线模式忽略该参数。
        """
        guard = self._get_path_guard()
        scoped: list[str] | None = None
        paths: list[str] | None = None
        if guard is not None:
            if collection and collection.strip():
                scoped = CollectionPathResolver(self._get_history(), guard).resolve(
                    collection.strip()
                )
                if not scoped:
                    return []  # D3：空定界集不调用 sidecar（fail-closed）
                paths = scoped
            else:
                # AC4 白名单修复：恒发显式 paths，不再回退 sidecar CWD。
                paths = guard.allowed_roots
        started = time.perf_counter()
        response = self._backend.search(AgenticSearchRequest(query=query, paths=paths))
        if guard is not None:
            # 装配模式下 last_response 记录本路实际使用的（过滤后）响应，
            # 供 verbose / 评估采集的 Token 与耗时字段原样保留；基线模式
            # （guard 为 None）不过滤、last_response 与基线逐字节一致。
            response = self._filter_response(response, guard, scoped)
        self.last_response = response
        self.last_elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return evidence_to_retrieval_results(response, top_k=top_k)

    def _filter_response(self, response: Any, guard: PathGuard, scoped: list[str] | None) -> Any:
        """证据恒经白名单过滤；带 collection 时再按定界集成员收窄。

        与 MCP tool ``_build_result`` 同形：先 ``filter_evidence_paths``
        归一（相对路径按 sidecar CWD 回显的常见基准解析），越权剔除；
        定界集过滤挡住 sidecar 经 ``file_read`` 返回的白名单内、定界集外
        的其他 collection 文件。下游 ``evidence_to_retrieval_results`` /
        ``dedup_and_expand`` 只看到过滤后的证据。
        """
        scope = set(scoped) if scoped is not None else None
        source_files = guard.filter_evidence_paths(response.source_files)
        kept_evidences = []
        for evidence in response.evidences:
            normalized = guard.filter_evidence_paths([evidence.source_path])
            if normalized and (scope is None or normalized[0] in scope):
                evidence.source_path = normalized[0]
                kept_evidences.append(evidence)
        if scope is not None:
            source_files = [p for p in source_files if p in scope]
        return type(response)(
            answer=response.answer,
            evidences=kept_evidences,
            mode=response.mode,
            source_files=source_files,
            confidence=response.confidence,
            llm_call_count=response.llm_call_count,
            total_llm_tokens=response.total_llm_tokens,
            elapsed_ms=response.elapsed_ms,
            telemetry=response.telemetry,
        )


def build_agentic_retriever(settings: Any) -> AgenticRouteAdapter:
    """按 ``settings.agentic_search.backend`` 构建第三路检索适配器（AC4 装配模式）。"""
    return AgenticRouteAdapter(AgenticSearchFactory.create(settings), settings=settings)
