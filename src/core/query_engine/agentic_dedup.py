"""Agentic 第三路后置去重与上下文扩展（阶段 S2.4）。

三路 RRF 融合后、返回前对 agentic 路存活条目做两步清理（纯函数式，
由 :class:`~core.query_engine.hybrid_search.HybridSearch` 在触发第三路
且融合结果含 agentic 条目时调用；未触发时路径零改动）：

1. **后置去重**：agentic 证据是文件内字符区间（无页码/行号），与
   Dense/Sparse 的 chunk 级结果可能指向同一文件的同一区域。判定规则：
   ``source_path`` 相同 **且** 文本重叠率 ≥
   :data:`TEXT_OVERLAP_DUPLICATE_THRESHOLD` 即视为重复——chunk 级结果
   保留，agentic 条目丢弃（chunk 级命中已覆盖该区域，agentic 仅作
   **补位**：同文件但文本不重叠的 agentic 条目保留）。
2. **上下文扩展**：去重后存活的 agentic snippet 按 ``char_start``/
   ``char_end`` 向宿主文件扩展至 ≈ ``chunk_size``（对齐 settings 的
   切分粒度，使 Cross-Encoder 对 agentic 与 chunk 级结果评分公平）。
   宿主文件读取必须经 :class:`~libs.agentic_search.path_guard.PathGuard`
   白名单校验（realpath 语义，fail-closed）；任何失败（文件不可读 /
   越权 / 偏移越界）都**保留原 snippet**，不抛错。

扩展后 metadata 保留原字段并追加扩展标注（``context_expanded`` /
``expanded_char_start`` / ``expanded_char_end``），便于 trace/调试。
"""

from __future__ import annotations

from difflib import SequenceMatcher

from core.types import RetrievalResult
from libs.agentic_search.path_guard import PathGuard
from observability.logger import get_logger

__all__ = [
    "TEXT_OVERLAP_DUPLICATE_THRESHOLD",
    "text_overlap_ratio",
    "dedup_and_expand",
]

# 文本重叠去重阈值：agentic snippet 与同文件 chunk 级结果的重叠率
# （短文本被长文本覆盖的比例，见 text_overlap_ratio）达到该值即视为
# 重复。agentic snippet 通常完整落在源 chunk 内（提取自宿主文件同一
# 区域），0.8 允许少量空白/换行差异；低于该值视为覆盖不同区域的补位。
TEXT_OVERLAP_DUPLICATE_THRESHOLD = 0.8

_logger = get_logger("modular-rag.query_engine.agentic_dedup")


def text_overlap_ratio(shorter: str, longer: str) -> float:
    """计算文本重叠率：``shorter`` 被 ``longer`` 覆盖的字符比例 ∈ [0, 1]。

    基于最长匹配块覆盖（非编辑距离 ratio）：包含关系（snippet 是 chunk
    的子串）得 1.0，无公共字符得 0.0。空文本重叠率为 0.0。
    """
    if not shorter or not longer:
        return 0.0
    a, b = (shorter, longer) if len(shorter) <= len(longer) else (longer, shorter)
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    covered = sum(block.size for block in matcher.get_matching_blocks())
    return covered / len(a)


def _is_agentic(result: RetrievalResult) -> bool:
    """判定是否 agentic 路条目（metadata 契约 ``retrieval_route="agentic"``）。"""
    return result.metadata.get("retrieval_route") == "agentic"


def _expand_snippet(
    result: RetrievalResult,
    *,
    chunk_size: int,
    path_guard: PathGuard | None,
) -> RetrievalResult:
    """把单条 agentic 结果的 snippet 向宿主文件扩展至 ≈ ``chunk_size``。

    失败（文件不可读 / PathGuard 越权 / 偏移越界 / 无偏移）时原样返回，
    不抛错——扩展是尽力而为的评分公平化，不是召回的正确性前提。
    """
    meta = result.metadata
    start, end = meta.get("char_start"), meta.get("char_end")
    source_path = meta.get("source_path")
    if not (
        isinstance(start, int)
        and isinstance(end, int)
        and not isinstance(start, bool)
        and not isinstance(end, bool)
        and isinstance(source_path, str)
        and source_path
        and 0 <= start < end
    ):
        return result  # 无偏移/偏移非法：保留原 snippet
    if end - start >= chunk_size:
        return result  # 已达 chunk 粒度，无需扩展

    if path_guard is None:
        return result  # 无白名单守卫（allowed_roots 为空 / 构造失败）：fail-closed 不扩展
    try:
        # realpath 语义校验（符号链接逃逸 / 排除子串 / 白名单外均拒绝），
        # 返回归一化绝对路径作为读取对象。
        (resolved,) = path_guard.validate_paths([source_path])
        content = _read_text(resolved)
    except Exception as exc:  # 越权 / 不存在 / IO / 解码失败 → 保留原 snippet
        _logger.debug("agentic context expansion skipped for %r: %s", source_path, exc)
        return result
    if not content or end > len(content):
        return result  # 空文件 / 偏移越界（宿主文件已变更）：保留原 snippet

    span = end - start
    budget = chunk_size - span
    # 以 snippet 为中心向两侧扩展；一侧到边界时余量回补给另一侧，
    # 尽量凑满 ≈ chunk_size。
    left_limit, right_limit = start, len(content) - end
    left = min(left_limit, budget // 2)
    right = min(right_limit, budget - left)
    left = min(left_limit, budget - right)
    new_start, new_end = start - left, end + right
    if new_start >= new_end or new_end <= start:  # 防御：展开区间退化
        return result

    expanded = RetrievalResult(
        chunk_id=result.chunk_id,  # 确定性 ID 不变（trace 可关联扩展前后）
        score=result.score,
        text=content[new_start:new_end],
        metadata=dict(meta),  # 保留原 metadata（含原 char_start/char_end）
    )
    expanded.metadata.update(
        {
            "context_expanded": True,
            "expanded_char_start": new_start,
            "expanded_char_end": new_end,
        }
    )
    return expanded


def _read_text(resolved: str) -> str:
    """按 UTF-8 读取白名单内文件（严格解码，失败由调用方兜底）。"""
    with open(resolved, encoding="utf-8") as fh:
        return fh.read()


def dedup_and_expand(
    results: list[RetrievalResult],
    *,
    chunk_size: int,
    path_guard: PathGuard | None = None,
    overlap_threshold: float = TEXT_OVERLAP_DUPLICATE_THRESHOLD,
) -> list[RetrievalResult]:
    """对三路融合结果做 agentic 条目去重与上下文扩展（纯函数，保序）。

    Args:
        results: 融合后的结果列表（dense/sparse/agentic 混合）。
        chunk_size: 扩展目标长度（对齐 ``settings.splitter.chunk_size``）。
        path_guard: 宿主文件白名单守卫；``None`` 时跳过扩展（fail-closed）。
        overlap_threshold: 文本重叠去重阈值（测试可注入以覆盖边界）。

    Returns:
        新列表：chunk 级结果原样保留；与 chunk 级结果同文件且文本重叠率
        达阈值的 agentic 条目被丢弃；存活的 agentic 条目尽力扩展。
    """
    # 同文件 chunk 级命中索引：source_path -> chunk 文本列表（仅非 agentic
    # 条目；dense/sparse chunk metadata 契约含 source_path）。
    chunk_texts_by_source: dict[str, list[str]] = {}
    for result in results:
        if _is_agentic(result):
            continue
        source = result.metadata.get("source_path")
        if isinstance(source, str) and source:
            chunk_texts_by_source.setdefault(source, []).append(result.text)

    kept: list[RetrievalResult] = []
    for result in results:
        if _is_agentic(result):
            source = result.metadata.get("source_path")
            texts = chunk_texts_by_source.get(source, []) if isinstance(source, str) else []
            if any(
                text_overlap_ratio(result.text, chunk_text) >= overlap_threshold
                for chunk_text in texts
            ):
                _logger.debug(
                    "drop duplicate agentic evidence (source=%r, overlap>=%.2f)",
                    source, overlap_threshold,
                )
                continue  # 同文件 chunk 级已命中同区域：丢弃重复，chunk 优先
            kept.append(_expand_snippet(result, chunk_size=chunk_size, path_guard=path_guard))
        else:
            kept.append(result)
    return kept
