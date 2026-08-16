"""DocumentChunker：Document → Chunks 业务适配层（阶段 C4）。

职责边界（与 ``libs.splitter`` 互补，不重复造轮子）：
- ``libs.splitter``：纯文本切分工具（``str -> List[str]``），不涉及业务对象。
- ``DocumentChunker``：业务适配器（``Document -> List[Chunk]``），提供 6 项增值：
  1. Chunk ID 生成：确定性 ID ``{doc_id}_{index:04d}_{content_hash前8位}``。
  2. 元数据继承：Document.metadata 复制到每个 Chunk.metadata。
  3. chunk_index：记录 chunk 在文档中的序号（从 0 开始）。
  4. source_ref 溯源：Chunk.source_ref 指向父 Document.id。
  5. 图片引用按需分发：扫描 chunk 文本中的 ``[IMAGE: {id}]`` 占位符，
     从文档级 ``metadata["images"]`` 提取该 chunk 实际引用的子集写入
     ``chunk.metadata["images"]``，并把 image_id 列表写入
     ``chunk.metadata["image_refs"]``；无占位符的 chunk 不含这两个字段
     （不可整体继承或丢弃文档级 images，否则 C7 ImageCaptioner 无法定位）。
  6. 类型转换：``List[str]`` -> 符合 ``core.types`` 契约的 ``List[Chunk]``。

切分策略由 ``Settings.splitter`` 驱动（经 ``SplitterFactory`` 创建），
修改 ``chunk_size`` 等配置即可改变切分结果，零代码切换。
"""

from __future__ import annotations

import hashlib

from core.settings import Settings
from core.types import Chunk, Document, iter_image_placeholders
from libs.splitter.splitter_factory import SplitterFactory

__all__ = ["DocumentChunker"]


class DocumentChunker:
    """``Document`` → ``List[Chunk]`` 转换器（调用 ``libs.splitter`` 完成纯文本切分）。"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._splitter = SplitterFactory.create(settings)

    @property
    def splitter(self):
        """当前使用的 ``BaseSplitter`` 实例（便于测试与诊断）。"""
        return self._splitter

    def split_document(self, document: Document) -> list[Chunk]:
        """将 ``Document`` 切分为 ``Chunk`` 列表。

        Args:
            document: Loader 产出的文档对象。

        Returns:
            按文档顺序排列的 ``Chunk`` 列表（ID 确定性且唯一）。
        """
        texts = self._splitter.split_text(document.text)
        chunks: list[Chunk] = []
        for index, text in enumerate(texts):
            chunks.append(
                Chunk(
                    id=self._generate_chunk_id(document.id, index, text),
                    text=text,
                    metadata=self._inherit_metadata(document, index, text),
                    source_ref=document.id,
                )
            )
        return chunks

    @staticmethod
    def _generate_chunk_id(doc_id: str, index: int, text: str) -> str:
        """生成确定性 Chunk ID：``{doc_id}_{index:04d}_{content_hash[:8]}``。"""
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        return f"{doc_id}_{index:04d}_{content_hash}"

    @staticmethod
    def _inherit_metadata(document: Document, chunk_index: int, chunk_text: str) -> dict:
        """元数据继承 + 图片引用按需分发。

        - 复制全部 Document.metadata 并追加 ``chunk_index``。
        - 覆写图片字段：有占位符时写入子集 ``images`` + ``image_refs``；
          无占位符时移除文档级 ``images``（避免下游误判该 chunk 含图）。
        """
        metadata = dict(document.metadata)
        metadata["chunk_index"] = chunk_index
        image_ids = [image_id for image_id, _ in iter_image_placeholders(chunk_text)]
        doc_images = document.metadata.get("images") or []
        if image_ids:
            by_id = {img["id"]: img for img in doc_images if isinstance(img, dict) and img.get("id")}
            metadata["images"] = [by_id[i] for i in image_ids if i in by_id]
            metadata["image_refs"] = image_ids
        else:
            metadata.pop("images", None)
        return metadata
