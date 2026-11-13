"""ChunkRefiner：规则去噪 + 可选 LLM 智能重写（阶段 C5）。

处理顺序（每个 chunk）：
1. **规则去噪**（``_rule_based_refine``）：按代码块分段清洗非代码文本 ——
   去控制字符 / HTML 注释 / 页眉页脚 / 分隔线，折叠多余空白；
   代码块（fenced code）内部格式逐字保留。
2. **可选 LLM 重写**（``_llm_refine``）：``ingestion.chunk_refiner.use_llm``
   打开时，读取 prompt 模板（``{text}`` 占位符）调用 LLM 智能重组；
   LLM 异常 / 空响应 / 未配置时**自动降级回规则结果**，不阻塞 ingestion。

降级标记：``metadata["refined_by"]``（"llm" / "rule"）；
降级原因写入 ``metadata["refine_fallback"]``（仅在发生降级时存在）。
单条 chunk 处理异常时保留原文并继续处理其余 chunk。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.transform.base_transform import BaseTransform
from libs.llm import BaseLLM, LLMFactory
from observability.logger import get_logger

__all__ = ["ChunkRefiner"]

# fenced code block（``` 围栏），内部文本逐字保留。
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# 控制字符（保留 \t=\x09 与 \n=\x0a；移除 \x0c 换页 / \x00 等 PDF 提取噪声）。
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# HTML 注释（跨行）。
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# 行内 2 个以上连续空格/制表符折叠为单个空格。
_INLINE_SPACES_RE = re.compile(r"[ \t]{2,}")

# 3 个以上连续空行折叠为一个空行。
_BLANK_LINES_RE = re.compile(r"\n{3,}")

# 页眉页脚 / 分隔线噪声（整行匹配才移除）。
_PAGE_NOISE_RES = (
    re.compile(r"(?:Page\s+\d+(?:\s*(?:of|/)\s*\d+)?|第\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?)"),
    re.compile(r"\d{1,4}"),  # 独立行号 / 页码
    re.compile(r"[-=_*·•]{3,}"),  # 分隔线
)

#: 内置默认 prompt 模板（prompt 文件缺失时的 fallback，与
#: ``config/prompts/chunk_refinement.txt`` 正文一致）。
DEFAULT_PROMPT_TEMPLATE = """你是一位文档清洗与重组助手。请对下面的文档片段进行智能重组：
1. 去除页眉页脚、多余空白、乱码与无意义格式标记；
2. 合并被物理切断但语义紧密相关的相邻段落；
3. 保留代码块、Markdown 结构与关键术语不被破坏；
4. 输出一个自包含、语义完整的干净片段。

原文：
{text}"""


class ChunkRefiner(BaseTransform):
    """规则去噪 + 可选 LLM 增强的 chunk 精炼器（失败降级，不阻塞 ingestion）。"""

    name = "chunk_refiner"

    def __init__(
        self,
        settings: Settings,
        llm: BaseLLM | None = None,
        prompt_path: str | None = None,
    ) -> None:
        refiner_cfg = settings.ingestion.chunk_refiner
        self._use_llm = refiner_cfg.use_llm
        self._prompt_path = prompt_path or refiner_cfg.prompt_path
        self._logger = get_logger("modular-rag.ingestion.chunk_refiner")
        self._prompt_template = self._load_prompt(self._prompt_path)
        self._llm: BaseLLM | None = llm
        self._llm_unavailable_reason: str | None = None
        if self._use_llm and llm is None:
            # LLM 创建失败不阻塞构造：降级为规则模式并记录原因。
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
        """精炼 chunk 列表；单条异常保留原文，其余继续。"""
        refined: list[Chunk] = []
        llm_refined = 0
        fallbacks = 0
        for chunk in chunks:
            try:
                text, refined_by, fallback_reason = self._refine_one(chunk.text, trace)
            except Exception as exc:
                self._logger.warning("chunk %s refine failed, keep original: %s", chunk.id, exc)
                refined.append(chunk)
                fallbacks += 1
                continue
            metadata = dict(chunk.metadata)
            metadata["refined_by"] = refined_by
            if fallback_reason:
                metadata["refine_fallback"] = fallback_reason
                fallbacks += 1
            refined.append(
                Chunk(
                    id=chunk.id,
                    text=text,
                    metadata=metadata,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    source_ref=chunk.source_ref,
                )
            )
            if refined_by == "llm":
                llm_refined += 1
        if trace is not None:
            trace.record_stage(
                "transform",
                method=self.name,
                details={
                    "chunks_in": len(chunks),
                    "chunks_out": len(refined),
                    "llm_refined": llm_refined,
                    "fallback": fallbacks,
                    "use_llm": self._use_llm,
                },
            )
        return refined

    # ------------------------------------------------------------------
    # 精炼流程
    # ------------------------------------------------------------------

    def _refine_one(
        self, text: str, trace: TraceContext | None
    ) -> tuple[str, str, str | None]:
        """单条 chunk 精炼：先规则，后可选 LLM。

        Returns:
            ``(精炼文本, refined_by, 降级原因或 None)``。
        """
        refined = self._rule_based_refine(text)
        refined_by = "rule"
        fallback_reason: str | None = None
        if self._use_llm:
            if self._llm is None:
                fallback_reason = self._llm_unavailable_reason or "llm_unavailable"
            else:
                try:
                    llm_text = self._llm_refine(text, trace)
                except Exception as exc:
                    llm_text = None
                    fallback_reason = f"llm_error: {exc}"
                    self._logger.warning("llm refine failed, fallback to rule: %s", exc)
                else:
                    if llm_text is None or not llm_text.strip():
                        fallback_reason = "llm_empty_response"
                if llm_text is not None and llm_text.strip():
                    refined = llm_text
                    refined_by = "llm"
                    fallback_reason = None
        return refined, refined_by, fallback_reason

    # ------------------------------------------------------------------
    # 规则去噪
    # ------------------------------------------------------------------

    def _rule_based_refine(self, text: str) -> str:
        """规则去噪：代码块逐字保留，其余分段清洗。"""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        parts: list[tuple[bool, str]] = []
        pos = 0
        for match in _CODE_FENCE_RE.finditer(text):
            if match.start() > pos:
                parts.append((False, text[pos:match.start()]))
            parts.append((True, match.group(0)))
            pos = match.end()
        if pos < len(text):
            parts.append((False, text[pos:]))
        cleaned = "".join(
            segment if is_code else self._clean_text_segment(segment)
            for is_code, segment in parts
        )
        return cleaned.strip()

    def _clean_text_segment(self, segment: str) -> str:
        # 去控制字符（如 PDF 提取出的 \x0c / \x00），保留 \n 与 \t。
        segment = _CONTROL_CHAR_RE.sub("", segment)
        # 去 HTML 注释。
        segment = _HTML_COMMENT_RE.sub("", segment)
        kept: list[str] = []
        for line in segment.split("\n"):
            stripped = line.strip()
            if self._is_page_noise(stripped):
                continue
            # 行内多余空格折叠 + 去行尾空白（仅在非代码段执行）。
            kept.append(_INLINE_SPACES_RE.sub(" ", line).rstrip())
        # 3+ 连续空行折叠为一个空行。
        return _BLANK_LINES_RE.sub("\n\n", "\n".join(kept))

    @staticmethod
    def _is_page_noise(stripped_line: str) -> bool:
        """整行命中页眉页脚/页码/分隔线模式时视为噪声。"""
        if not stripped_line:
            return False  # 空行保留，由 _BLANK_LINES_RE 统一折叠。
        return any(pattern.fullmatch(stripped_line) for pattern in _PAGE_NOISE_RES)

    # ------------------------------------------------------------------
    # LLM 重写
    # ------------------------------------------------------------------

    def _llm_refine(self, text: str, trace: TraceContext | None = None) -> str | None:
        """调用 LLM 重写（异常向上抛出，由 ``_refine_one`` 统一降级）。"""
        prompt = self._prompt_template.replace("{text}", text)
        return self._llm.chat_text(prompt, trace=trace)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Prompt 加载
    # ------------------------------------------------------------------

    def _load_prompt(self, prompt_path: str | None) -> str:
        """加载 prompt 模板：去 ``#`` 注释行；文件缺失用内置 fallback。

        Raises:
            ValueError: 模板缺少 ``{text}`` 占位符时。
        """
        raw: str
        path = prompt_path or "config/prompts/chunk_refinement.txt"
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            self._logger.warning("prompt file not found: %s, using built-in default", path)
            raw = DEFAULT_PROMPT_TEMPLATE
        # 去掉以 # 开头的注释行（文档说明，不应进入 prompt）。
        lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("#")]
        template = "\n".join(lines).strip()
        if "{text}" not in template:
            raise ValueError(f"prompt template must contain the {{text}} placeholder: {path}")
        return template
