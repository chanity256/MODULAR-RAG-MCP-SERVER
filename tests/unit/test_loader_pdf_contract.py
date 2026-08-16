"""``libs.loader`` PDF Loader 契约测试（阶段 C3）。

fixtures 由测试内置的极简 PDF 字节流生成器构造（正确计算 xref 偏移）：
- ``simple.pdf``：单页纯文本 PDF。
- ``with_images.pdf``：两页 PDF，第 1 页嵌入一张 2x2 RGB 未压缩图片。

覆盖验收标准：
- 产出 ``Document``，metadata 至少含 ``source_path``（另含 doc_type/sha256）。
- 图片提取到 ``{images_root}/{doc_hash}/``，文本插入 ``[IMAGE: {id}]`` 占位符，
  ``metadata.images`` 符合 C1 六字段契约（text_offset/text_length 精确）。
- 无图片时省略 ``images`` 字段；图片提取失败降级不阻塞文本解析。
"""

from __future__ import annotations

import logging
import struct
import sys
import types
from pathlib import Path

import pytest

from core.types import Document, iter_image_placeholders
from libs.loader.base_loader import BaseLoader
from libs.loader.file_integrity import SQLiteIntegrityChecker
from libs.loader.pdf_loader import PdfLoader, PdfLoaderError

# ---------------------------------------------------------------------------
# 极简 PDF 构造器（正确计算 xref 偏移）
# ---------------------------------------------------------------------------

_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
# 2x2 DeviceRGB 8bpc 未压缩像素（红/绿/蓝/白）。
_IMAGE_PIXELS = b"\xff\x00\x00\x00\xff\x00\x00\x00\xff\xff\xff\xff"


def _build_pdf(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode("latin-1")
    return bytes(out)


def _content_stream(text: str) -> bytes:
    payload = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    return b"<< /Length %d >>\nstream\n" % len(payload) + payload + b"\nendstream"


def _page(parent: int, contents: int, resources: bytes) -> bytes:
    return (
        f"<< /Type /Page /Parent {parent} 0 R /MediaBox [0 0 612 792] "
        f"/Contents {contents} 0 R /Resources {resources.decode('latin-1')} >>"
    ).encode("latin-1")


def _image_xobject() -> bytes:
    return (
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 "
        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 12 >>\n"
        b"stream\n" + _IMAGE_PIXELS + b"\nendstream"
    )


def _write_simple_pdf(path: Path) -> Path:
    pdf = _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        _page(2, 4, b"<< /Font << /F1 5 0 R >> >>"),
        _content_stream("Hello PDF Loader contract"),
        _FONT,
    ])
    path.write_bytes(pdf)
    return path


def _write_pdf_with_images(path: Path) -> Path:
    pdf = _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
        _page(2, 4, b"<< /Font << /F1 5 0 R >> /XObject << /Im0 7 0 R >> >>"),
        _content_stream("First page about system architecture"),
        _FONT,
        _page(2, 8, b"<< /Font << /F1 5 0 R >> >>"),
        _image_xobject(),
        _content_stream("Second page conclusion"),
    ])
    path.write_bytes(pdf)
    return path


@pytest.fixture
def images_root(tmp_path) -> Path:
    return tmp_path / "images"


@pytest.fixture
def simple_pdf(tmp_path) -> Path:
    return _write_simple_pdf(tmp_path / "simple.pdf")


@pytest.fixture
def images_pdf(tmp_path) -> Path:
    return _write_pdf_with_images(tmp_path / "with_images.pdf")


@pytest.fixture
def loader(images_root) -> PdfLoader:
    return PdfLoader(str(images_root), engine="pypdf")


# ---------------------------------------------------------------------------
# 基础契约：simple.pdf
# ---------------------------------------------------------------------------


def test_base_loader_is_abstract():
    with pytest.raises(TypeError):
        BaseLoader()  # type: ignore[abstract]


def test_load_simple_pdf_returns_document_contract(loader, simple_pdf):
    doc = loader.load(str(simple_pdf))
    assert isinstance(doc, Document)
    assert "Hello PDF Loader contract" in doc.text
    assert doc.metadata["source_path"] == str(simple_pdf)
    assert doc.metadata["doc_type"] == "pdf"
    assert doc.metadata["page_count"] == 1
    assert loader.last_engine == "pypdf"


def test_document_id_is_sha256_consistent_with_integrity_checker(loader, simple_pdf):
    doc = loader.load(str(simple_pdf))
    checker = SQLiteIntegrityChecker(":memory:")
    try:
        assert doc.id == checker.compute_sha256(str(simple_pdf))
        assert doc.metadata["sha256"] == doc.id
    finally:
        checker.close()


def test_simple_pdf_has_no_images_field(loader, simple_pdf):
    doc = loader.load(str(simple_pdf))
    assert doc.image_refs == []
    assert "images" not in doc.metadata


def test_missing_file_raises_readable_error(loader, tmp_path):
    with pytest.raises(FileNotFoundError) as exc_info:
        loader.load(str(tmp_path / "nope.pdf"))
    assert "nope.pdf" in str(exc_info.value)


def test_load_is_deterministic(loader, images_pdf):
    doc1 = loader.load(str(images_pdf))
    doc2 = loader.load(str(images_pdf))
    assert doc1.id == doc2.id
    assert doc1.text == doc2.text
    assert [r.to_dict() for r in doc1.image_refs] == [r.to_dict() for r in doc2.image_refs]


# ---------------------------------------------------------------------------
# 图片契约：with_images.pdf
# ---------------------------------------------------------------------------


def test_load_pdf_with_images_inserts_placeholder(loader, images_pdf, images_root):
    doc = loader.load(str(images_pdf))
    refs = doc.image_refs
    assert len(refs) == 1
    ref = refs[0]
    assert ref.id == f"{doc.id}_0_0"  # {doc_hash}_{page}_{seq}
    # 占位符位于第 1 页文本之后、第 2 页文本之前。
    assert doc.text.startswith("First page about system architecture")
    assert "Second page conclusion" in doc.text
    placeholder = f"[IMAGE: {ref.id}]"
    assert placeholder in doc.text
    # text_offset/text_length 精确指向占位符。
    assert doc.text[ref.text_offset:ref.text_offset + ref.text_length] == placeholder
    assert iter_image_placeholders(doc.text) == [(ref.id, ref.text_offset)]


def test_image_file_saved_under_doc_hash_dir(loader, images_pdf, images_root):
    doc = loader.load(str(images_pdf))
    ref = doc.image_refs[0]
    saved = Path(ref.path)
    assert saved == images_root / doc.id / f"{ref.id}.png"
    assert saved.is_file()
    payload = saved.read_bytes()
    # PNG 魔数 + IHDR 尺寸 2x2。
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", payload[16:24])
    assert (width, height) == (2, 2)


def test_metadata_images_follow_c1_contract(loader, images_pdf):
    doc = loader.load(str(images_pdf))
    images = doc.metadata["images"]
    assert isinstance(images, list) and len(images) == 1
    entry = images[0]
    assert set(entry.keys()) == {"id", "path", "page", "text_offset", "text_length", "position"}
    assert entry["page"] == 0
    assert entry["position"] == {"width": 2, "height": 2}
    assert entry["text_length"] == len(f"[IMAGE: {entry['id']}]")


# ---------------------------------------------------------------------------
# 降级行为
# ---------------------------------------------------------------------------


def test_image_failure_degrades_to_text_only(loader, images_pdf, caplog):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(PdfLoader, "_store_image", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        with caplog.at_level(logging.WARNING, logger="modular-rag.loader.pdf"):
            doc = loader.load(str(images_pdf))
    assert "First page about system architecture" in doc.text
    assert doc.image_refs == []
    assert "images" not in doc.metadata
    assert any("boom" in rec.getMessage() for rec in caplog.records)


def test_unsupported_colorspace_skipped_but_text_kept(loader, images_pdf, caplog):
    """单图编码失败：跳过该图但其余解析（文本/占位符）正常。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            PdfLoader,
            "_encode_image",
            lambda self, obj, data: (_ for _ in ()).throw(ValueError("unsupported")),
        )
        with caplog.at_level(logging.WARNING, logger="modular-rag.loader.pdf"):
            doc = loader.load(str(images_pdf))
    assert "First page about system architecture" in doc.text
    assert "[IMAGE:" not in doc.text
    assert doc.image_refs == []


# ---------------------------------------------------------------------------
# 引擎选择
# ---------------------------------------------------------------------------


def test_engine_auto_uses_markitdown_when_available(images_pdf, images_root, monkeypatch):
    fake = types.ModuleType("markitdown")

    class _FakeMarkItDown:
        def convert(self, path):
            return types.SimpleNamespace(text_content="# Fake Title\n\nFake markitdown body.")

    fake.MarkItDown = _FakeMarkItDown
    monkeypatch.setitem(sys.modules, "markitdown", fake)
    loader = PdfLoader(str(images_root), engine="auto")
    doc = loader.load(str(images_pdf))
    assert loader.last_engine == "markitdown"
    assert doc.text.startswith("# Fake Title")
    # 整块文本引擎：占位符统一追加文末。
    refs = doc.image_refs
    assert len(refs) == 1
    assert doc.text.endswith(f"[IMAGE: {refs[0].id}]")


def test_engine_auto_falls_back_to_pypdf_without_markitdown(images_pdf, images_root, monkeypatch):
    monkeypatch.delitem(sys.modules, "markitdown", raising=False)
    loader = PdfLoader(str(images_root), engine="auto")
    doc = loader.load(str(images_pdf))
    assert loader.last_engine == "pypdf"
    assert "First page about system architecture" in doc.text


def test_engine_markitdown_required_but_missing_raises(images_pdf, images_root, monkeypatch):
    monkeypatch.delitem(sys.modules, "markitdown", raising=False)
    loader = PdfLoader(str(images_root), engine="markitdown")
    with pytest.raises(PdfLoaderError) as exc_info:
        loader.load(str(images_pdf))
    assert "markitdown" in str(exc_info.value)


def test_engine_unknown_raises(images_root):
    with pytest.raises(PdfLoaderError) as exc_info:
        PdfLoader(str(images_root), engine="docx")
    assert "docx" in str(exc_info.value)
