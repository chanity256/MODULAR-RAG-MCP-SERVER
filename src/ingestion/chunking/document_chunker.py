"""DocumentChunker：Document → Chunks 业务适配层（阶段 C4）。

职责边界（与 ``libs.splitter`` 互补，不重复造轮子）：
- ``libs.splitter``：纯文本切分工具（``str -> List[str]``），不涉及业务对象。
- ``DocumentChunker``：业务适配器（``Document -> List[Chunk]``），提供 7 项增值：
  1. Chunk ID 生成：确定性 ID ``{doc_id}_{index:04d}_{content_hash前8位}``。
  2. 元数据继承：Document.metadata 复制到每个 Chunk.metadata。
  3. chunk_index：记录 chunk 在文档中的序号（从 0 开始）。
  4. source_ref 溯源：Chunk.source_ref 指向父 Document.id。
  5. 图片引用按需分发：扫描 chunk 文本中的 ``[IMAGE: {id}]`` 占位符，
     从文档级 ``metadata["images"]`` 提取该 chunk 实际引用的子集写入
     ``chunk.metadata["images"]``，并把 image_id 列表写入
     ``chunk.metadata["image_refs"]``；无占位符的 chunk 不含这两个字段
     （不可整体继承或丢弃文档级 images，否则 C7 ImageCaptioner 无法定位）。
  7. 占位符原子保护（G15）：小 chunk_size 下切分器可能把 ``[IMAGE: {id}]``
     从中间切断（图片引用静默丢失）或让占位符独占 chunk（正文为空，
     检索价值低）。默认**原样切分零改动**；仅当检测到这两种碎片时，
     退化为"占位符先提取、后切分、再回填"的提取式切分。

切分策略由 ``Settings.splitter`` 驱动（经 ``SplitterFactory`` 创建），
修改 ``chunk_size`` 等配置即可改变切分结果，零代码切换。
"""

from __future__ import annotations

import hashlib
import re

from core.settings import Settings
from core.types import Chunk, Document, IMAGE_PLACEHOLDER_PATTERN, iter_image_placeholders
from libs.splitter.splitter_factory import SplitterFactory

__all__ = ["DocumentChunker"]

#: 未闭合占位符头片段：文本以一段没有 ``]`` 的 ``[`` 序列结尾。
#: 占位符规范格式为 ``[IMAGE: {id}]``（冒号后有空格、id 内无空格与 ``]``），
#: 故 ``[^\]\n]*`` 覆盖任意切点（``[IMAGE:abc`` / ``[IMAGE: h`` / ``[IM`` /
#: 裸 ``[``）；排除换行防止跨段误报。Markdown 中未闭合的 ``[引用`` 同样
#: 命中——触发提取式切分的代价仅是切分方式变化，无内容损失。
_HEAD_FRAGMENT_RE = re.compile(r"\[[^\]\n]*$")


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
        texts = self._split_preserving_placeholders(document.text)
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

    # ------------------------------------------------------------------
    # 占位符原子保护（G15）
    # ------------------------------------------------------------------

    # Note: 混合守卫 vs 纯后修补 vs 无条件提取式的取舍 — 见 .agents/notes/implemented/bug-fix/2026-10-05-image-placeholder-atomic-guard.md
    def _split_preserving_placeholders(self, text: str) -> list[str]:
        """切分文本并保证 ``[IMAGE: {id}]`` 占位符完整归属单个 chunk。

        混合策略（对常规输入零行为变化）：

        1. 原样切分。若无 chunk 以未闭合 ``[`` 片段结尾、也没有占位符
           独占的 chunk，直接返回原切分结果（正常文档走这条快路径）。
        2. 否则（小 chunk_size 切断占位符 / 占位符独占 chunk）退化为
           **提取式切分**：占位符在切分前提取（切分器看不到它，不可能
           切断），切分后按文本段落归属回填——占位符附着到其前邻文本段
           产出的最后一个 chunk（图随文走）；前邻段为空/纯空白（连续
           占位符、文档开头即占位符）时顺延到后邻段；全文档只有占位符
           时保留为独立 chunk。

        副作用：提取式切分下，附着占位符的 chunk 可能超过 chunk_size
        （语义完整性优先于长度约束，与代码块不打断的取舍一致）。
        """
        texts = self._splitter.split_text(text)
        if not any(
            _HEAD_FRAGMENT_RE.search(t) or self._is_placeholder_only(t) for t in texts
        ):
            return texts

        # 提取式切分：占位符不进切分器
        matches = list(IMAGE_PLACEHOLDER_PATTERN.finditer(text))
        pieces: list[str] = []
        pending: list[str] = []  # 尚未落位的占位符（前邻文本段无产出时顺延）
        cursor = 0
        for m in matches:
            run = text[cursor:m.start()]
            cursor = m.end()
            # 过滤空片段：切分器哨兵/边界可能落在段内产生空串，无内容价值。
            run_pieces = [rp for rp in (self._splitter.split_text(run) if run else []) if rp]
            if run_pieces:
                for rp in run_pieces:
                    if pending:
                        rp = "".join(pending) + rp
                        pending.clear()
                    pieces.append(rp)
                pieces[-1] += m.group(0)  # 占位符附着到前邻文本的最后一段
            else:
                pending.append(m.group(0))
        tail = text[cursor:]
        if tail:
            tail_pieces = [tp for tp in self._splitter.split_text(tail) if tp]
            for tp in tail_pieces:
                if pending:
                    tp = "".join(pending) + tp
                    pending.clear()
                pieces.append(tp)
        if pending:
            pieces.append("".join(pending))  # 全文档只有占位符：独立成 chunk
        return pieces

    @staticmethod
    def _is_placeholder_only(text: str) -> bool:
        """剥掉占位符与空白后无正文（占位符独占 chunk）。"""
        return not IMAGE_PLACEHOLDER_PATTERN.sub("", text).strip()

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
