"""多模态内容组装器 — 阶段 E6。

检索命中的 chunk 携带 ``metadata.images``（``ImageRef`` 列表，DEV_SPEC
3.5）时，读取本地图片文件并编码为 Base64，追加 MCP ImageContent 到
响应 ``content`` 数组（3.2.5 约定：``content[0]`` 恒为 Markdown 文本，
图像作为后续项）::

    {"type": "image", "data": "<base64>", "mimeType": "image/png"}

设计要点：
- **纯追加**：不改动 ``content[0]`` 文本与 ``structuredContent.citations``
  （Client 兼容性优先）；图像信息另记 ``structuredContent.images``。
- **降级不阻塞**：单张图片缺失/读取失败只记 warning 并跳过（返回仍为
  合法响应），与 3.5.5 降级策略一致。
- **上限保护**：默认最多返回 ``max_images`` 张，防止大文档拖垮响应体。
- ``mimeType`` 按扩展名推断，缺省 ``image/png``。
"""

from __future__ import annotations

import base64
import mimetypes
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from core.types import RetrievalResult, parse_image_refs
from observability.logger import get_logger

__all__ = ["MultimodalAssembler", "DEFAULT_MAX_IMAGES", "DEFAULT_MIME_TYPE"]

#: 单次响应最多返回的图片数。
DEFAULT_MAX_IMAGES = 10
#: 扩展名无法识别时的缺省 MIME。
DEFAULT_MIME_TYPE = "image/png"


class MultimodalAssembler:
    """把命中 chunk 关联的图片组装进 MCP tool result。"""

    def __init__(self, max_images: int = DEFAULT_MAX_IMAGES, logger: Any = None) -> None:
        if not isinstance(max_images, int) or isinstance(max_images, bool) or max_images < 1:
            raise ValueError(f"max_images must be a positive integer, got {max_images!r}")
        self._max_images = max_images
        self._logger = logger or get_logger("modular-rag.response.multimodal_assembler")

    def assemble(
        self, tool_result: dict[str, Any], retrieval_results: Sequence[RetrievalResult]
    ) -> dict[str, Any]:
        """把图片 ImageContent 追加到 ``tool_result``（就地修改并返回）。

        Args:
            tool_result: ``ResponseBuilder.build`` 产出的 MCP tool result。
            retrieval_results: 检索结果（``metadata.images`` 提供图片引用）。

        Returns:
            同一 ``tool_result`` dict（``content`` 追加 image 项，
            ``structuredContent.images`` 记录图像索引）。

        Raises:
            TypeError: 输入类型不符；``metadata.images`` 非法结构
                （透传 ``parse_image_refs`` fail-fast）。
        """
        if not isinstance(tool_result, dict) or "content" not in tool_result:
            raise TypeError("tool_result must be an MCP tool result dict with 'content'")
        if not isinstance(retrieval_results, (list, tuple)):
            raise TypeError(
                f"retrieval_results must be a list of RetrievalResult, got {type(retrieval_results).__name__}"
            )
        images: list[dict[str, Any]] = []
        for result in retrieval_results:
            for ref in parse_image_refs(result.metadata or {}):
                if len(images) >= self._max_images:
                    self._logger.warning(
                        "image limit reached (%d), skipping remaining images", self._max_images
                    )
                    return self._finish(tool_result, images)
                item = self._load(ref.id, ref.path, result.chunk_id)
                if item is not None:
                    images.append(item)
                    tool_result["content"].append(
                        {"type": "image", "data": item["data"], "mimeType": item["mimeType"]}
                    )
        return self._finish(tool_result, images)

    def _load(self, image_id: str, path: str, chunk_id: str) -> dict[str, Any] | None:
        """读取单个图片文件并编码 Base64；失败记 warning 返回 None。"""
        file_path = Path(path)
        try:
            payload = file_path.read_bytes()
            data = base64.b64encode(payload).decode("ascii")
        except OSError as exc:
            self._logger.warning("image %s (%s) unreadable, skipped: %s", image_id, path, exc)
            return None
        return {
            "id": image_id,
            "chunk_id": chunk_id,
            "mimeType": self._mime_type(file_path),
            "data": data,
        }

    @staticmethod
    def _mime_type(file_path: Path) -> str:
        guessed = mimetypes.guess_type(file_path.name)[0]
        if guessed and guessed.startswith("image/"):
            return guessed
        return DEFAULT_MIME_TYPE

    @staticmethod
    def _finish(tool_result: dict[str, Any], images: list[dict[str, Any]]) -> dict[str, Any]:
        structured = tool_result.setdefault("structuredContent", {})
        structured["images"] = [
            {k: item[k] for k in ("id", "chunk_id", "mimeType")} for item in images
        ]
        return tool_result
