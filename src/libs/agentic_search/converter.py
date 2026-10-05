"""sidecar 响应解析与证据转换 — 阶段 S1.4/S1.5。

两个职责：

1. :func:`parse_search_context`：把 sidecar ``/api/v1/search``
   （``response_format=context``）返回的 ``data`` dict 防御式解析为
   :class:`AgenticSearchResponse`。``snippets`` 的 dict/str 两形态、
   ``cluster=None``、telemetry 缺失都必须容忍（spec 4.6）。
2. :func:`evidence_to_retrieval_results`：把证据转换为
   :class:`~core.types.RetrievalResult`，供 ResponseBuilder/Citation
   复用与（P2 门控后）N 路 RRF 融合。

确定性 ID：``agentic:{sha1(source_path|start|end|snippet)[:12]}``，
不与 C4 chunk_id（``{doc_id}_{index}_{hash8}``）空间冲突；同一输入
重复转换 id 稳定（离线可测）。
"""

from __future__ import annotations

import hashlib
import sys
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from core.types import RetrievalResult
from libs.agentic_search.base_agentic_search import (
    AgenticEvidence,
    AgenticSearchResponse,
    AgenticSearchResponseError,
)

__all__ = ["parse_search_context", "evidence_to_retrieval_results", "make_agentic_id"]


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _parse_snippet(raw: Any) -> tuple[str, int | None, int | None, float | None]:
    """兼容 snippets 的 dict/str 两形态（spec 4.6）。

    dict 形如 ``{"snippet", "start", "end", "score", "reasoning"}``，
    start/end 是抽取文本上的字符偏移；str 形态无偏移无分数。
    """
    if isinstance(raw, str):
        return raw, None, None, None
    if isinstance(raw, dict):
        snippet = raw.get("snippet")
        if not isinstance(snippet, str):
            raise AgenticSearchResponseError(
                f"snippet dict missing 'snippet' string field: {type(snippet).__name__}"
            )
        return snippet, _as_int(raw.get("start")), _as_int(raw.get("end")), _as_float(raw.get("score"))
    raise AgenticSearchResponseError(f"snippet must be str or dict, got {type(raw).__name__}")


def parse_search_context(data: dict[str, Any], *, mode: str = "FAST") -> AgenticSearchResponse:
    """把 sidecar ``data``（``context.to_dict()`` 投影）解析为响应契约。

    Args:
        data: HTTP 响应中的 ``data`` 字段（期望含 ``answer`` 与可选
            ``cluster.evidences`` / ``cluster.search_results`` /
            ``total_llm_tokens`` / ``llm_usages`` / ``telemetry``）。
        mode: 本次请求模式（回填契约字段；sidecar 不回显 mode）。

    Raises:
        AgenticSearchResponseError: ``data`` 非 dict 或缺 ``answer``。
    """
    if not isinstance(data, dict):
        raise AgenticSearchResponseError(
            f"sidecar data must be a dict, got {type(data).__name__}"
        )
    answer = data.get("answer")
    if not isinstance(answer, str):
        # greeting 短路等路径 answer 恒为 str；缺失即契约破坏。
        raise AgenticSearchResponseError(
            f"sidecar data missing 'answer' string field, got {type(answer).__name__}"
        )

    evidences: list[AgenticEvidence] = []
    source_files: list[str] = []
    confidence: float | None = None
    cluster = data.get("cluster")
    if isinstance(cluster, dict):
        confidence = _as_float(cluster.get("confidence"))
        raw_results = cluster.get("search_results")
        if isinstance(raw_results, list):
            source_files = [r for r in raw_results if isinstance(r, str)]
        raw_evidences = cluster.get("evidences")
        if raw_evidences is not None and not isinstance(raw_evidences, list):
            raise AgenticSearchResponseError(
                f"cluster.evidences must be a list, got {type(raw_evidences).__name__}"
            )
        for raw in raw_evidences or []:
            if not isinstance(raw, dict):
                continue  # 单条畸形降级跳过，不阻塞整体响应
            file_or_url = raw.get("file_or_url")
            if not isinstance(file_or_url, str) or not file_or_url:
                continue
            snippets = raw.get("snippets") or []
            if not isinstance(snippets, list):
                snippets = []
            if snippets:
                for raw_snippet in snippets:
                    try:
                        text, start, end, score = _parse_snippet(raw_snippet)
                    except AgenticSearchResponseError:
                        continue  # 单条畸形降级跳过，不阻塞整体响应
                    evidences.append(
                        AgenticEvidence(
                            source_path=file_or_url,
                            snippet=text,
                            score=score,
                            char_start=start,
                            char_end=end,
                        )
                    )
            else:
                # 无 snippet 时保留 summary 作为证据文本（保证引用可回溯）。
                summary = raw.get("summary")
                evidences.append(
                    AgenticEvidence(
                        source_path=file_or_url,
                        snippet=summary if isinstance(summary, str) else "",
                        score=None,
                    )
                )

    llm_call_count = len(data["llm_usages"]) if isinstance(data.get("llm_usages"), list) else None
    total_llm_tokens = _as_int(data.get("total_llm_tokens"))

    return AgenticSearchResponse(
        answer=answer,
        evidences=evidences,
        mode=mode,
        source_files=source_files,
        confidence=confidence,
        llm_call_count=llm_call_count,
        total_llm_tokens=total_llm_tokens,
    )


def make_agentic_id(evidence: AgenticEvidence) -> str:
    """确定性证据 ID：``agentic:{sha1(source_path|start|end|snippet)[:12]}``。"""
    key = "|".join(
        [
            evidence.source_path,
            str(evidence.char_start) if evidence.char_start is not None else "",
            str(evidence.char_end) if evidence.char_end is not None else "",
            evidence.snippet,
        ]
    )
    return f"agentic:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def evidence_to_retrieval_results(
    response: AgenticSearchResponse,
    *,
    top_k: int | None = None,
) -> list[RetrievalResult]:
    """把证据列表转换为 ``RetrievalResult``（保序；top_k 可选截断）。

    metadata 契约（spec S1.5）：``retrieval_route="agentic"`` /
    ``source_path`` / ``char_start`` / ``char_end`` /
    ``agentic_confidence`` / ``mode``。score 缺失的证据回退 0.0
    （RetrievalResult 构造要求非数值 score fail-fast）。
    """
    if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
        raise ValueError(f"top_k must be a positive integer or None, got {top_k!r}")
    results: list[RetrievalResult] = []
    for evidence in response.evidences:
        results.append(
            RetrievalResult(
                chunk_id=make_agentic_id(evidence),
                score=evidence.score if evidence.score is not None else 0.0,
                text=evidence.snippet,
                metadata={
                    "retrieval_route": "agentic",
                    "source_path": evidence.source_path,
                    "char_start": evidence.char_start,
                    "char_end": evidence.char_end,
                    "agentic_confidence": response.confidence,
                    "mode": response.mode,
                },
            )
        )
    if top_k is not None:
        results = results[:top_k]
    return results
