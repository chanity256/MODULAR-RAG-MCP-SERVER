"""``core.types`` 核心数据契约测试（阶段 C1）。

覆盖：
- ``Document`` / ``Chunk`` / ``ChunkRecord`` 的 ``to_dict`` / ``from_dict`` /
  ``to_json`` 序列化与字段稳定性（键集合固定，未知字段忽略）。
- ``metadata`` 约定最少包含 ``source_path``，且增量扩展字段不破坏兼容。
- ``metadata.images`` 图片引用规范（``ImageRef`` 六字段结构）。
- 文本图片占位符规范（``[IMAGE: {image_id}]``）与 ID 生成格式。
"""

from __future__ import annotations

import json

import pytest

from core.types import (
    Chunk,
    ChunkRecord,
    Document,
    ImageRef,
    IMAGE_PLACEHOLDER_TEMPLATE,
    image_placeholder,
    iter_image_placeholders,
    make_image_id,
    parse_image_refs,
)


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


def test_document_to_dict_field_stability():
    doc = Document(id="doc_abc", text="hello", metadata={"source_path": "a.pdf"})
    d = doc.to_dict()
    assert set(d.keys()) == {"id", "text", "metadata"}
    assert d["id"] == "doc_abc"
    assert d["text"] == "hello"
    assert d["metadata"]["source_path"] == "a.pdf"


def test_document_json_round_trip():
    doc = Document(id="doc_abc", text="你好 [IMAGE: h1_0_1]", metadata={"source_path": "a.pdf"})
    restored = Document.from_json(doc.to_json())
    assert restored == doc


def test_document_from_dict_ignores_unknown_fields():
    d = {"id": "doc_abc", "text": "t", "metadata": {"source_path": "a.pdf"}, "future_field": 1}
    doc = Document.from_dict(d)
    assert doc.id == "doc_abc"
    assert doc.text == "t"
    assert doc.metadata == {"source_path": "a.pdf"}


def test_document_metadata_extra_fields_preserved():
    """metadata 增量扩展（新增字段）不破坏序列化与往返一致性。"""
    metadata = {
        "source_path": "a.pdf",
        "title": "Sample",
        "tags": ["rag", "mcp"],
        "custom": {"nested": True},
    }
    doc = Document(id="doc_abc", text="t", metadata=metadata)
    restored = Document.from_dict(json.loads(doc.to_json()))
    assert restored.metadata == metadata


def test_document_requires_non_empty_id():
    with pytest.raises(ValueError) as exc_info:
        Document(id="", text="t", metadata={"source_path": "a.pdf"})
    assert "Document.id" in str(exc_info.value)


def test_document_rejects_non_dict_metadata():
    with pytest.raises(TypeError) as exc_info:
        Document(id="doc_abc", text="t", metadata=["not", "a", "dict"])
    assert "Document.metadata" in str(exc_info.value)


def test_document_rejects_non_string_text():
    with pytest.raises(TypeError) as exc_info:
        Document(id="doc_abc", text=123, metadata={})
    assert "Document.text" in str(exc_info.value)


def test_document_with_images_metadata_is_json_serializable():
    """metadata.images 为纯 dict 结构时整体可直接 json.dumps。"""
    ref = ImageRef(
        id=make_image_id("h1", 0, 1),
        path="data/images/col/h1_0_1.png",
        text_offset=5,
        text_length=len(image_placeholder("h1_0_1")),
        page=0,
        position={"x": 10, "y": 20, "width": 100, "height": 50},
    )
    text = "开头 " + image_placeholder(ref.id) + " 结尾"
    doc = Document(id="doc_h1", text=text, metadata={"source_path": "a.pdf", "images": [ref.to_dict()]})
    payload = json.loads(doc.to_json())
    assert payload["metadata"]["images"] == [ref.to_dict()]
    assert Document.from_dict(payload).image_refs == [ref]


def test_document_source_path_convention_survives_round_trip():
    doc = Document(id="doc_h1", text="t", metadata={"source_path": "docs/a.pdf", "page": 3})
    restored = Document.from_dict(json.loads(doc.to_json()))
    assert restored.metadata["source_path"] == "docs/a.pdf"


# ---------------------------------------------------------------------------
# Chunk
# ---------------------------------------------------------------------------


def test_chunk_to_dict_field_stability():
    chunk = Chunk(id="c1", text="片段", metadata={"source_path": "a.pdf"})
    d = chunk.to_dict()
    assert set(d.keys()) == {"id", "text", "metadata", "start_offset", "end_offset", "source_ref"}
    # 可选字段默认 None（序列化键仍稳定存在）。
    assert d["start_offset"] is None
    assert d["end_offset"] is None
    assert d["source_ref"] is None


def test_chunk_round_trip_with_offsets_and_source_ref():
    chunk = Chunk(
        id="c1",
        text="片段 [IMAGE: h1_0_1]",
        metadata={"source_path": "a.pdf", "chunk_index": 0, "image_refs": ["h1_0_1"]},
        start_offset=0,
        end_offset=42,
        source_ref="doc_h1",
    )
    restored = Chunk.from_dict(json.loads(chunk.to_json()))
    assert restored == chunk


def test_chunk_requires_non_empty_id():
    with pytest.raises(ValueError):
        Chunk(id="", text="t")


# ---------------------------------------------------------------------------
# ChunkRecord
# ---------------------------------------------------------------------------


def test_chunk_record_defaults_and_shape():
    rec = ChunkRecord(id="c1", text="片段", metadata={"source_path": "a.pdf"})
    assert rec.dense_vector is None
    assert rec.sparse_vector is None
    d = rec.to_dict()
    assert set(d.keys()) == {"id", "text", "metadata", "dense_vector", "sparse_vector"}


def test_chunk_record_with_vectors_round_trip():
    rec = ChunkRecord(
        id="c1",
        text="片段",
        metadata={"source_path": "a.pdf"},
        dense_vector=[0.1, 0.2, 0.3],
        sparse_vector={"检索": 1.5, "bm25": 0.8},
    )
    restored = ChunkRecord.from_dict(json.loads(rec.to_json()))
    assert restored == rec
    assert restored.dense_vector == [0.1, 0.2, 0.3]
    assert restored.sparse_vector == {"检索": 1.5, "bm25": 0.8}


def test_chunk_record_from_chunk_copies_and_defaults_vectors():
    chunk = Chunk(id="c1", text="片段", metadata={"source_path": "a.pdf", "chunk_index": 2})
    rec = ChunkRecord.from_chunk(chunk)
    assert rec.id == chunk.id
    assert rec.text == chunk.text
    assert rec.metadata == chunk.metadata
    assert rec.metadata is not chunk.metadata  # 浅拷贝，互不影响
    assert rec.dense_vector is None
    assert rec.sparse_vector is None


# ---------------------------------------------------------------------------
# ImageRef（metadata.images 规范）
# ---------------------------------------------------------------------------


def test_image_ref_to_dict_six_fields():
    ref = ImageRef(id="h1_0_1", path="data/images/col/h1_0_1.png", text_offset=5, text_length=16, page=0,
                   position={"x": 1})
    assert set(ref.to_dict().keys()) == {"id", "path", "page", "text_offset", "text_length", "position"}


def test_image_ref_round_trip():
    ref = ImageRef(id="h1_2_3", path="data/images/col/h1_2_3.png", text_offset=100, text_length=16, page=2,
                   position={"page_width": 595})
    assert ImageRef.from_dict(ref.to_dict()) == ref


def test_image_ref_from_dict_uses_defaults_for_optional_fields():
    ref = ImageRef.from_dict({"id": "h1_0_1", "path": "p.png"})
    assert ref.page is None
    assert ref.text_offset == 0
    assert ref.text_length == 0
    assert ref.position == {}


def test_image_ref_requires_id_and_path():
    with pytest.raises(ValueError):
        ImageRef(id="", path="p.png")
    with pytest.raises(ValueError):
        ImageRef(id="h1_0_1", path="")


# ---------------------------------------------------------------------------
# 占位符与图片 ID 规范
# ---------------------------------------------------------------------------


def test_image_placeholder_format():
    assert image_placeholder("h1_0_1") == "[IMAGE: h1_0_1]"
    assert IMAGE_PLACEHOLDER_TEMPLATE == "[IMAGE: {image_id}]"


def test_make_image_id_format():
    assert make_image_id("abcd1234", 0, 1) == "abcd1234_0_1"


def test_iter_image_placeholders_finds_all_in_order():
    text = "开头 [IMAGE: h1_0_1] 中段 [IMAGE: h1_0_2] 结尾"
    found = iter_image_placeholders(text)
    assert found == [("h1_0_1", 3), ("h1_0_2", 22)]


def test_iter_image_placeholders_no_match_returns_empty():
    assert iter_image_placeholders("没有图片的普通文本") == []


# ---------------------------------------------------------------------------
# parse_image_refs（metadata.images 访问）
# ---------------------------------------------------------------------------


def test_parse_image_refs_from_metadata():
    refs = [
        ImageRef(id="h1_0_1", path="data/images/col/h1_0_1.png", text_offset=3, text_length=16, page=0),
        ImageRef(id="h1_0_2", path="data/images/col/h1_0_2.png", text_offset=21, text_length=16, page=0),
    ]
    metadata = {"source_path": "a.pdf", "images": [r.to_dict() for r in refs]}
    assert parse_image_refs(metadata, owner="Document.metadata") == refs
    assert Document(id="d", text="t", metadata=metadata).image_refs == refs


def test_parse_image_refs_missing_images_returns_empty():
    assert parse_image_refs({"source_path": "a.pdf"}) == []
    doc = Document(id="d", text="t", metadata={"source_path": "a.pdf"})
    assert doc.image_refs == []


def test_parse_image_refs_malformed_raises_readable_errors():
    with pytest.raises(TypeError) as exc_info:
        parse_image_refs({"images": {"id": "x"}})
    assert "images must be a list" in str(exc_info.value)
    with pytest.raises(TypeError) as exc_info:
        parse_image_refs({"images": ["not-a-dict"]})
    assert "images[0] must be a dict" in str(exc_info.value)
    with pytest.raises(ValueError) as exc_info:
        parse_image_refs({"images": [{"id": "x"}]})
    assert "non-empty 'id' and 'path'" in str(exc_info.value)
