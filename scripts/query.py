"""在线查询命令行入口（阶段 D7，开发调试用）。

用法（spec D7 验收形态）::

    python scripts/query.py --query "如何配置 Azure？"
    python scripts/query.py --query "bm25 scoring" --top-k 5 --collection docs --verbose
    python scripts/query.py --query "部署密钥" --no-rerank

行为契约（D7 验收标准）：
- ``--query``：必填查询文本（空/纯空白按用法错误处理，退出码 2）。
- ``--top-k``：可选返回数量（默认 ``retrieval.top_k_final``）。
- ``--collection``：可选，限定检索集合（构造 ``filters={"collection": ...}``，
  Dense 路 Pre-filter + 全路 Post-filter 兜底）。
- ``--verbose``：额外显示各阶段中间结果（Dense/Sparse/Fusion 召回、
  Rerank 后端与 fallback 状态）。
- ``--no-rerank``：跳过 Reranker 阶段（直接返回融合排名）。
- ``--agentic``（S2.5）：启用第三路 Agentic 检索（注入 HybridSearch 的
  ``agentic_retriever``，见 S2.3）；需 ``settings.agentic_search.enabled=true``，
  未启用按用法/配置错误处理（退出码 2）。
- 默认模式输出：Top-K 结果（序号、score、来源文件、页码、文本摘要）。
- 无数据/无命中：友好提示先运行 ``scripts/ingest.py`` 摄取数据（退出码 0）。

内部流程（spec D7）：加载 Settings → 初始化组件（Embedding / VectorStore /
BM25Indexer / Reranker）→ 组装 QueryProcessor + DenseRetriever +
SparseRetriever + HybridSearch → ``HybridSearch.search()`` →（除非
``--no-rerank``）``Reranker.rerank()`` → 格式化输出。

与 MCP Tool 的关系（spec 注记）：本脚本是开发调试入口；E3
``query_knowledge_hub`` 是生产 MCP Tool。两者共享 Core 层
（HybridSearch + Reranker），入口与输出格式各自独立。

可测试性：解析/渲染/执行全部为可导入纯函数，``main(argv, *, search=...,
reranker=..., out=...)`` 支持注入已构建的查询栈（离线单测零网络零
真实持久化，见 ``tests/unit/test_query_entry.py``）。

退出码：``0`` = 查询完成（含无命中）；``1`` = 检索链路失败；
``2`` = 用法/配置错误。
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

# 让 ``src`` 下的包在未安装时也可被直接 import（与根 ``main.py``、
# ``scripts/ingest.py`` 同一模式，支持 ``python scripts/query.py`` 直接运行）。
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.query_engine import HybridSearch, Reranker  # noqa: E402
from core.query_engine.query_processor import QueryProcessingError  # noqa: E402
from core.settings import ConfigError, Settings, load_settings  # noqa: E402
from core.trace.persist import persist_trace  # noqa: E402
from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext  # noqa: E402
from core.types import RetrievalResult  # noqa: E402
from libs.agentic_search import (  # noqa: E402
    AgenticSearchFactory,
    AgenticSearchRequest,
    evidence_to_retrieval_results,
)

__all__ = [
    "AgenticRouteAdapter",
    "DEFAULT_CONFIG_PATH",
    "EXIT_OK",
    "EXIT_FAILURE",
    "EXIT_USAGE",
    "QueryOutcome",
    "build_parser",
    "parse_args",
    "build_search",
    "build_reranker",
    "build_agentic_retriever",
    "run_query",
    "render_result_line",
    "render_no_results",
    "render_verbose_sections",
    "main",
]

#: 默认配置文件（仓库根 ``config/settings.yaml``，绝对路径不受 CWD 影响）。
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"

# 退出码语义。
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

#: 结果文本摘要的最大字符数（单行折叠）。
EXCERPT_CHARS = 160

#: 输出函数签名（默认 ``print``；测试注入 list.append 收集器）。
Emit = Callable[[str], None]


# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------


@dataclass
class QueryOutcome:
    """一次 CLI 查询的完整结果（人读渲染 + 机读 ``to_dict``）。"""

    query: str
    top_k: int
    results: list[RetrievalResult] = field(default_factory=list)
    dense: list[RetrievalResult] = field(default_factory=list)
    sparse: list[RetrievalResult] = field(default_factory=list)
    fused: list[RetrievalResult] = field(default_factory=list)
    collection: str | None = None
    rerank_applied: bool = False
    rerank_fallback: bool | None = None
    rerank_backend: str | None = None
    # ---- 第三路 agentic 中间结果（S2.5，仅 --verbose 且 --agentic 时渲染）----
    agentic_requested: bool = False
    agentic: list[RetrievalResult] = field(default_factory=list)
    agentic_triggered: bool | None = None
    agentic_trigger_reason: str | None = None
    agentic_source_files: list[str] = field(default_factory=list)
    agentic_total_llm_tokens: int | None = None
    agentic_elapsed_ms: float | None = None
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "top_k": self.top_k,
            "collection": self.collection,
            "results": [r.to_dict() for r in self.results],
            "dense_ids": [r.chunk_id for r in self.dense],
            "sparse_ids": [r.chunk_id for r in self.sparse],
            "fused_ids": [r.chunk_id for r in self.fused],
            "rerank_applied": self.rerank_applied,
            "rerank_fallback": self.rerank_fallback,
            "rerank_backend": self.rerank_backend,
            "trace_id": self.trace_id,
        }


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器（``--query`` 必填，其余可选）。"""
    parser = argparse.ArgumentParser(
        prog="query",
        description="对本地知识库执行混合检索（Dense + Sparse + RRF + 可选 Rerank）。",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="查询文本（必填）",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="返回结果数量（默认取 settings retrieval.top_k_final）",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="限定检索集合（写入 filters 供 Pre/Post-filter）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示各阶段中间结果（Dense/Sparse/Fusion/Rerank）",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="跳过 Reranker 精排阶段（直接返回融合排名）",
    )
    parser.add_argument(
        "--agentic",
        action="store_true",
        help="启用第三路 Agentic 检索（需 settings.agentic_search.enabled=true）",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"配置文件路径（默认 {DEFAULT_CONFIG_PATH}）",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析 argv（缺省取 ``sys.argv[1:]``；非法参数由 argparse 退出码 2 处理）。"""
    return build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# 查询栈构建与执行
# ---------------------------------------------------------------------------


def build_search(settings: Settings, *, agentic_retriever: Any | None = None) -> HybridSearch:
    """按 ``Settings`` 构建默认混合检索栈（HybridSearch 内部按需创建组件）。

    Args:
        agentic_retriever: 可选第三路 agentic 检索器（S2.5 ``--agentic``
            注入；``None`` 时与基线行为逐字节一致）。
    """
    return HybridSearch(settings, agentic_retriever=agentic_retriever)


class AgenticRouteAdapter:
    """把 ``BaseAgenticSearch`` 包装成第三路 ``retrieve`` 形态（S2.5）。

    ``HybridSearch`` 的 ``agentic_retriever`` 注入契约（S2.3）是鸭子类型
    ``retrieve(query, top_k, trace=...) -> list[RetrievalResult]``；本适配器
    在其下调用 ``backend.search(AgenticSearchRequest)`` 并经
    ``evidence_to_retrieval_results`` 转换证据。``last_response`` /
    ``last_elapsed_ms`` 供 ``--verbose`` 展示 Token 与耗时。

    落点说明：仅 CLI 需要此包装，故收敛在脚本内（最小侵入）；如后续
    MCP tool 等入口也要注入第三路，可平移到 ``libs/agentic_search/``。
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self.last_response: Any | None = None
        self.last_elapsed_ms: float | None = None

    @property
    def backend_name(self) -> str:
        return getattr(self._backend, "backend_name", type(self._backend).__name__)

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        trace: Any = None,
    ) -> list[RetrievalResult]:
        """执行一次 Agentic 检索并转换为 ``RetrievalResult`` 列表。"""
        started = time.perf_counter()
        response = self._backend.search(AgenticSearchRequest(query=query))
        self.last_response = response
        self.last_elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return evidence_to_retrieval_results(response, top_k=top_k)


def build_agentic_retriever(settings: Settings) -> AgenticRouteAdapter:
    """按 ``settings.agentic_search.backend`` 构建第三路检索适配器（S2.5）。"""
    return AgenticRouteAdapter(AgenticSearchFactory.create(settings))


def build_reranker(settings: Settings) -> Reranker:
    """按 ``Settings`` 构建精排器（backend 由 ``rerank.backend`` 驱动）。"""
    return Reranker(settings)


def run_query(
    search: Any,
    reranker: Any | None,
    query: str,
    top_k: int | None = None,
    *,
    collection: str | None = None,
    verbose: bool = False,
    out: Emit | None = None,
    log_file: str | None = None,
    agentic: bool = False,
    agentic_adapter: Any | None = None,
) -> QueryOutcome:
    """执行一次查询并渲染输出（组件可注入，离线可测）。

    Args:
        search: 任何具备 ``search(query, top_k, filters, trace=...)`` 签法
            的对象（真 ``HybridSearch`` 或测试 fake；fake 需自行提供
            ``last_dense_results`` / ``last_sparse_results`` /
            ``last_fused_results`` 供 verbose 展示，缺失时静默跳过）。
        reranker: 任何具备 ``rerank(query, results, trace=...)`` 签法的对象；
            ``None`` 或空结果时跳过精排（等价 ``--no-rerank``）。
        query: 查询文本。
        top_k: 返回数量（``None`` 透传给 search 由其取默认）。
        collection: 可选集合过滤。
        verbose: True 时输出各阶段中间结果。
        out: 输出函数（缺省 ``print``）。

    Returns:
        :class:`QueryOutcome`（含最终结果与各阶段中间产物）。
    """
    emit = print if out is None else out
    trace = TraceContext(
        trace_type=TRACE_TYPE_QUERY,
        metadata={"query": query, "collection": collection},
    )
    filters = {"collection": collection} if collection else None

    results = search.search(query, top_k=top_k, filters=filters, trace=trace)

    outcome = QueryOutcome(query=query, top_k=top_k or 0, collection=collection)
    outcome.dense = list(getattr(search, "last_dense_results", []) or [])
    outcome.sparse = list(getattr(search, "last_sparse_results", []) or [])
    outcome.fused = list(getattr(search, "last_fused_results", []) or [])

    if reranker is not None and results:
        outcome.rerank_applied = True
        outcome.rerank_backend = getattr(reranker, "backend_name", None)
        results = reranker.rerank(query, results, trace=trace)
        outcome.rerank_fallback = bool(getattr(reranker, "last_fallback", False))

    outcome.results = list(results)
    outcome.trace_id = trace.trace_id

    # ---- 第三路 agentic 中间结果采集（S2.5，仅 --agentic 时展示）----
    if agentic:
        outcome.agentic_requested = True
        outcome.agentic = list(getattr(search, "last_agentic_results", []) or [])
        decision = getattr(search, "last_trigger_decision", None)
        if decision is not None:
            outcome.agentic_triggered = bool(decision.triggered)
            outcome.agentic_trigger_reason = getattr(decision, "reason", None)
        # Token/耗时取**真正执行查询**的适配器：注入 search 时其内部
        # ``_agentic_retriever`` 与 main 新建的 adapter 不是同一实例。
        telemetry_adapter = getattr(search, "_agentic_retriever", None) or agentic_adapter
        if telemetry_adapter is not None:
            response = getattr(telemetry_adapter, "last_response", None)
            if response is not None:
                outcome.agentic_source_files = list(getattr(response, "source_files", []) or [])
                outcome.agentic_total_llm_tokens = getattr(response, "total_llm_tokens", None)
            outcome.agentic_elapsed_ms = getattr(telemetry_adapter, "last_elapsed_ms", None)

    if log_file:
        persist_trace(trace, log_file=log_file)

    if verbose:
        emit(render_verbose_sections(outcome))
        emit("")

    if not outcome.results:
        emit(render_no_results())
    else:
        emit(f'query: {query!r}  top_k={len(outcome.results)}'
             + (f'  collection={collection!r}' if collection else ''))
        for index, result in enumerate(outcome.results, start=1):
            emit(render_result_line(index, result))
    return outcome


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def render_result_line(index: int, result: RetrievalResult, *, excerpt_chars: int = EXCERPT_CHARS) -> str:
    """渲染单条结果：序号 + score + 来源 + 页码 + 单行文本摘要。"""
    source = result.metadata.get("source_path") or result.metadata.get("source") or "-"
    page = result.metadata.get("page")
    location = f"  source={source}"
    if page is not None:
        location += f"  page={page}"
    excerpt = " ".join(result.text.split())  # 折叠空白为单行
    if len(excerpt) > excerpt_chars:
        excerpt = excerpt[: excerpt_chars - 1] + "…"
    return f"[{index}] score={result.score:.4f}{location}\n    {excerpt}"


def render_no_results() -> str:
    """无命中时的友好提示（spec D7：提示先摄取数据）。"""
    return (
        "未找到相关文档。请先运行摄取入口建立索引，例如：\n"
        "  python scripts/ingest.py --path <文件或目录> --collection <集合名>"
    )


def _render_stage_items(items: Sequence[RetrievalResult]) -> str:
    if not items:
        return "    (无)"
    return "\n".join(f"    {r.chunk_id}  score={r.score:.4f}" for r in items)


def render_verbose_sections(outcome: QueryOutcome) -> str:
    """渲染 verbose 各阶段中间结果（Dense/Sparse/Fusion/Rerank）。"""
    lines = [
        f"--- dense recall ({len(outcome.dense)} hits) ---",
        _render_stage_items(outcome.dense),
        f"--- sparse recall ({len(outcome.sparse)} hits) ---",
        _render_stage_items(outcome.sparse),
        f"--- fusion ({len(outcome.fused)} fused) ---",
        _render_stage_items(outcome.fused),
    ]
    if outcome.rerank_applied:
        fallback = "true" if outcome.rerank_fallback else "false"
        lines.append(
            f"--- rerank (backend={outcome.rerank_backend}, fallback={fallback}) ---"
        )
    else:
        lines.append("--- rerank (skipped) ---")
    if outcome.agentic_requested:
        lines.append(render_agentic_section(outcome))
    return "\n".join(lines)


def render_agentic_section(outcome: QueryOutcome) -> str:
    """渲染 verbose 第三路 agentic 段（S2.5：触发状态/来源文件/Token/耗时）。

    真实 HTTP 后端在 sidecar 进程内运行（本项目侧无 stdout 污染问题），
    正常 print 即可；输出风格对齐 dense/sparse/fusion/rerank 各段。
    """
    if not outcome.agentic_triggered:
        reason = outcome.agentic_trigger_reason or "no trigger decision"
        return f"--- agentic (not triggered: {reason}) ---"
    # 来源优先取响应 source_files（sidecar 引用文件列表），缺失时从
    # 证据 metadata.source_path 兜底（Fake 后端无 source_files 时仍可展示）。
    sources = outcome.agentic_source_files or [
        str(r.metadata.get("source_path") or "-") for r in outcome.agentic
    ]
    tokens = (
        str(outcome.agentic_total_llm_tokens)
        if outcome.agentic_total_llm_tokens is not None
        else "-"
    )
    elapsed = (
        f"{outcome.agentic_elapsed_ms:.1f}ms"
        if outcome.agentic_elapsed_ms is not None
        else "-"
    )
    return "\n".join(
        [
            (
                f"--- agentic ({len(outcome.agentic)} evidences, "
                f"sources=[{', '.join(sources)}], tokens={tokens}, elapsed={elapsed}) ---"
            ),
            _render_stage_items(outcome.agentic),
        ]
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    search: Any | None = None,
    reranker: Any | None = None,
    out: Emit | None = None,
    agentic_backend: Any | None = None,
) -> int:
    """CLI 入口（返回进程退出码，不直接 ``sys.exit``）。

    Args:
        argv: 参数列表（缺省 ``sys.argv[1:]``）。
        search: 已构建的 ``HybridSearch``（测试注入；缺省按 ``--config``
            加载 ``Settings`` 后经 :func:`build_search` 构建）。
        reranker: 已构建的 ``Reranker``（测试注入；缺省且未指定
            ``--no-rerank`` 时构建）。注意：显式传入 ``reranker=None``
            与未传无法区分时按"构建默认"处理；离线测试请传入 sentinel
            或 ``--no-rerank``。
        out: 输出函数（缺省 ``print``）。
        agentic_backend: 已构建的 ``BaseAgenticSearch``（``--agentic`` 时
            包装成第三路检索器注入；测试注入 fake 后端离线运行；缺省按
            ``settings.agentic_search.backend`` 经工厂构建）。

    Returns:
        ``0`` 查询完成（含无命中）；``1`` 检索链路失败；
        ``2`` 用法/配置错误。
    """
    emit = print if out is None else out
    args = parse_args(argv)

    need_settings = search is None or args.agentic or (reranker is None and not args.no_rerank)
    settings: Settings | None = None
    if need_settings:
        try:
            settings = load_settings(args.config)
        except (ConfigError, FileNotFoundError) as exc:
            emit(f"[usage] 配置加载失败: {exc}")
            return EXIT_USAGE

    # ---- 第三路 agentic（S2.5）：enabled=false 明确报错（退出码 2）----
    agentic_adapter: Any | None = None
    if args.agentic:
        assert settings is not None  # need_settings 含 args.agentic
        if not settings.agentic_search.enabled:
            emit(
                "[usage] agentic 第三路未启用：请把 config/settings.yaml 中 "
                "agentic_search.enabled 改为 true（当前 backend="
                f"{settings.agentic_search.backend!r}）后重试"
            )
            return EXIT_USAGE
        if agentic_backend is not None:
            agentic_adapter = AgenticRouteAdapter(agentic_backend)
        else:
            try:
                agentic_adapter = build_agentic_retriever(settings)
            except Exception as exc:
                emit(f"[usage] Agentic 后端初始化失败: {exc}")
                return EXIT_USAGE

    if search is None:
        try:
            search = build_search(settings, agentic_retriever=agentic_adapter)
        except Exception as exc:
            emit(f"[usage] 查询组件初始化失败: {exc}")
            return EXIT_USAGE

    if reranker is None and not args.no_rerank:
        # 默认构建精排器；构建失败按用法/配置错误处理（可用 --no-rerank 规避）。
        try:
            reranker = build_reranker(settings)
        except Exception as exc:
            emit(f"[usage] Reranker 初始化失败（可用 --no-rerank 规避）: {exc}")
            return EXIT_USAGE

    try:
        run_query(
            search,
            reranker,
            args.query,
            top_k=args.top_k,
            collection=args.collection,
            verbose=args.verbose,
            out=emit,
            log_file=settings.observability.log_file if settings is not None else None,
            agentic=args.agentic,
            agentic_adapter=agentic_adapter,
        )
    except QueryProcessingError as exc:
        emit(f"[usage] 查询无效: {exc}")
        return EXIT_USAGE
    except (TypeError, ValueError) as exc:
        emit(f"[usage] 参数无效: {exc}")
        return EXIT_USAGE
    except Exception as exc:  # HybridSearchError 及其余运行时故障
        emit(f"[error] 检索失败: {type(exc).__name__}: {exc}")
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
