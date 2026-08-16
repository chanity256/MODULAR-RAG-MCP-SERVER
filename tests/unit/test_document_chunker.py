"""``ingestion.chunking.DocumentChunker`` 单元测试（阶段 C4）。

使用 FakeSplitter（按哨兵分隔符切分）隔离测试，不依赖真实 LLM/外部服务；
另含一个基于真实 RecursiveSplitter 的配置驱动用例（chunk_size 改变切分结果）。

覆盖验收标准：ID 唯一/确定性/格式、元数据继承 + chunk_index、图片引用按需
分发（子集/移除）、source_ref 溯源、Chunk 类型契约（可序列化往返）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from core.types import Chunk, Document, ImageRef, image_placeholder
from ingestion.chunking.document_chunker import DocumentChunker
from libs.splitter.base_splitter import BaseSplitter
from libs.splitter.splitter_factory import SplitterFactory, SplitterProviderError

_VALID_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
    vision_llm:
      provider: azure
      model: gpt-4o
    vector_store:
      backend: chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path, splitter_yaml: str = ""):
    path = Path(tmp_path) / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VALID_YAML + splitter_yaml, encoding="utf-8")
    return load_settings(path)


@pytest.fixture
def clean_registry():
    saved = dict(SplitterFactory._registry)
    SplitterFactory._registry.clear()
    yield SplitterFactory
    SplitterFactory._registry.clear()
    SplitterFactory._registry.update(saved)


class _FakeSplitter(BaseSplitter):
    """按哨兵 ``\\x1e`` 切分的确定性 Fake 实现。"""

    def __init__(self, splitter_config, **kwargs):
        self.config = splitter_config
        self.kwargs = kwargs
        self.received: list[str] = []

    def split_text(self, text, *, trace=None, **kwargs):
        self.received.append(text)
        return text.split("\x1e")


@pytest.fixture
def fake_chunker(tmp_path, clean_registry):
    """注册 fake provider=recursive 并返回构造函数（延迟创建 chunker）。"""
    clean_registry.register("recursive")(_FakeSplitter)

    def make():
        return DocumentChunker(_settings(tmp_path))
    return make


def _make_document(text: str, images: list[ImageRef] | None = None) -> Document:
    metadata = {"source_path": "docs/sample.pdf", "doc_type": "pdf", "sha256": "ab" * 32}
    if images is not None:
        metadata["images"] = [r.to_dict() for r in images]
    return Document(id="doc" + "0" * 61, text=text, metadata=metadata)


def _img(image_id: str, page: int = 0) -> ImageRef:
    return ImageRef(
        id=image_id,
        path=f"data/images/col/{image_id}.png",
        text_offset=0,
        text_length=len(image_placeholder(image_id)),
        page=page,
        position={"width": 2, "height": 2},
    )


# ---------------------------------------------------------------------------
# 基础契约
# ---------------------------------------------------------------------------


def test_split_document_returns_chunks_contract(fake_chunker):
    doc = _make_document("第一段\x1e第二段\x1e第三段")
    chunks = fake_chunker().split_document(doc)
    assert len(chunks) == 3
    assert all(isinstance(c, Chunk) for c in chunks)
    assert [c.text for c in chunks] == ["第一段", "第二段", "第三段"]
    assert [c.metadata["chunk_index"] for c in chunks] == [0, 1, 2]
    assert all(c.source_ref == doc.id for c in chunks)


def test_splitter_receives_full_document_text(fake_chunker):
    doc = _make_document("第一段\x1e第二段")
    chunker = fake_chunker()
    chunker.split_document(doc)
    assert chunker.splitter.received == [doc.text]


def test_chunk_id_format(fake_chunker):
    doc = _make_document("内容 A\x1e内容 B")
    chunks = fake_chunker().split_document(doc)
    pattern = re.compile(rf"^{re.escape(doc.id)}_\d{{4}}_[0-9a-f]{{8}}$")
    for c in chunks:
        assert pattern.match(c.id)
    # hash 部分为 chunk 文本 sha256 前 8 位。
    assert chunks[1].id == f"{doc.id}_0001_{hashlib.sha256('内容 B'.encode()).hexdigest()[:8]}"


def test_chunk_ids_unique(fake_chunker):
    doc = _make_document("同文\x1e同文\x1e同文")  # 相同文本也必须 ID 唯一（index 区分）
    chunks = fake_chunker().split_document(doc)
    assert len({c.id for c in chunks}) == 3


def test_chunk_ids_deterministic(fake_chunker):
    doc = _make_document("甲\x1e乙\x1e丙")
    first = fake_chunker().split_document(doc)
    second = fake_chunker().split_document(doc)
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.to_dict() for c in first] == [c.to_dict() for c in second]


def test_metadata_inherits_document_fields(fake_chunker):
    doc = _make_document("片段一\x1e片段二")
    chunks = fake_chunker().split_document(doc)
    for c in chunks:
        assert c.metadata["source_path"] == "docs/sample.pdf"
        assert c.metadata["doc_type"] == "pdf"
        assert c.metadata["sha256"] == "ab" * 32


def test_chunks_serializable_round_trip(fake_chunker):
    doc = _make_document("片段一\x1e片段二")
    chunks = fake_chunker().split_document(doc)
    for c in chunks:
        assert Chunk.from_dict(c.to_dict()) == c


# ---------------------------------------------------------------------------
# 图片引用按需分发
# ---------------------------------------------------------------------------


def test_image_refs_distributed_per_chunk(fake_chunker):
    a, b = _img("h1_0_0"), _img("h1_1_2")
    text = (
        f"首段引用图片 {image_placeholder(a.id)}\x1e"
        f"次段引用另一张 {image_placeholder(b.id)}\x1e"
        "末段没有图片"
    )
    doc = _make_document(text, images=[a, b])
    chunks = fake_chunker().split_document(doc)

    assert chunks[0].metadata["image_refs"] == [a.id]
    assert chunks[0].metadata["images"] == [a.to_dict()]
    assert chunks[1].metadata["image_refs"] == [b.id]
    assert chunks[1].metadata["images"] == [b.to_dict()]
    # 无占位符的 chunk：不含 images / image_refs 字段（文档级 images 不整体继承）。
    assert "images" not in chunks[2].metadata
    assert "image_refs" not in chunks[2].metadata


def test_multiple_placeholders_in_same_chunk(fake_chunker):
    a, b = _img("h1_0_0"), _img("h1_1_2")
    doc = _make_document(
        f"双图段落 {image_placeholder(a.id)} 和 {image_placeholder(b.id)}",
        images=[a, b],
    )
    chunks = fake_chunker().split_document(doc)
    assert chunks[0].metadata["image_refs"] == [a.id, b.id]
    assert chunks[0].metadata["images"] == [a.to_dict(), b.to_dict()]


def test_placeholder_id_missing_from_doc_images(fake_chunker):
    doc = _make_document(f"幽灵引用 {image_placeholder('ghost_0_0')}", images=[_img("h1_0_0")])
    chunks = fake_chunker().split_document(doc)
    # image_refs 与占位符一致；images 仅含文档中实际存在的子集（此处为空）。
    assert chunks[0].metadata["image_refs"] == ["ghost_0_0"]
    assert chunks[0].metadata["images"] == []


def test_document_without_images_no_fields(fake_chunker):
    doc = _make_document(f"只有占位符 {image_placeholder('x_0_0')}")  # 文档级无 images
    chunks = fake_chunker().split_document(doc)
    assert chunks[0].metadata["image_refs"] == ["x_0_0"]
    assert chunks[0].metadata["images"] == []


# ---------------------------------------------------------------------------
# 配置驱动与工厂集成
# ---------------------------------------------------------------------------


def test_chunker_uses_factory_created_splitter_with_config(fake_chunker):
    chunker = fake_chunker()
    assert isinstance(chunker.splitter, _FakeSplitter)
    assert chunker.splitter.config.provider == "recursive"  # 默认 splitter 节


def test_unknown_provider_raises_at_construction(tmp_path, clean_registry):
    settings = _settings(tmp_path, splitter_yaml="\nsplitter:\n  provider: nonexistent\n")
    with pytest.raises(SplitterProviderError):
        DocumentChunker(settings)


def test_config_driven_chunk_size_changes_chunk_count(tmp_path):
    """真实 RecursiveSplitter：chunk_size 越小产出 chunk 越多。"""
    body = "\n\n".join(
        f"## Section {i}\n" + ("关于模块化检索架构的说明文字。" * 12) for i in range(8)
    )
    doc = _make_document(body)
    small = DocumentChunker(_settings(tmp_path / "a", "\nsplitter:\n  chunk_size: 120\n  chunk_overlap: 0\n"))
    large = DocumentChunker(_settings(tmp_path / "b", "\nsplitter:\n  chunk_size: 1200\n  chunk_overlap: 0\n"))
    n_small = len(small.split_document(doc))
    n_large = len(large.split_document(doc))
    assert n_small > n_large >= 1
