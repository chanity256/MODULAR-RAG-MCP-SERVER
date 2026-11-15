"""极简 PDF 构造器（公共 fixture helper，G1 收口抽出）。

原实现内联在 ``tests/e2e/test_data_ingestion.py``（与
``tests/unit/test_loader_pdf_contract.py`` 同法：正确计算 xref 偏移的
最小合法 PDF，pypdf 可解析；仓库历史无预置 PDF fixture）。

现在同时供两处使用：
- 测试在 ``tmp_path`` 内即时生成语料；
- ``scripts/generate_sample_pdfs.py`` 落盘 ``tests/fixtures/sample_documents/``
  的常驻样例，供 I5 全链路验收（ingest → query → evaluate → Dashboard）使用。
"""

from __future__ import annotations

from pathlib import Path

_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
_IMAGE_PIXELS = b"\xff\x00\x00\x00\xff\x00\x00\x00\xff\xff\xff\xff"


def build_pdf(objects: list[bytes]) -> bytes:
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


def write_simple_pdf(path: Path) -> Path:
    """单页纯文本 PDF（对齐原 e2e 的 simple.pdf 场景）。"""
    path.write_bytes(
        build_pdf(
            [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                _page(2, 4, b"<< /Font << /F1 5 0 R >> >>"),
                _content_stream("Hello ingestion e2e simple document"),
                _FONT,
            ]
        )
    )
    return path


def write_pdf_with_images(path: Path) -> Path:
    """两页 PDF：第一页文本 + 第二页嵌入 2x2 RGB 图片（对齐原 e2e 场景）。"""
    path.write_bytes(
        build_pdf(
            [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
                _page(2, 4, b"<< /Font << /F1 5 0 R >> /XObject << /Im0 7 0 R >> >>"),
                _content_stream("First page about system architecture overview"),
                _FONT,
                _page(2, 8, b"<< /Font << /F1 5 0 R >> >>"),
                _image_xobject(),
                _content_stream("Second page conclusion with embedded figure"),
            ]
        )
    )
    return path
