"""``ingestion.transform.ImageCaptioner`` 降级契约测试（阶段 C7）。

覆盖：
- 启用模式：mock Vision LLM 断言调用与 ``image_captions`` 写回。
- 降级模式：禁用/工厂失败/图片缺失/LLM 异常/空响应 → 保留 image_refs、
  标记 ``has_unprocessed_images``、不阻塞。
- 无图 chunk 原样透传；契约字段不变；trace 打点。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.transform.image_captioner import ImageCaptioner
from libs.llm import BaseVisionLLM, ChatResponse

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


def _settings(tmp_path: Path, ingestion_yaml: str = ""):
    path = Path(tmp_path) / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VALID_YAML + ingestion_yaml, encoding="utf-8")
    return load_settings(path)


def _enabled_settings(tmp_path: Path):
    return _settings(tmp_path, "\ningestion:\n  image_captioner:\n    enabled: true\n")


def _image_chunk(tmp_path: Path, index: int = 0, *, with_images: bool = True) -> Chunk:
    """构造带一张真实图片文件的 chunk（metadata.images/image_refs 齐全）。"""
    img_path = tmp_path / f"img_{index}.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
    images = [{"id": f"img_{index}", "path": str(img_path), "page": 1}] if with_images else []
    return Chunk(
        id=f"doc_{index:04d}_ab12cd34",
        text=f"第{index}段正文，图片见下。\n\n[IMAGE: img_{index}]\n\n后续内容。",
        metadata={
            "source_path": "docs/arch.pdf",
            "doc_type": "pdf",
            "chunk_index": index,
            "images": images,
            "image_refs": [f"img_{index}"],
        },
        start_offset=index * 10,
        end_offset=index * 10 + 100,
        source_ref="doc" + "0" * 61,
    )


def _plain_chunk(index: int = 1) -> Chunk:
    return Chunk(
        id=f"doc_{index:04d}_ab12cd34",
        text="纯文本 chunk 没有图片。",
        metadata={"source_path": "docs/arch.pdf", "chunk_index": index},
        start_offset=0,
        end_offset=20,
        source_ref="doc" + "0" * 61,
    )


class _MockVisionLLM(BaseVisionLLM):
    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def chat(self, messages, *, trace=None, **kwargs):
        raise AssertionError("ImageCaptioner should call chat_with_image")

    def chat_with_image(self, text, image, *, trace=None, **kwargs):
        self.calls.append((text, str(image)))
        if self.error is not None:
            raise self.error
        content = self.responses.pop(0) if self.responses else ""
        return ChatResponse(content=content, model="mock-vision")


# ---------------------------------------------------------------------------
# 启用模式
# ---------------------------------------------------------------------------


def test_enabled_writes_captions_into_metadata(tmp_path):
    llm = _MockVisionLLM(responses=["图 1 为系统三层架构流程图。"])
    out = ImageCaptioner(_enabled_settings(tmp_path), vision_llm=llm).transform(
        [_image_chunk(tmp_path)]
    )[0]
    assert out.metadata["image_captions"] == {"img_0": "图 1 为系统三层架构流程图。"}
    assert "has_unprocessed_images" not in out.metadata  # 全部成功不打标记
    assert out.metadata["image_refs"] == ["img_0"]  # 引用保留


def test_enabled_calls_vision_llm_with_prompt_and_path(tmp_path):
    llm = _MockVisionLLM(responses=["描述"])
    ImageCaptioner(_enabled_settings(tmp_path), vision_llm=llm).transform([_image_chunk(tmp_path)])
    assert len(llm.calls) == 1
    prompt, image = llm.calls[0]
    assert "图片" in prompt or "描述" in prompt  # 使用了 prompt 文件内容
    assert "第0段正文" in prompt  # {image_context} 被替换为 chunk 上下文
    assert image.endswith("img_0.png")


def test_prompt_from_file_is_injectable(tmp_path):
    llm = _MockVisionLLM(responses=["c"])
    ImageCaptioner(
        _enabled_settings(tmp_path), vision_llm=llm, prompt="定制PROMPT {image_context}"
    ).transform([_image_chunk(tmp_path)])
    assert llm.calls[0][0].startswith("定制PROMPT")


def test_multiple_images_partial_failure_marks_degraded(tmp_path):
    img1, img2 = tmp_path / "a.png", tmp_path / "b.png"
    img1.write_bytes(b"png1")
    img2.write_bytes(b"png2")
    chunk = Chunk(
        id="doc_0000_ab12cd34",
        text="[IMAGE: a] 和 [IMAGE: b]",
        metadata={
            "source_path": "d.pdf",
            "images": [{"id": "a", "path": str(img1)}, {"id": "b", "path": str(img2)}],
            "image_refs": ["a", "b"],
        },
        start_offset=0,
        end_offset=10,
        source_ref="doc" + "0" * 61,
    )
    llm = _MockVisionLLM(responses=["甲图描述"])  # 第二张响应列表耗尽 → 空响应
    out = ImageCaptioner(_enabled_settings(tmp_path), vision_llm=llm).transform([chunk])[0]
    assert out.metadata["image_captions"] == {"a": "甲图描述"}
    assert out.metadata["has_unprocessed_images"] is True


def test_missing_image_file_marks_degraded(tmp_path):
    chunk = Chunk(
        id="doc_0000_ab12cd34",
        text="[IMAGE: ghost]",
        metadata={
            "source_path": "d.pdf",
            "images": [{"id": "ghost", "path": str(tmp_path / "nonexistent.png")}],
            "image_refs": ["ghost"],
        },
        start_offset=0,
        end_offset=10,
        source_ref="doc" + "0" * 61,
    )
    llm = _MockVisionLLM(responses=["x"])
    out = ImageCaptioner(_enabled_settings(tmp_path), vision_llm=llm).transform([chunk])[0]
    assert "image_captions" not in out.metadata
    assert out.metadata["has_unprocessed_images"] is True
    assert out.metadata["image_refs"] == ["ghost"]


def test_image_ref_without_path_record_marks_degraded(tmp_path):
    chunk = Chunk(
        id="doc_0000_ab12cd34",
        text="[IMAGE: orphan]",
        metadata={"source_path": "d.pdf", "images": [], "image_refs": ["orphan"]},
        start_offset=0,
        end_offset=10,
        source_ref="doc" + "0" * 61,
    )
    llm = _MockVisionLLM(responses=["x"])
    out = ImageCaptioner(_enabled_settings(tmp_path), vision_llm=llm).transform([chunk])[0]
    assert out.metadata["has_unprocessed_images"] is True


def test_llm_exception_marks_degraded_not_fatal(tmp_path):
    llm = _MockVisionLLM(error=RuntimeError("vision 503"))
    out = ImageCaptioner(_enabled_settings(tmp_path), vision_llm=llm).transform(
        [_image_chunk(tmp_path)]
    )[0]
    assert "image_captions" not in out.metadata
    assert out.metadata["has_unprocessed_images"] is True
    assert out.metadata["image_refs"] == ["img_0"]


# ---------------------------------------------------------------------------
# 降级模式（默认禁用 / 工厂失败）
# ---------------------------------------------------------------------------


def test_disabled_keeps_refs_and_marks_unprocessed(tmp_path):
    llm = _MockVisionLLM(responses=["不该被调用"])
    out = ImageCaptioner(_settings(tmp_path), vision_llm=llm).transform(
        [_image_chunk(tmp_path)]
    )[0]
    assert llm.calls == []
    assert out.metadata["has_unprocessed_images"] is True
    assert out.metadata["image_refs"] == ["img_0"]
    assert "image_captions" not in out.metadata


def test_factory_failure_degrades_with_reason(tmp_path, monkeypatch):
    from ingestion.transform import image_captioner as ic_module

    def boom(settings, **kwargs):
        raise RuntimeError("no vision provider")

    monkeypatch.setattr(ic_module.LLMFactory, "create_vision_llm", staticmethod(boom))
    out = ImageCaptioner(_enabled_settings(tmp_path)).transform([_image_chunk(tmp_path)])[0]
    assert out.metadata["has_unprocessed_images"] is True
    assert "vision_llm_init_failed" in out.metadata["image_caption_fallback"]


def test_missing_prompt_file_uses_builtin_default(tmp_path):
    # settings 指向不存在的 prompt 路径，应回退内置模板而不抛错。
    yaml = "\ningestion:\n  image_captioner:\n    enabled: true\n    prompt_path: /nonexistent/prompt.txt\n"
    llm = _MockVisionLLM(responses=["ok"])
    out = ImageCaptioner(_settings(tmp_path, yaml), vision_llm=llm).transform(
        [_image_chunk(tmp_path)]
    )[0]
    assert out.metadata["image_captions"] == {"img_0": "ok"}


# ---------------------------------------------------------------------------
# 契约与 trace
# ---------------------------------------------------------------------------


def test_plain_chunk_passthrough_untouched(tmp_path):
    chunk = _plain_chunk()
    llm = _MockVisionLLM()
    out = ImageCaptioner(_enabled_settings(tmp_path), vision_llm=llm).transform([chunk])[0]
    assert out is chunk
    assert "has_unprocessed_images" not in out.metadata
    assert llm.calls == []


def test_chunk_contract_preserved(tmp_path):
    chunk = _image_chunk(tmp_path, index=3)
    out = ImageCaptioner(_settings(tmp_path)).transform([chunk])[0]
    assert out.id == chunk.id
    assert out.text == chunk.text
    assert out.source_ref == chunk.source_ref
    assert out.start_offset == chunk.start_offset
    assert out.metadata["images"] == chunk.metadata["images"]
    assert "has_unprocessed_images" not in chunk.metadata  # 原 chunk 不被修改


def test_config_switch_parses(tmp_path):
    assert _settings(tmp_path).ingestion.image_captioner.enabled is False
    assert _enabled_settings(tmp_path).ingestion.image_captioner.enabled is True


def test_transform_records_trace_stage(tmp_path):
    trace = TraceContext()
    ImageCaptioner(_settings(tmp_path)).transform(
        [_image_chunk(tmp_path), _plain_chunk()], trace=trace
    )
    record = trace.get_stage("transform")
    assert record["method"] == "image_captioner"
    assert record["details"]["images_degraded"] == 1
    assert record["details"]["chunks_in"] == 2


def test_transform_empty_input_returns_empty(tmp_path):
    assert ImageCaptioner(_settings(tmp_path)).transform([]) == []
