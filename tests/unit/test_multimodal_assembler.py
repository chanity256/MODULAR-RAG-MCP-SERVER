"""E6: MultimodalAssembler 单元测试（临时图片文件，离线）。"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from core.response import ResponseBuilder
from core.response.multimodal_assembler import MultimodalAssembler
from core.types import RetrievalResult

PNG_MAGIC = base64.b64decode("iVBORw0KGgo=")  # PNG 头，内容无需是真图


def _result(images=None, chunk_id="c1") -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id, score=0.9, text="chunk with image",
        metadata={"source_path": "a.pdf", "page": 1, "images": images or []},
    )


@pytest.fixture()
def png_file(tmp_path: Path) -> Path:
    path = tmp_path / "img1.png"
    path.write_bytes(PNG_MAGIC + b"payload")
    return path


class TestMultimodalAssembler:
    def test_appends_image_content_after_markdown(self, png_file):
        result = _result(images=[{"id": "img1", "path": str(png_file)}])
        tool_result = ResponseBuilder().build([result], query="arch")
        MultimodalAssembler().assemble(tool_result, [result])
        assert tool_result["content"][0]["type"] == "text"  # 文本恒在首位
        image = tool_result["content"][1]
        assert image["type"] == "image"
        assert image["mimeType"] == "image/png"
        assert base64.b64decode(image["data"]) == PNG_MAGIC + b"payload"

    def test_structured_content_images_index(self, png_file):
        result = _result(images=[{"id": "img1", "path": str(png_file)}])
        tool_result = ResponseBuilder().build([result])
        MultimodalAssembler().assemble(tool_result, [result])
        assert tool_result["structuredContent"]["images"] == [
            {"id": "img1", "chunk_id": "c1", "mimeType": "image/png"}
        ]

    def test_missing_image_skipped_not_fatal(self, tmp_path):
        result = _result(images=[{"id": "gone", "path": str(tmp_path / "nope.png")}])
        tool_result = ResponseBuilder().build([result])
        MultimodalAssembler().assemble(tool_result, [result])
        assert len(tool_result["content"]) == 1  # 仅剩文本
        assert tool_result["structuredContent"]["images"] == []

    def test_no_images_leaves_result_untouched(self):
        result = _result()
        tool_result = ResponseBuilder().build([result])
        before = repr(tool_result)
        MultimodalAssembler().assemble(tool_result, [result])
        assert repr(tool_result) != before  # images: [] 索引仍会写入
        assert tool_result["structuredContent"]["images"] == []
        assert len(tool_result["content"]) == 1

    def test_max_images_limit(self, tmp_path):
        files = []
        for i in range(4):
            path = tmp_path / f"img{i}.png"
            path.write_bytes(PNG_MAGIC)
            files.append(path)
        result = _result(images=[{"id": f"i{i}", "path": str(p)} for i, p in enumerate(files)])
        tool_result = ResponseBuilder().build([result])
        MultimodalAssembler(max_images=2).assemble(tool_result, [result])
        assert [c["type"] for c in tool_result["content"]] == ["text", "image", "image"]

    def test_mime_from_extension(self, tmp_path):
        jpg = tmp_path / "photo.jpg"
        jpg.write_bytes(PNG_MAGIC)
        result = _result(images=[{"id": "j", "path": str(jpg)}])
        tool_result = ResponseBuilder().build([result])
        MultimodalAssembler().assemble(tool_result, [result])
        assert tool_result["content"][1]["mimeType"] == "image/jpeg"

    def test_unknown_extension_defaults_png(self, tmp_path):
        raw = tmp_path / "blob.dat"
        raw.write_bytes(PNG_MAGIC)
        result = _result(images=[{"id": "b", "path": str(raw)}])
        tool_result = ResponseBuilder().build([result])
        MultimodalAssembler().assemble(tool_result, [result])
        assert tool_result["content"][1]["mimeType"] == "image/png"

    def test_invalid_tool_result_raises(self):
        with pytest.raises(TypeError):
            MultimodalAssembler().assemble({"no": "content"}, [])

    def test_invalid_results_type_raises(self):
        with pytest.raises(TypeError):
            MultimodalAssembler().assemble({"content": []}, "nope")

    def test_invalid_max_images_raises(self):
        with pytest.raises(ValueError):
            MultimodalAssembler(max_images=0)
