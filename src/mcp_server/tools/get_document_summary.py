"""MCP Tool：get_document_summary — 阶段 E5。

按 ``doc_id`` 返回文档摘要与元信息（DEV_SPEC 3.2.4）：title / summary /
tags（来自摄取期 ``MetadataEnricher`` 注入的 chunk metadata）+ 来源与
chunk 统计。

``doc_id`` 语义：本项目的文档标识是 ``source_path``（ingestion /
DocumentManager 全链路共用），因此 tool 以 source_path 作为 doc_id。

设计要点：
- **懒初始化**：Chroma client 首次调用时才构建（与 E3/E4 同纪律）；
  默认扫描所有 collection 的 ``metadata.source_path``（本项目采用
  单 collection + metadata["collection"] 标记的存储布局）。
- **可注入**：``reader(doc_id) -> list[chunk dict]`` 可注入（测试
  离线零持久化）。
- **规范错误**：doc_id 不存在时返回 ``isError: true`` 的 tool result
  （参数本身合法，属资源不存在，不用 -32602）；参数非法抛
  ``ToolInvalidParams``。
"""

from __future__ import annotations

import sys
from collections import Counter
from typing import Any, Callable

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from mcp_server.protocol_handler import ToolDefinition, ToolInvalidParams

__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "TOOL_INPUT_SCHEMA",
    "GetDocumentSummaryTool",
    "create_get_document_summary_definition",
]

TOOL_NAME = "get_document_summary"
TOOL_DESCRIPTION = (
    "Get the summary and metadata of an indexed document: title, summary, "
    "tags, source path, collection and chunk count. `doc_id` is the "
    "document's source path as reported in query_knowledge_hub citations."
)
TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_id": {
            "type": "string",
            "description": "Document identifier (its source path).",
        }
    },
    "required": ["doc_id"],
}

#: chunk 记录读取器：doc_id -> [{"id","text","metadata"}]（可注入）。
DocReader = Callable[[str], list[dict[str, Any]]]


def _default_reader_factory() -> DocReader:
    """构建默认 reader：扫描 Chroma 所有 collection 按 source_path 匹配。"""

    def reader(doc_id: str) -> list[dict[str, Any]]:
        import chromadb

        from core.settings import load_settings

        settings = load_settings()
        client = chromadb.PersistentClient(path=settings.vector_store.persist_path)
        records: list[dict[str, Any]] = []
        for collection in client.list_collections():
            name = collection.name if hasattr(collection, "name") else str(collection)
            got = collection.get(where={"source_path": doc_id}, include=["documents", "metadatas"])
            for cid, text, metadata in zip(
                got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []
            ):
                records.append({"id": cid, "text": text, "metadata": dict(metadata or {}), "collection": name})
        return records

    return reader


class GetDocumentSummaryTool:
    """get_document_summary 的可调用实现。"""

    def __init__(self, reader: DocReader | None = None) -> None:
        self._reader = reader

    def _get_reader(self) -> DocReader:
        if self._reader is None:
            self._reader = _default_reader_factory()
        return self._reader

    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """返回 MCP tool result；doc_id 不存在时 ``isError: true``。"""
        if not isinstance(arguments, dict):
            raise TypeError(f"arguments must be an object, got {type(arguments).__name__}")
        doc_id = arguments.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise ToolInvalidParams("'doc_id' must be a non-empty string")
        records = self._get_reader()(doc_id)
        if not records:
            return {
                "content": [{"type": "text", "text": f"Document not found: {doc_id}"}],
                "isError": True,
                "structuredContent": {"doc_id": doc_id, "found": False},
            }
        summary = self._summarize(doc_id, records)
        markdown = self._render_markdown(summary)
        return {
            "content": [{"type": "text", "text": markdown}],
            "structuredContent": summary,
        }

    @staticmethod
    def _summarize(doc_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        first = records[0]["metadata"]
        collections = Counter(r.get("collection") or first.get("collection") for r in records)
        tags = first.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        pages = sorted(
            {
                r["metadata"].get("page")
                for r in records
                if isinstance(r["metadata"].get("page"), int)
            }
        )
        return {
            "doc_id": doc_id,
            "found": True,
            "title": first.get("title") or doc_id,
            "summary": first.get("summary") or "",
            "tags": list(tags),
            "source_path": first.get("source_path") or doc_id,
            "collection": collections.most_common(1)[0][0],
            "chunk_count": len(records),
            "pages": [pages[0], pages[-1]] if pages else None,
        }

    @staticmethod
    def _render_markdown(summary: dict[str, Any]) -> str:
        lines = [f"# {summary['title']}", ""]
        lines.append(f"- **Source**: {summary['source_path']}")
        lines.append(f"- **Collection**: {summary['collection']}")
        lines.append(f"- **Chunks**: {summary['chunk_count']}")
        if summary["pages"]:
            lines.append(f"- **Pages**: {summary['pages'][0]}–{summary['pages'][1]}")
        if summary["tags"]:
            lines.append(f"- **Tags**: {', '.join(str(t) for t in summary['tags'])}")
        lines.append("")
        if summary["summary"]:
            lines.append("## Summary")
            lines.append("")
            lines.append(str(summary["summary"]))
        return "\n".join(lines).rstrip()


def create_get_document_summary_definition(reader: DocReader | None = None) -> ToolDefinition:
    """构建可注册到 ``ProtocolHandler`` 的 ``ToolDefinition``。"""
    return ToolDefinition(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        input_schema=TOOL_INPUT_SCHEMA,
        handler=GetDocumentSummaryTool(reader=reader),
    )
