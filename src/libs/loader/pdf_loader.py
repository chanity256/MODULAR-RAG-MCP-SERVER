"""PDF Loader（阶段 C3，最小可跑实现）。

按 DEV_SPEC 3.1 的技术选型，PDF -> Markdown 文本的首选引擎为 MarkItDown；
本实现遵循"优雅降级"原则：

- ``engine="auto"``（默认）：优先尝试 MarkItDown（未安装/转换失败时自动回退
  pypdf，并记录 warning）；``engine="pypdf"`` 强制使用 pypdf；
  ``engine="markitdown"`` 强制要求 MarkItDown 可用（否则抛错）。
- 图片提取统一使用 pypdf（硬依赖）：遍历每页 XObject 图像资源，保存到
  ``{images_root}/{doc_hash}/`` 目录，并在文本中插入 ``[IMAGE: {image_id}]``
  占位符（pypdf 引擎下插入对应页文本之后；整块文本引擎下追加到文末），
  同时按 C1 契约写入 ``metadata.images``。
- 降级行为：单张图片提取/编码失败仅记录 warning 并跳过，不阻塞文本解析；
  图片提取整体失败时返回纯文本 Document。

元数据约定：``source_path`` / ``doc_type="pdf"`` / ``sha256`` / ``page_count``
（可选 ``title``），有图片时另含 ``images``（无图片时省略该字段）。
"""

from __future__ import annotations

import binascii
import hashlib
import struct
import zlib
from pathlib import Path
from typing import Any, Iterator

from pypdf import PdfReader

from core.types import Document, ImageRef, image_placeholder, make_image_id
from libs.loader.base_loader import BaseLoader
from observability.logger import get_logger

__all__ = ["PdfLoader", "PdfLoaderError", "UnsupportedImageError"]

# 流式读取块大小（与 file_integrity 保持一致）。
_CHUNK_SIZE = 64 * 1024

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# 颜色空间 -> (PNG color_type, 通道数)；仅支持 8bit 的 RGB/Gray 转 PNG。
_PNG_COLORSPACES = {"/DeviceRGB": (2, 3), "/DeviceGray": (0, 1)}


class PdfLoaderError(RuntimeError):
    """PdfLoader 配置或引擎错误（例如强制要求的引擎不可用）。"""


class UnsupportedImageError(ValueError):
    """图片编码格式不支持（调用方应降级跳过，不阻塞文本解析）。"""


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _raw_to_png(data: bytes, width: int, height: int, color_type: int, channels: int) -> bytes:
    """将未压缩的 8bit 行序原始像素编码为 PNG（无需 Pillow）。"""
    stride = width * channels
    if len(data) < stride * height:
        raise UnsupportedImageError(
            f"raw pixel data too short: got {len(data)}, need {stride * height}"
        )
    raw = b"".join(
        b"\x00" + data[y * stride:(y + 1) * stride] for y in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


class PdfLoader(BaseLoader):
    """PDF -> ``Document`` 加载器（文本 + 图片提取，遵循 C1 多模态契约）。

    Args:
        images_root: 图片输出根目录（默认 ``data/images``），
            实际写入 ``{images_root}/{doc_hash}/{image_id}.{ext}``。
        engine: 文本解析引擎 ``"auto"`` / ``"pypdf"`` / ``"markitdown"``。
    """

    def __init__(self, images_root: str = "data/images", *, engine: str = "auto") -> None:
        if engine not in ("auto", "pypdf", "markitdown"):
            raise PdfLoaderError(f"unknown engine: {engine!r} (expected auto/pypdf/markitdown)")
        self._images_root = Path(images_root)
        self._engine = engine
        self._logger = get_logger("modular-rag.loader.pdf")
        self._last_engine: str | None = None

    @property
    def last_engine(self) -> str | None:
        """最近一次 ``load`` 实际使用的文本引擎（pypdf / markitdown）。"""
        return self._last_engine

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def load(self, path: str) -> Document:
        """解析 PDF 为 ``Document``（图片失败降级，不阻塞文本）。"""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"file not found or not a regular file: {file_path}")

        doc_hash = self._compute_sha256(file_path)
        text, pages = self._extract_text(file_path)
        image_refs: list[ImageRef] = []
        try:
            text, image_refs = self._extract_images(file_path, doc_hash, text, pages)
        except Exception as exc:  # 图片提取整体失败 → 降级为纯文本
            self._logger.warning("image extraction failed for %s: %s", file_path, exc)

        metadata: dict[str, Any] = {
            "source_path": str(path),
            "doc_type": "pdf",
            "sha256": doc_hash,
        }
        page_count = self._safe_page_count(file_path)
        if page_count is not None:
            metadata["page_count"] = page_count
        title = self._safe_title(file_path)
        if title:
            metadata["title"] = title
        if image_refs:
            metadata["images"] = [ref.to_dict() for ref in image_refs]
        return Document(id=doc_hash, text=text, metadata=metadata)

    # ------------------------------------------------------------------
    # 文本提取
    # ------------------------------------------------------------------

    def _extract_text(self, file_path: Path) -> tuple[str, list[str] | None]:
        """返回 ``(整块文本, 分页文本或 None)``。

        pypdf 引擎可返回分页文本（用于图片占位符按页落位）；
        MarkItDown 产出整块 Markdown，无法分页（返回 None）。
        """
        if self._engine in ("auto", "markitdown"):
            try:
                text = self._extract_with_markitdown(file_path)
                if text.lstrip().startswith("%PDF-"):
                    # markitdown 未装 [pdf] 依赖时可能把原始 PDF 字节经
                    # 纯文本兜底转换器原样透传（0.1.8 实测），这不构成
                    # 成功转换——按转换失败处理（降级 pypdf / 强制引擎报错）。
                    raise PdfLoaderError(
                        f"markitdown returned raw PDF content for {file_path} "
                        "(install 'markitdown[pdf]' for real PDF conversion)"
                    )
                self._last_engine = "markitdown"
                return text, None
            except ImportError:
                if self._engine == "markitdown":
                    raise PdfLoaderError(
                        "engine 'markitdown' requested but the package is not installed"
                    ) from None
                self._logger.warning(
                    "markitdown not installed, falling back to pypdf for %s", file_path
                )
            except Exception as exc:
                if self._engine == "markitdown":
                    raise PdfLoaderError(f"markitdown failed to convert {file_path}: {exc}") from exc
                # Note: 原始字节透传守卫的取舍 — 见
                # .agents/notes/implemented/bug-fix/2026-10-07-pdf-loader-raw-passthrough-guard.md
                self._logger.warning(
                    "markitdown conversion failed (%s); falling back to pypdf for %s", exc, file_path
                )
        pages = self._extract_with_pypdf(file_path)
        self._last_engine = "pypdf"
        return "\n\n".join(pages), pages

    def _extract_with_markitdown(self, file_path: Path) -> str:
        """惰性导入 MarkItDown 并转换（未安装抛 ImportError 供上层降级）。"""
        from markitdown import MarkItDown  # type: ignore[import-not-found]

        result = MarkItDown().convert(str(file_path))
        return result.text_content or ""

    def _extract_with_pypdf(self, file_path: Path) -> list[str]:
        reader = PdfReader(str(file_path))
        return [page.extract_text() or "" for page in reader.pages]

    # ------------------------------------------------------------------
    # 图片提取与占位符插入
    # ------------------------------------------------------------------

    def _extract_images(
        self,
        file_path: Path,
        doc_hash: str,
        text: str,
        pages: list[str] | None,
    ) -> tuple[str, list[ImageRef]]:
        """提取全部图片并在文本中插入占位符；单图失败仅告警跳过。

        Returns:
            ``(新文本, ImageRef 列表)``；无图片时原文本不变、列表为空。
        """
        reader = PdfReader(str(file_path))
        page_images: list[list[ImageRef]] = [[] for _ in reader.pages]
        for page_idx, page in enumerate(reader.pages):
            for seq, img_obj in enumerate(self._iter_page_images(page)):
                try:
                    ref = self._store_image(doc_hash, page_idx, seq, img_obj)
                except Exception as exc:
                    self._logger.warning(
                        "skip image #%d on page %d of %s: %s", seq, page_idx, file_path, exc
                    )
                    continue
                page_images[page_idx].append(ref)

        all_refs = [ref for refs in page_images for ref in refs]
        if not all_refs:
            return text, []

        # 按页落位（pypdf）或统一追加文末（整块文本引擎）。
        segments: list[tuple[str, list[ImageRef]]] = []
        if pages is not None:
            for idx, page_text in enumerate(pages):
                refs = page_images[idx] if idx < len(page_images) else []
                segments.append((page_text, refs))
        else:
            segments.append((text, all_refs))

        new_text = ""
        for seg_text, refs in segments:
            new_text = self._append_segment(new_text, seg_text)
            for ref in refs:
                placeholder = image_placeholder(ref.id)
                new_text = self._append_segment(new_text, placeholder)
                # 回算偏移，兼容 "\n\n" 分隔符的存在。
                ref.text_offset = len(new_text) - len(placeholder)
                ref.text_length = len(placeholder)
        return new_text, all_refs

    @staticmethod
    def _append_segment(current: str, segment: str) -> str:
        return segment if not current else current + "\n\n" + segment

    def _iter_page_images(self, page: Any) -> Iterator[Any]:
        """遍历单页 ``/Resources`` 中的 XObject 图像（流对象）。

        直接遍历资源树而非 ``page.images``，避免对 Pillow 的硬依赖；
        流解码（含 FlateDecode）延迟到 ``_store_image`` 内并纳入降级 try 块。
        """
        resources = page.get("/Resources")
        if not resources:
            return
        xobjects = resources.get("/XObject")
        if not xobjects:
            return
        for ref in xobjects.values():
            obj = ref.get_object()
            if obj.get("/Subtype") == "/Image":
                yield obj

    def _store_image(
        self,
        doc_hash: str,
        page_idx: int,
        seq: int,
        img_obj: Any,
    ) -> ImageRef:
        """解码、编码并落盘一张图片，返回 ``ImageRef``（偏移由调用方回填）。"""
        image_id = make_image_id(doc_hash, page_idx, seq)
        data = img_obj.get_data()  # 应用 /Filter 解码，失败由调用方降级
        ext, payload, width, height = self._encode_image(img_obj, data)
        out_dir = self._images_root / doc_hash
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{image_id}{ext}"
        out_path.write_bytes(payload)
        return ImageRef(
            id=image_id,
            path=str(out_path),
            page=page_idx,
            position={"width": width, "height": height},
        )

    def _encode_image(self, img_obj: Any, data: bytes) -> tuple[str, bytes, int, int]:
        """按 XObject 元数据选择编码：JPEG 直存，RGB/Gray 原始像素转 PNG。

        Returns:
            ``(扩展名, 文件字节, width, height)``。

        Raises:
            UnsupportedImageError: 颜色空间/位深不支持时（调用方降级跳过）。
        """
        filters = str(img_obj.get("/Filter"))
        width = int(img_obj.get("/Width") or 0)
        height = int(img_obj.get("/Height") or 0)
        if "DCTDecode" in filters:
            return ".jpg", data, width, height
        if "JPXDecode" in filters:
            return ".jp2", data, width, height

        colorspace = str(img_obj.get("/ColorSpace"))
        bits = int(img_obj.get("/BitsPerComponent") or 8)
        png_spec = _PNG_COLORSPACES.get(colorspace)
        if png_spec is None or bits != 8 or width <= 0 or height <= 0:
            raise UnsupportedImageError(
                f"unsupported image: colorspace={colorspace} bits={bits} "
                f"size={width}x{height}"
            )
        color_type, channels = png_spec
        return ".png", _raw_to_png(data, width, height, color_type, channels), width, height

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _safe_page_count(self, file_path: Path) -> int | None:
        try:
            return len(PdfReader(str(file_path)).pages)
        except Exception as exc:
            self._logger.warning("failed to read page count for %s: %s", file_path, exc)
            return None

    def _safe_title(self, file_path: Path) -> str | None:
        try:
            title = (PdfReader(str(file_path)).metadata or {}).title
            return str(title) if title else None
        except Exception:
            return None
