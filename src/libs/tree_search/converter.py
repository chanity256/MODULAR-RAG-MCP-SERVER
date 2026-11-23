"""TreeEvidence → RetrievalResult 转换与 sidecar 响应解析 — 阶段 PI3。

metadata 契约（D8）：``retrieval_route="longdoc"`` / ``source_path`` /
``page``（= ``page_start``）/ ``page_end`` / ``section`` / ``line_start`` /
``line_end`` / ``node_id``。``chunk_id`` 为 ``pageindex:{doc_id}:{node_id}``
（``RetrievalResult.chunk_id`` 须非空字符串，且是前端引用 React key 的
组成部分；``tree_loop`` 对同一节点去重，保证一次检索内唯一）。
"""

from __future__ import annotations

from typing import Any

from core.types import RetrievalResult
from libs.tree_search.base_tree_search import (
    TreeEvidence,
    TreeSearchResponse,
    TreeSearchResponseError,
)

__all__ = [
    "parse_search_data",
    "make_tree_chunk_id",
    "tree_evidence_to_retrieval_results",
]


def _as_int(value: Any) -> int | None:
    """宽松整数解析：int 原样、bool 拒绝、float 取整值、其余 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _as_float(value: Any) -> float | None:
    """宽松数值解析：int/float 原样（bool 拒绝），其余 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _parse_evidence(raw: Any) -> TreeEvidence | None:
    """单条证据解析；缺必需字段或类型不符返回 None（调用方降级跳过）。"""
    if not isinstance(raw, dict):
        return None
    source_path = raw.get("source_path")
    doc_id = raw.get("doc_id")
    node_id = raw.get("node_id")
    text = raw.get("text")
    if not all(isinstance(v, str) and v for v in (source_path, doc_id, node_id)):
        return None
    if not isinstance(text, str):
        return None
    return TreeEvidence(
        source_path=source_path,
        doc_id=doc_id,
        node_id=node_id,
        text=text,
        score=_as_float(raw.get("score")),
        section=_as_str_or_none(raw.get("section")),
        page_start=_as_int(raw.get("page_start")),
        page_end=_as_int(raw.get("page_end")),
        line_start=_as_int(raw.get("line_start")),
        line_end=_as_int(raw.get("line_end")),
    )


def parse_search_data(data: Any) -> TreeSearchResponse:
    """把 sidecar ``/search`` 响应的 ``data`` 解析为响应契约。

    Args:
        data: HTTP 响应信封中的 ``data`` 字段，期望含 ``evidences`` /
            ``llm_call_count`` / ``total_llm_tokens`` / ``telemetry``。

    Raises:
        TreeSearchResponseError: ``data`` 非 dict、``evidences`` 缺失或非
            list、``telemetry`` 非 dict 非 None（结构畸形）。
    """
    if not isinstance(data, dict):
        raise TreeSearchResponseError(
            f"sidecar data must be a dict, got {type(data).__name__}"
        )
    raw_evidences = data.get("evidences")
    if raw_evidences is None or not isinstance(raw_evidences, list):
        raise TreeSearchResponseError(
            f"sidecar data missing 'evidences' list field, got {type(raw_evidences).__name__}"
        )
    evidences = [e for e in (_parse_evidence(raw) for raw in raw_evidences) if e is not None]
    telemetry = data.get("telemetry")
    if telemetry is not None and not isinstance(telemetry, dict):
        raise TreeSearchResponseError(
            f"sidecar telemetry must be a dict or null, got {type(telemetry).__name__}"
        )
    return TreeSearchResponse(
        evidences=evidences,
        llm_call_count=_as_int(data.get("llm_call_count")),
        total_llm_tokens=_as_int(data.get("total_llm_tokens")),
        telemetry=dict(telemetry) if isinstance(telemetry, dict) else None,
    )


def make_tree_chunk_id(evidence: TreeEvidence) -> str:
    """确定性证据 ID：``pageindex:{doc_id}:{node_id}``（converter 细则）。"""
    return f"pageindex:{evidence.doc_id}:{evidence.node_id}"


def tree_evidence_to_retrieval_results(
    response: TreeSearchResponse,
    *,
    top_k: int | None = None,
) -> list[RetrievalResult]:
    """把树证据列表转换为 ``RetrievalResult``（保序；top_k 可选截断）。

    metadata 契约见模块 docstring（D8）。score 缺失的证据回退 0.0
    （RetrievalResult 构造要求非数值 score fail-fast）。
    """
    if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
        raise ValueError(f"top_k must be a positive integer or None, got {top_k!r}")
    results: list[RetrievalResult] = []
    for evidence in response.evidences:
        results.append(
            RetrievalResult(
                chunk_id=make_tree_chunk_id(evidence),
                score=evidence.score if evidence.score is not None else 0.0,
                text=evidence.text,
                metadata={
                    "retrieval_route": "longdoc",
                    "source_path": evidence.source_path,
                    "page": evidence.page_start,
                    "page_end": evidence.page_end,
                    "section": evidence.section,
                    "line_start": evidence.line_start,
                    "line_end": evidence.line_end,
                    "node_id": evidence.node_id,
                },
            )
        )
    if top_k is not None:
        results = results[:top_k]
    return results
