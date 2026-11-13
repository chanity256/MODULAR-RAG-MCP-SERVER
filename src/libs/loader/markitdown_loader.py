"""通用格式 Loader（IR5.1，基于 MarkItDown 可选依赖）。

支持 DOCX / PPTX / XLSX / HTML 转纯文本（Markdown），产出与 ``PdfLoader`` 同构的
``Document``：``id`` 为文件内容 SHA256，metadata 含 ``source_path`` / ``doc_type`` /
``sha256``。不抽取图片（``image_refs`` 恒空，``images`` 字段省略）。

``.md`` 暂不在支持清单内：Markdown 结构化摄取归 MarkdownLoader（已排期
INGESTION_ROBUSTNESS_SPEC M6 / IR7，落地前避免双登记冲突）。

``markitdown`` 是可选依赖（``pip install 'modular-rag-mcp-server[ingest-formats]'``），
只在 ``load`` 时惰性导入，模块 import 期不触发，未安装时其余功能零影响。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from core.types import Document
from libs.loader.base_loader import BaseLoader

__all__ = [
    "INSTALL_HINT",
    "MarkitdownLoader",
    "MarkitdownLoaderError",
    "MarkitdownNotInstalledError",
    "SUPPORTED_EXTENSIONS",
]

# 扩展名 -> metadata.doc_type。
_DOC_TYPES: dict[str, str] = {
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".html": "html",
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_DOC_TYPES)

INSTALL_HINT = (
    "pip install 'modular-rag-mcp-server[ingest-formats]' "
    "(or: pip install 'markitdown[docx,pptx,xlsx]')"
)

_CHUNK_SIZE = 64 * 1024


class MarkitdownLoaderError(RuntimeError):
    """MarkitdownLoader 解析失败（扩展名不支持、转换异常、抽取文本为空）。"""


class MarkitdownNotInstalledError(MarkitdownLoaderError, ImportError):
    """可选依赖 ``markitdown`` 未安装（消息含安装指引）。"""


class MarkitdownLoader(BaseLoader):
    """DOCX/PPTX/XLSX/HTML -> ``Document`` 加载器（纯文本，无图片）。"""

    def load(self, path: str) -> Document:
        file_path = Path(path)
        suffix = file_path.suffix.lower()
        doc_type = _DOC_TYPES.get(suffix)
        if doc_type is None:
            raise MarkitdownLoaderError(
                f"unsupported extension {suffix!r} for {file_path} "
                f"(supported: {sorted(SUPPORTED_EXTENSIONS)})"
            )
        if not file_path.is_file():
            raise FileNotFoundError(f"file not found or not a regular file: {file_path}")

        converter_cls = self._import_markitdown()
        doc_hash = self._compute_sha256(file_path)
        try:
            result = converter_cls().convert(str(file_path))
        except Exception as exc:
            raise MarkitdownLoaderError(
                f"markitdown failed to convert {file_path}: {exc}"
            ) from exc

        text = getattr(result, "text_content", None) or ""
        if not isinstance(text, str):
            text = str(text)
        if not text.strip():
            # 空文本当作 load 失败而非产出空 Document，便于按 failed_stage 诊断上游解析问题。
            raise MarkitdownLoaderError(
                f"markitdown extracted no text from {file_path} (doc_type={doc_type})"
            )

        metadata: dict[str, Any] = {
            "source_path": str(path),
            "doc_type": doc_type,
            "sha256": doc_hash,
        }
        return Document(id=doc_hash, text=text, metadata=metadata)

    @staticmethod
    def _import_markitdown() -> type:
        try:
            from markitdown import MarkItDown  # type: ignore[import-not-found]
        except ImportError as exc:
            raise MarkitdownNotInstalledError(
                f"optional dependency 'markitdown' is not installed; "
                f"install it with: {INSTALL_HINT}"
            ) from exc
        return MarkItDown

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
