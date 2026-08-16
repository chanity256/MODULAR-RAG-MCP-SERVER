"""MetadataEnricher：语义元数据注入（阶段 C6）。

为每个 chunk 生成 ``title`` / ``summary`` / ``tags`` 三项元数据：

- **规则模式（兜底，默认）**：标题取首个 Markdown 标题或首行；摘要取开头
  若干句（~120 字符）；标签取高频关键词（去停用词）。确定性、零成本、非空。
- **LLM 模式（``ingestion.metadata_enricher.use_llm`` 打开）**：读取内置
  prompt（``{text}`` 占位符）调用 LLM，要求输出严格 JSON
  ``{"title","summary","tags"}``；解析失败/字段非法/调用异常时降级回规则
  结果，并在 ``metadata["enrich_fallback"]`` 记录原因，不抛致命异常。

标记约定：``metadata["enriched_by"]``（"llm" / "rule"）。
单条 chunk 异常保留原文继续处理其余 chunk。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.transform.base_transform import BaseTransform
from libs.llm import BaseLLM, LLMFactory
from observability.logger import get_logger

__all__ = ["MetadataEnricher"]

#: 内置 prompt 模板（要求严格 JSON 输出，便于解析）。
DEFAULT_PROMPT_TEMPLATE = """你是一位文档元数据提取助手。请阅读下面的文档片段，输出一个严格的 JSON 对象（不要输出任何其他文字或代码围栏）：
{"title": "简短小标题（不超过30字）", "summary": "一到两句话的内容摘要", "tags": ["标签1", "标签2", "标签3"]}

文档片段：
{text}"""

# 标题/标签提取用的分词与停用词。
_LATIN_WORD_RE = re.compile(r"[a-z0-9]{2,}")
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
_HEADING_RE = re.compile(r"^#+\s*(.+)$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?.])")

_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "are", "was",
    "into", "not", "you", "your", "can", "will", "的", "了", "是", "在",
    "和", "与", "或", "及", "对", "为", "以", "等", "个", "中",
}

_TITLE_MAX = 80
_SUMMARY_MAX = 160
_TAG_LIMIT = 5


class MetadataEnricher(BaseTransform):
    """规则兜底 + 可选 LLM 增强的元数据注入器（失败降级，不阻塞）。"""

    name = "metadata_enricher"

    def __init__(self, settings: Settings, llm: BaseLLM | None = None) -> None:
        self._use_llm = settings.ingestion.metadata_enricher.use_llm
        self._logger = get_logger("modular-rag.ingestion.metadata_enricher")
        self._llm: BaseLLM | None = llm
        self._llm_unavailable_reason: str | None = None
        if self._use_llm and llm is None:
            try:
                self._llm = LLMFactory.create(settings)
            except Exception as exc:
                self._logger.warning("LLM creation failed, fallback to rule mode: %s", exc)
                self._llm = None
                self._llm_unavailable_reason = f"llm_init_failed: {exc}"

    @property
    def use_llm(self) -> bool:
        return self._use_llm

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def transform(
        self,
        chunks: Sequence[Chunk],
        trace: TraceContext | None = None,
    ) -> list[Chunk]:
        enriched: list[Chunk] = []
        llm_enriched = 0
        fallbacks = 0
        for chunk in chunks:
            try:
                title, summary, tags, enriched_by, fallback_reason = self._enrich_one(chunk)
            except Exception as exc:
                self._logger.warning("chunk %s enrich failed, keep original: %s", chunk.id, exc)
                enriched.append(chunk)
                fallbacks += 1
                continue
            metadata = dict(chunk.metadata)
            metadata["title"] = title
            metadata["summary"] = summary
            metadata["tags"] = tags
            metadata["enriched_by"] = enriched_by
            if fallback_reason:
                metadata["enrich_fallback"] = fallback_reason
                fallbacks += 1
            enriched.append(
                Chunk(
                    id=chunk.id,
                    text=chunk.text,
                    metadata=metadata,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    source_ref=chunk.source_ref,
                )
            )
            if enriched_by == "llm":
                llm_enriched += 1
        if trace is not None:
            trace.record_stage(
                "transform",
                method=self.name,
                details={
                    "chunks_in": len(chunks),
                    "chunks_out": len(enriched),
                    "llm_enriched": llm_enriched,
                    "fallback": fallbacks,
                    "use_llm": self._use_llm,
                },
            )
        return enriched

    # ------------------------------------------------------------------
    # 单 chunk 流程
    # ------------------------------------------------------------------

    def _enrich_one(self, chunk: Chunk) -> tuple[str, str, list[str], str, str | None]:
        title, summary, tags = self._rule_enrich(chunk.text, chunk.metadata)
        enriched_by = "rule"
        fallback_reason: str | None = None
        if self._use_llm:
            if self._llm is None:
                fallback_reason = self._llm_unavailable_reason or "llm_unavailable"
            else:
                try:
                    parsed = self._llm_enrich(chunk.text)
                except Exception as exc:
                    parsed = None
                    fallback_reason = f"llm_error: {exc}"
                    self._logger.warning("llm enrich failed, fallback to rule: %s", exc)
                else:
                    if parsed is None:
                        fallback_reason = "llm_invalid_json"
                if parsed is not None:
                    title, summary, tags = parsed
                    enriched_by = "llm"
                    fallback_reason = None
        return title, summary, tags, enriched_by, fallback_reason

    # ------------------------------------------------------------------
    # 规则模式
    # ------------------------------------------------------------------

    def _rule_enrich(
        self, text: str, metadata: dict
    ) -> tuple[str, str, list[str]]:
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        title = self._derive_title(lines, metadata)
        summary = self._derive_summary(text)
        tags = self._derive_tags(text, metadata)
        return title, summary, tags

    @staticmethod
    def _derive_title(lines: list[str], metadata: dict) -> str:
        for line in lines:
            match = _HEADING_RE.match(line)
            if match and match.group(1).strip():
                return match.group(1).strip()[:_TITLE_MAX]
        if lines:
            return lines[0][:_TITLE_MAX]
        source = str(metadata.get("source_path") or "chunk")
        return (Path(source).stem or "untitled")[:_TITLE_MAX]

    @staticmethod
    def _derive_summary(text: str) -> str:
        compact = re.sub(r"\s+", " ", text.strip())
        if not compact:
            return "（空内容）"
        parts: list[str] = []
        total = 0
        for sentence in _SENTENCE_SPLIT_RE.split(compact):
            parts.append(sentence)
            total += len(sentence)
            if total >= 120:
                break
        summary = "".join(parts).strip()
        if len(summary) > _SUMMARY_MAX:
            summary = summary[:_SUMMARY_MAX].rstrip() + "…"
        return summary

    @staticmethod
    def _derive_tags(text: str, metadata: dict) -> list[str]:
        lowered = text.lower()
        counts: Counter[str] = Counter(
            word
            for word in _LATIN_WORD_RE.findall(lowered)
            if word not in _STOPWORDS and not word.isdigit()
        )
        tags = [word for word, _ in counts.most_common(_TAG_LIMIT)]
        if len(tags) < _TAG_LIMIT and _CJK_CHAR_RE.search(text):
            tags.append("中文")
        if not tags:
            doc_type = str(metadata.get("doc_type") or "document")
            tags = [doc_type]
        # 去重保序。
        return list(dict.fromkeys(tags))[:_TAG_LIMIT]

    # ------------------------------------------------------------------
    # LLM 模式
    # ------------------------------------------------------------------

    def _llm_enrich(self, text: str) -> tuple[str, str, list[str]] | None:
        """调用 LLM 提取元数据；解析/校验失败返回 None（由上层降级）。"""
        prompt = DEFAULT_PROMPT_TEMPLATE.replace("{text}", text)
        raw = self._llm.chat_text(prompt)  # type: ignore[union-attr]
        return self._parse_llm_json(raw)

    @staticmethod
    def _parse_llm_json(raw: str) -> tuple[str, str, list[str]] | None:
        """解析 LLM 输出为 (title, summary, tags)；非法返回 None。"""
        if not raw or not raw.strip():
            return None
        # 兼容代码围栏包裹（```json ... ```）。
        candidate = raw.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", candidate).strip()
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError:
            return None
        title = data.get("title")
        summary = data.get("summary")
        tags = data.get("tags")
        if not isinstance(title, str) or not title.strip():
            return None
        if not isinstance(summary, str) or not summary.strip():
            return None
        if (
            not isinstance(tags, list)
            or not tags
            or not all(isinstance(t, str) and t.strip() for t in tags)
        ):
            return None
        return title.strip(), summary.strip(), [t.strip() for t in tags]
