"""ImageCaptioner：图片描述生成（阶段 C7）。

对含 ``metadata["image_refs"]`` 的 chunk 逐图生成 caption：

- **启用模式**（``ingestion.image_captioner.enabled=true``）：经 Vision LLM
  （``BaseVisionLLM.chat_with_image``）生成中文描述，写入
  ``metadata["image_captions"][image_id]``。prompt 取
  ``config/prompts/image_captioning.txt``（``{image_context}`` 为图片所在
  chunk 的上下文文本，截断防超长）。
- **降级模式**（默认禁用 / Vision 工厂创建失败 / 单图读取或调用异常）：
  保留 ``image_refs`` 与 ``images`` 原样，标记
  ``metadata["has_unprocessed_images"]=True``，不阻塞 ingestion。

标记约定：全部图片成功 → 移除 ``has_unprocessed_images`` 且不设置；
任一图片失败 → 置 True。无图片的 chunk 原样透传。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.transform.base_transform import BaseTransform
from libs.llm import BaseVisionLLM, LLMFactory
from observability.logger import get_logger

__all__ = ["ImageCaptioner"]

#: 内置 prompt 兜底（prompt 文件缺失/为空时使用，保证不阻塞）。
_DEFAULT_PROMPT = """你是一位专业的图像理解助手。请根据给定的图片及其在文档中的上下文，生成一段准确、简洁且可被检索的中文描述。

上下文：
{image_context}

请描述图片中的关键信息，包括图片类型、关键对象、以及与上下文相关的结论。"""

#: 传给 Vision LLM 的上下文文本上限（字符）。
_CONTEXT_MAX_CHARS = 800


class ImageCaptioner(BaseTransform):
    """Vision LLM 图片 caption 生成器（禁用/失败降级，不阻塞 ingestion）。"""

    name = "image_captioner"

    def __init__(
        self,
        settings: Settings,
        vision_llm: BaseVisionLLM | None = None,
        *,
        prompt: str | None = None,
    ) -> None:
        self._enabled = settings.ingestion.image_captioner.enabled
        self._logger = get_logger("modular-rag.ingestion.image_captioner")
        self._vision_llm: BaseVisionLLM | None = vision_llm
        self._llm_unavailable_reason: str | None = None
        self._prompt = prompt if prompt is not None else self._load_prompt(
            settings.ingestion.image_captioner.prompt_path
        )
        if self._enabled and vision_llm is None:
            try:
                self._vision_llm = LLMFactory.create_vision_llm(settings)
            except Exception as exc:
                self._logger.warning("Vision LLM creation failed, degrade to markers: %s", exc)
                self._vision_llm = None
                self._llm_unavailable_reason = f"vision_llm_init_failed: {exc}"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _load_prompt(self, prompt_path: str) -> str:
        """读取 prompt 模板；失败时用内置兜底（保证可用性优先于可配置性）。"""
        try:
            content = Path(prompt_path).read_text(encoding="utf-8")
        except OSError as exc:
            self._logger.warning("prompt file unreadable (%s), use builtin default", exc)
            return _DEFAULT_PROMPT
        content = content.strip()
        return content or _DEFAULT_PROMPT

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def transform(
        self,
        chunks: Sequence[Chunk],
        trace: TraceContext | None = None,
    ) -> list[Chunk]:
        out: list[Chunk] = []
        captioned = 0
        degraded = 0
        for chunk in chunks:
            image_refs = chunk.metadata.get("image_refs")
            if not image_refs:
                out.append(chunk)
                continue
            try:
                new_chunk, ok_count, fail_count = self._caption_one(chunk)
            except Exception as exc:
                self._logger.warning("chunk %s caption failed, keep original: %s", chunk.id, exc)
                metadata = dict(chunk.metadata)
                metadata["has_unprocessed_images"] = True
                degraded += 1
                out.append(
                    Chunk(
                        id=chunk.id,
                        text=chunk.text,
                        metadata=metadata,
                        start_offset=chunk.start_offset,
                        end_offset=chunk.end_offset,
                        source_ref=chunk.source_ref,
                    )
                )
                continue
            captioned += ok_count
            degraded += fail_count
            out.append(new_chunk)
        if trace is not None:
            trace.record_stage(
                "transform",
                method=self.name,
                details={
                    "chunks_in": len(chunks),
                    "chunks_out": len(out),
                    "images_captioned": captioned,
                    "images_degraded": degraded,
                    "enabled": self._enabled,
                },
            )
        return out

    # ------------------------------------------------------------------
    # 单 chunk 流程
    # ------------------------------------------------------------------

    def _caption_one(self, chunk: Chunk) -> tuple[Chunk, int, int]:
        """为单个含图 chunk 生成 caption，返回 (新 chunk, 成功数, 失败数)。"""
        metadata = dict(chunk.metadata)
        image_refs = metadata.get("image_refs") or []
        images_by_id = {
            img.get("id"): img
            for img in (metadata.get("images") or [])
            if isinstance(img, dict) and img.get("id")
        }
        context = self._image_context(chunk.text)
        captions: dict[str, str] = {}
        failures = 0
        if not self._enabled or self._vision_llm is None:
            # 降级模式：保留引用，只打标记。
            metadata["has_unprocessed_images"] = True
            if self._enabled and self._llm_unavailable_reason:
                metadata["image_caption_fallback"] = self._llm_unavailable_reason
            return _copy(chunk, metadata), 0, len(image_refs)
        for image_id in image_refs:
            ref = images_by_id.get(image_id)
            path = str(ref.get("path")) if ref else None
            if not path:
                self._logger.warning("image %s has no path record, skip", image_id)
                failures += 1
                continue
            if not Path(path).exists():
                self._logger.warning("image %s file missing: %s", image_id, path)
                failures += 1
                continue
            try:
                prompt = self._prompt.replace("{image_context}", context)
                response = self._vision_llm.chat_with_image(prompt, path)
                caption = (response.content or "").strip()
            except Exception as exc:
                self._logger.warning("caption image %s failed: %s", image_id, exc)
                failures += 1
                continue
            if not caption:
                self._logger.warning("caption image %s got empty response", image_id)
                failures += 1
                continue
            captions[image_id] = caption
        if captions:
            metadata["image_captions"] = captions
        if failures:
            metadata["has_unprocessed_images"] = True
        return _copy(chunk, metadata), len(captions), failures

    @staticmethod
    def _image_context(text: str) -> str:
        """图片所在 chunk 的上下文文本（截断防 prompt 超长）。"""
        compact = text.strip()
        if len(compact) > _CONTEXT_MAX_CHARS:
            compact = compact[:_CONTEXT_MAX_CHARS] + "…"
        return compact


def _copy(chunk: Chunk, metadata: dict) -> Chunk:
    return Chunk(
        id=chunk.id,
        text=chunk.text,
        metadata=metadata,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        source_ref=chunk.source_ref,
    )
