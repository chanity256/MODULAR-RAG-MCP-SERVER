"""Vision LLM 抽象基类（阶段 B8）。

扩展 ``BaseLLM``，增加多模态输入能力（文本 + 图片）。
为 C7 的 ``ImageCaptioner`` 提供底层抽象。

设计要点：
- ``chat_with_image(text, image_path, trace)`` 为统一入口，返回 ``ChatResponse``。
- ``image_path`` 接受文件路径（``str`` / ``Path``）或原始字节（``bytes``）。
- 提供图片预处理扩展点（``_preprocess_image``），子类可覆盖以实现压缩/格式转换。
- 图片加载与 base64 编码逻辑封装在基类，子类只需实现 ``_send_image_request``。
"""

from __future__ import annotations

import base64
from abc import abstractmethod
from pathlib import Path
from typing import Any

from libs.llm.base_llm import BaseLLM, ChatResponse

__all__ = ["BaseVisionLLM"]

# 扩展名 -> MIME type 映射
_EXT_TO_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_DEFAULT_MIME = "image/png"


class BaseVisionLLM(BaseLLM):
    """Vision LLM 抽象基类，支持文本 + 图片输入。"""

    @abstractmethod
    def chat_with_image(
        self,
        text: str,
        image: str | Path | bytes,
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """根据文本和图片生成回复。

        Args:
            text: 文本提示/指令。
            image: 图片文件路径（``str``/``Path``）或原始字节（``bytes``）。
            trace: 可选的 ``TraceContext``（当前占位）。
            **kwargs: provider 特定的扩展参数。

        Returns:
            统一的 ``ChatResponse``。
        """
        raise NotImplementedError

    # -- 图片辅助方法（子类可直接使用） -----------------------------------

    @staticmethod
    def load_image(image: str | Path | bytes) -> tuple[bytes, str]:
        """加载图片，返回 (raw_bytes, media_type)。

        Args:
            image: 文件路径或原始字节。

        Returns:
            ``(raw_bytes, media_type)`` 元组。
        """
        if isinstance(image, bytes):
            # 原始字节：默认 PNG，可被 kwargs 覆盖
            return image, _DEFAULT_MIME
        p = Path(image)
        if not p.exists():
            raise FileNotFoundError(f"image file not found: {p}")
        raw = p.read_bytes()
        media_type = _EXT_TO_MIME.get(p.suffix.lower(), _DEFAULT_MIME)
        return raw, media_type

    @staticmethod
    def encode_image_base64(image_bytes: bytes) -> str:
        """将图片字节编码为 base64 字符串。"""
        return base64.b64encode(image_bytes).decode("ascii")

    def prepare_image(
        self,
        image: str | Path | bytes,
        *,
        max_size: int | None = None,
    ) -> tuple[str, str]:
        """加载 + 预处理 + base64 编码图片，返回 ``(base64_str, media_type)``。

        Args:
            image: 文件路径或原始字节。
            max_size: 最大尺寸（像素），触发预处理扩展点。

        Returns:
            ``(base64_str, media_type)`` 元组。
        """
        raw, media_type = self.load_image(image)
        if max_size is not None:
            raw = self._preprocess_image(raw, max_size, media_type)
        b64 = self.encode_image_base64(raw)
        return b64, media_type

    def _preprocess_image(self, image_bytes: bytes, max_size: int, media_type: str) -> bytes:
        """图片预处理扩展点（压缩/格式转换）。

        默认实现：不处理，直接返回原始字节。
        子类可覆盖以实现实际压缩逻辑（如用 Pillow resize）。

        Args:
            image_bytes: 原始图片字节。
            max_size: 最大尺寸（像素）。
            media_type: 图片 MIME type。

        Returns:
            处理后的图片字节。
        """
        return image_bytes
