"""MCP Tool：list_collections — 阶段 E4。

列出知识库中可用的文档集合（DEV_SPEC 3.2.4）。集合的真实来源是
向量库（Chroma 持久化目录下的 collections），而非 ``data/documents/``
目录——摄取管线按 collection 写入 Chroma，Chroma 才是权威账本。

设计要点：
- **懒初始化**：Chroma client 首次调用时才构建（与 E3 同纪律）。
- **可注入**：任何具备 ``list_collections()`` 签法的 client 均可注入
  （测试用 fake，离线零持久化）。
- **附带统计**：每个集合返回 ``chunk_count``（Chroma collection.count，
  即向量条数=chunk 数）；文档级统计延后到 DocumentManager（C 阶段）接入。
- 空集合列表返回友好提示而非空数组。
"""

from __future__ import annotations

import sys
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from mcp_server.protocol_handler import ToolDefinition

__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "TOOL_INPUT_SCHEMA",
    "ListCollectionsTool",
    "create_list_collections_definition",
    "EMPTY_COLLECTIONS_MESSAGE",
]

TOOL_NAME = "list_collections"
TOOL_DESCRIPTION = (
    "List the document collections available in the knowledge hub, "
    "with per-collection chunk counts. Use this to discover valid "
    "collection names before calling query_knowledge_hub."
)
TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
}

#: 无集合时的友好提示。
EMPTY_COLLECTIONS_MESSAGE = (
    "No collections found in the knowledge hub.\n\n"
    "Suggestions:\n"
    "- Ingest documents first with `python scripts/ingest.py`;"
)


class ListCollectionsTool:
    """list_collections 的可调用实现（协议无关）。"""

    def __init__(self, client: Any | None = None, *, settings: Any | None = None) -> None:
        """Args:
            client: 具备 ``list_collections()`` 的向量库 client（缺省首次
                调用时按 ``Settings.vector_store.persist_path`` 构建
                ``chromadb.PersistentClient``）。
            settings: 全局配置（构建默认 client 用）。
        """
        self._client = client
        self._settings = settings

    def _get_client(self) -> Any:
        if self._client is None:
            import chromadb

            if self._settings is None:
                from core.settings import load_settings

                self._settings = load_settings()
            self._client = chromadb.PersistentClient(path=self._settings.vector_store.persist_path)
        return self._client

    def __call__(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """返回 MCP tool result：集合名 + chunk_count 统计。"""
        if arguments is not None and not isinstance(arguments, dict):
            raise TypeError(f"arguments must be an object, got {type(arguments).__name__}")
        collections = self._collect(self._get_client())
        if not collections:
            return {
                "content": [{"type": "text", "text": EMPTY_COLLECTIONS_MESSAGE}],
                "structuredContent": {"collections": []},
            }
        lines = ["Available collections in the knowledge hub:", ""]
        lines += [f"- **{name}** ({count} chunks)" for name, count in collections]
        markdown = "\n".join(lines)
        return {
            "content": [{"type": "text", "text": markdown}],
            "structuredContent": {
                "collections": [
                    {"name": name, "chunk_count": count} for name, count in collections
                ]
            },
        }

    @staticmethod
    def _collect(client: Any) -> list[tuple[str, int]]:
        """从 client 提取 (name, chunk_count) 列表（Chroma collection 对象）。"""
        entries: list[tuple[str, int]] = []
        for collection in client.list_collections():
            name = collection.name if hasattr(collection, "name") else str(collection)
            try:
                count = int(collection.count())
            except Exception:  # 统计失败不阻塞列表（计为 0）
                count = 0
            entries.append((name, count))
        return entries


def create_list_collections_definition(
    client: Any | None = None, **kwargs: Any
) -> ToolDefinition:
    """构建可注册到 ``ProtocolHandler`` 的 ``ToolDefinition``。"""
    return ToolDefinition(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        input_schema=TOOL_INPUT_SCHEMA,
        handler=ListCollectionsTool(client=client, **kwargs),
    )
