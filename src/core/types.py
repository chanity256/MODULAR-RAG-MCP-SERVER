"""核心数据类型/契约（阶段 C1）。

定义全链路（ingestion → retrieval → mcp tools）共用的数据结构，作为
ingestion / core / mcp_server 各子模块的数据契约中心，避免类型散落导致耦合：

- ``Document``：Loader 产出的文档（text + metadata）。
- ``Chunk``：Splitter/Chunker 产出的文本片段（含溯源信息 source_ref）。
- ``ChunkRecord``：存储/检索载体（chunk + 稠密/稀疏向量；向量字段由 C8~C12 演进填充）。
- ``ImageRef``：``metadata.images`` 中单张图片的引用记录（多模态支持）。

契约要点：
- 所有类型可序列化（``to_dict`` / ``from_dict`` / ``to_json``）且字段稳定；
  ``from_dict`` 忽略未知字段，保证 metadata 增量扩展不破坏兼容。
- ``metadata`` 约定最少包含 ``source_path``，其余字段允许增量扩展。
- 多模态规范（见 DEV_SPEC C1）：
  - ``metadata.images`` 结构：
    ``List[{"id", "path", "page", "text_offset", "text_length", "position"}]``
  - ``id`` 建议格式：``{doc_hash}_{page}_{seq}``（见 ``make_image_id``）
  - ``path`` 约定：``data/images/{collection}/{image_id}.png``
  - ``Document.text`` 中图片位置使用占位符 ``[IMAGE: {image_id}]``
    （见 ``IMAGE_PLACEHOLDER_TEMPLATE`` / ``image_placeholder``）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ImageRef",
    "Document",
    "Chunk",
    "ChunkRecord",
    "ProcessedQuery",
    "RetrievalResult",
    "IMAGE_PLACEHOLDER_TEMPLATE",
    "IMAGE_PLACEHOLDER_PATTERN",
    "image_placeholder",
    "make_image_id",
    "iter_image_placeholders",
    "parse_image_refs",
]


# ---------------------------------------------------------------------------
# 图片占位符与图片 ID 规范
# ---------------------------------------------------------------------------

#: 文本中图片占位符模板（出现在 ``Document.text`` / ``Chunk.text`` 中）。
IMAGE_PLACEHOLDER_TEMPLATE = "[IMAGE: {image_id}]"

#: 占位符匹配正则（image_id 内不允许空白与 ``]``）。
IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[IMAGE:\s*(?P<image_id>[^\]\s]+)\s*\]")


def image_placeholder(image_id: str) -> str:
    """按规范生成图片占位符文本，例如 ``image_placeholder("a1b2_0_1")``。"""
    return IMAGE_PLACEHOLDER_TEMPLATE.format(image_id=image_id)


def make_image_id(doc_hash: str, page: int, seq: int) -> str:
    """生成全局唯一图片 ID，建议格式：``{doc_hash}_{page}_{seq}``。"""
    return f"{doc_hash}_{page}_{seq}"


def iter_image_placeholders(text: str) -> list[tuple[str, int]]:
    """扫描文本中的全部图片占位符。

    Returns:
        按出现顺序排列的 ``(image_id, start_offset)`` 列表；
        ``start_offset`` 为占位符在文本中的起始字符位置（从 0 开始计数）。
    """
    return [(m.group("image_id"), m.start()) for m in IMAGE_PLACEHOLDER_PATTERN.finditer(text)]


# ---------------------------------------------------------------------------
# 构造期轻量校验
# ---------------------------------------------------------------------------


def _require_id(value: Any, type_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{type_name}.id must be a non-empty string, got {value!r}")
    return value


def _require_text(value: Any, type_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{type_name}.text must be a string, got {type(value).__name__}")
    return value


def _require_metadata(value: Any, type_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{type_name}.metadata must be a dict, got {type(value).__name__}")
    return value


# ---------------------------------------------------------------------------
# 核心数据类型
# ---------------------------------------------------------------------------


@dataclass
class ImageRef:
    """``metadata.images`` 中单张图片的引用记录（多模态支持）。

    Attributes:
        id: 全局唯一图片标识符（建议 ``{doc_hash}_{page}_{seq}``）。
        path: 图片文件存储路径（约定 ``data/images/{collection}/{image_id}.png``）。
        page: 图片在原文档中的页码（可选，适用于 PDF 等分页文档）。
        text_offset: 占位符在 ``Document.text`` 中的起始字符位置（从 0 开始计数）。
        text_length: 占位符字符长度（通常为 ``len("[IMAGE: {image_id}]")``）。
        position: 图片在原文档中的物理位置信息（可选，如 PDF 坐标/尺寸）。
    """

    id: str
    path: str
    text_offset: int = 0
    text_length: int = 0
    page: int | None = None
    position: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError(f"ImageRef.id must be a non-empty string, got {self.id!r}")
        if not isinstance(self.path, str) or not self.path:
            raise ValueError(f"ImageRef.path must be a non-empty string, got {self.path!r}")

    def to_dict(self) -> dict[str, Any]:
        """序列化为规范约定的六字段 dict（键集合稳定）。"""
        return {
            "id": self.id,
            "path": self.path,
            "page": self.page,
            "text_offset": self.text_offset,
            "text_length": self.text_length,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageRef:
        """从 dict 反序列化；忽略未知字段，缺失可选字段走默认值。"""
        return cls(
            id=data["id"],
            path=data["path"],
            text_offset=int(data.get("text_offset", 0) or 0),
            text_length=int(data.get("text_length", 0) or 0),
            page=data.get("page"),
            position=dict(data.get("position") or {}),
        )


@dataclass
class Document:
    """Loader 产出的文档对象。

    Attributes:
        id: 文档唯一标识（如 doc_hash）。
        text: 全文文本；图片位置以 ``[IMAGE: {image_id}]`` 占位符标记。
        metadata: 元数据，约定最少包含 ``source_path``；
            含图片时另含 ``images`` 字段（``List[ImageRef.to_dict()]``）。
    """

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_id(self.id, "Document")
        _require_text(self.text, "Document")
        _require_metadata(self.metadata, "Document")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "metadata": self.metadata}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        """从 dict 反序列化；忽略未知字段以保证向后兼容。"""
        return cls(
            id=data["id"],
            text=data["text"],
            metadata=dict(data.get("metadata") or {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> Document:
        return cls.from_dict(json.loads(raw))

    @property
    def image_refs(self) -> list[ImageRef]:
        """读取 ``metadata.images`` 中的图片引用（无图片时为空列表）。"""
        return parse_image_refs(self.metadata, owner="Document.metadata")


@dataclass
class Chunk:
    """Splitter/Chunker 产出的文本片段。

    Attributes:
        id: chunk 唯一标识（DocumentChunker 生成确定性 ID）。
        text: 片段文本；可含 ``[IMAGE: {image_id}]`` 占位符。
        metadata: 继承自 Document.metadata（含 ``source_path``），
            另含 ``chunk_index``；含图片占位符时另含 ``images`` / ``image_refs``。
        start_offset: 片段在 Document.text 中的起始字符位置（可选）。
        end_offset: 片段在 Document.text 中的结束字符位置（可选，不含 end）。
        source_ref: 指向父 Document.id，支持溯源。
    """

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    start_offset: int | None = None
    end_offset: int | None = None
    source_ref: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.id, "Chunk")
        _require_text(self.text, "Chunk")
        _require_metadata(self.metadata, "Chunk")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "metadata": self.metadata,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chunk:
        """从 dict 反序列化；忽略未知字段以保证向后兼容。"""
        return cls(
            id=data["id"],
            text=data["text"],
            metadata=dict(data.get("metadata") or {}),
            start_offset=data.get("start_offset"),
            end_offset=data.get("end_offset"),
            source_ref=data.get("source_ref"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> Chunk:
        return cls.from_dict(json.loads(raw))

    @property
    def image_refs(self) -> list[ImageRef]:
        """读取 ``metadata.images`` 中该 chunk 实际引用的图片子集。"""
        return parse_image_refs(self.metadata, owner="Chunk.metadata")


@dataclass
class ChunkRecord:
    """存储/检索载体（chunk + 向量）。

    Attributes:
        id: 与 Chunk.id 一致的确定性标识（幂等 upsert 的键）。
        text: chunk 原文。
        metadata: 完整 chunk metadata（source_path / page / tags / image_refs 等）。
        dense_vector: 稠密向量（C8 DenseEncoder 填充）。
        sparse_vector: 稀疏向量，``term -> weight`` 映射（C9 SparseEncoder 填充）。
    """

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    dense_vector: list[float] | None = None
    sparse_vector: dict[str, float] | None = None

    def __post_init__(self) -> None:
        _require_id(self.id, "ChunkRecord")
        _require_text(self.text, "ChunkRecord")
        _require_metadata(self.metadata, "ChunkRecord")

    @classmethod
    def from_chunk(cls, chunk: Chunk) -> ChunkRecord:
        """由 ``Chunk`` 构造（metadata 浅拷贝，向量待编码阶段填充）。"""
        return cls(id=chunk.id, text=chunk.text, metadata=dict(chunk.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "metadata": self.metadata,
            "dense_vector": self.dense_vector,
            "sparse_vector": self.sparse_vector,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkRecord:
        """从 dict 反序列化；忽略未知字段以保证向后兼容。"""
        return cls(
            id=data["id"],
            text=data["text"],
            metadata=dict(data.get("metadata") or {}),
            dense_vector=data.get("dense_vector"),
            sparse_vector=data.get("sparse_vector"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> ChunkRecord:
        return cls.from_dict(json.loads(raw))


@dataclass
class ProcessedQuery:
    """``QueryProcessor`` 产出的查询预处理结果（阶段 D1）。

    Attributes:
        query: 原始查询文本（原样保留，供 Dense 路生成 embedding 使用）。
        keywords: 关键词列表（去停用词、保序去重；供 D3 SparseRetriever
            喂给 ``BM25Indexer.query(keywords, top_k)``）。
        filters: 结构化过滤条件（如 ``collection`` / ``doc_type`` /
            ``language`` 等；无约束时为空 dict，值语义由检索器负责）。
    """

    query: str
    keywords: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError(
                f"ProcessedQuery.query must be a non-empty string, got {self.query!r}"
            )
        if not isinstance(self.keywords, list):
            raise TypeError(
                f"ProcessedQuery.keywords must be a list, got {type(self.keywords).__name__}"
            )
        if not isinstance(self.filters, dict):
            raise TypeError(
                f"ProcessedQuery.filters must be a dict, got {type(self.filters).__name__}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "keywords": list(self.keywords),
            "filters": dict(self.filters),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessedQuery:
        """从 dict 反序列化；忽略未知字段以保证向后兼容。"""
        return cls(
            query=data["query"],
            keywords=list(data.get("keywords") or []),
            filters=dict(data.get("filters") or {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> ProcessedQuery:
        return cls.from_dict(json.loads(raw))


@dataclass
class RetrievalResult:
    """单条检索结果（阶段 D2/D3/D5/D6 共用的查询链路契约）。

    Attributes:
        chunk_id: 命中 chunk 的确定性 ID（与 C4 DocumentChunker / C11
            ``SparseDocStats.chunk_id`` / C12 upsert 键共用同一 id 空间）。
        score: 相关性分数（越高越相关；语义由产出阶段决定——Dense 相似度 /
            BM25 分数 / D4 RRF 融合分数 / D6 Rerank 分数）。
        text: 命中 chunk 的原文（All-in-One 存储保证命中即可取回，无需查库）。
        metadata: 命中 chunk 的完整 metadata（source_path / page / title /
            tags / image_refs 等）。
    """

    chunk_id: str
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.chunk_id, str) or not self.chunk_id:
            raise ValueError(f"RetrievalResult.chunk_id must be a non-empty string, got {self.chunk_id!r}")
        if not isinstance(self.score, (int, float)) or isinstance(self.score, bool):
            raise TypeError(
                f"RetrievalResult.score must be a number, got {type(self.score).__name__}"
            )
        if not isinstance(self.text, str):
            raise TypeError(f"RetrievalResult.text must be a string, got {type(self.text).__name__}")
        _require_metadata(self.metadata, "RetrievalResult")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "score": self.score,
            "text": self.text,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievalResult:
        """从 dict 反序列化；忽略未知字段以保证向后兼容。"""
        return cls(
            chunk_id=data["chunk_id"],
            score=float(data["score"]),
            text=data["text"],
            metadata=dict(data.get("metadata") or {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> RetrievalResult:
        return cls.from_dict(json.loads(raw))


# ---------------------------------------------------------------------------
# metadata.images 访问工具
# ---------------------------------------------------------------------------


def parse_image_refs(metadata: dict[str, Any], owner: str = "metadata") -> list[ImageRef]:
    """从 ``metadata["images"]`` 解析图片引用列表。

    Args:
        metadata: Document/Chunk 的 metadata dict。
        owner: 错误信息中标注的字段归属（如 ``Document.metadata``）。

    Returns:
        ``ImageRef`` 列表；``images`` 字段缺失时返回空列表。

    Raises:
        TypeError: ``images`` 不是列表、或其中存在非法条目时（fail-fast，信息可读）。
    """
    images = metadata.get("images")
    if images is None:
        return []
    if not isinstance(images, list):
        raise TypeError(f"{owner}.images must be a list, got {type(images).__name__}")
    refs: list[ImageRef] = []
    for i, item in enumerate(images):
        if not isinstance(item, dict):
            raise TypeError(
                f"{owner}.images[{i}] must be a dict, got {type(item).__name__}"
            )
        if not item.get("id") or not item.get("path"):
            raise ValueError(f"{owner}.images[{i}] must contain non-empty 'id' and 'path'")
        refs.append(ImageRef.from_dict(item))
    return refs
