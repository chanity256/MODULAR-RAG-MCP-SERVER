"""有据回答生成器（WC5.4）。

输入检索结果（``RetrievalResult`` 列表），构造带编号的上下文并要求模型以 ``[n]``
标注引用，经 ``BaseLLM.chat_stream`` 流式产出答案。

设计要点：
- 上下文编号直接取自 ``CitationGenerator`` 生成的 ``Citation.id``：编号与引用列表
  同源同序，``[n]`` 必能对应 ``sources`` 帧里的第 n 条引用。
- ``max_context_chunks`` 截断发生在编号之前；调用方应对 :meth:`select` 的返回值
  生成 citations，才能保证与上下文一致。
- 空检索短路：不调 LLM，直接产出固定「未命中」文案（与 MCP 路径同一文案）。
"""

from __future__ import annotations

from typing import Any, Iterator, Sequence

from core.chat.prompts import PROMPT_ANSWER, load_prompt
from core.response.citation_generator import Citation, CitationGenerator
from core.response.response_builder import EMPTY_RESULT_MESSAGE
from core.types import RetrievalResult
from libs.llm.base_llm import BaseLLM, ChatMessage

__all__ = ["AnswerGenerator", "NOT_FOUND_MESSAGE"]

#: 检索为空时的固定回复（与 ``ResponseBuilder`` 的无命中文案一致）。
NOT_FOUND_MESSAGE = EMPTY_RESULT_MESSAGE

_CONTEXT_PLACEHOLDER = "{context}"


class AnswerGenerator:
    """基于检索结果流式生成带 ``[n]`` 引用的回答。

    Args:
        llm: 提供 ``chat_stream`` 的 LLM 后端。
        max_context_chunks: 送入上下文的 chunk 上限（取检索结果前 N 条）。
        prompt_template: 系统 prompt 模板（含 ``{context}`` 占位符）；缺省读取
            ``config/prompts/chat_answer.txt``。
        citation_generator: 引用生成器（可注入，缺省 ``CitationGenerator``）。
    """

    def __init__(
        self,
        llm: BaseLLM,
        *,
        max_context_chunks: int = 8,
        prompt_template: str | None = None,
        citation_generator: CitationGenerator | None = None,
    ) -> None:
        if isinstance(max_context_chunks, bool) or not isinstance(max_context_chunks, int) or max_context_chunks < 1:
            raise ValueError(f"max_context_chunks must be a positive integer, got {max_context_chunks!r}")
        self._llm = llm
        self._max_context_chunks = max_context_chunks
        self._prompt_template = prompt_template if prompt_template is not None else load_prompt(PROMPT_ANSWER)
        self._citation_generator = citation_generator or CitationGenerator()

    def select(self, results: Sequence[RetrievalResult]) -> list[RetrievalResult]:
        """应用 ``max_context_chunks`` 截断，返回实际进入上下文（也应进入引用）的结果。"""
        if not isinstance(results, (list, tuple)):
            raise TypeError(f"results must be a list of RetrievalResult, got {type(results).__name__}")
        return list(results[: self._max_context_chunks])

    def citations(self, results: Sequence[RetrievalResult]) -> list[Citation]:
        """对截断后的结果生成引用，id 与 :meth:`build_context` 的编号一致。"""
        return self._citation_generator.generate(self.select(results))

    def build_context(self, results: Sequence[RetrievalResult]) -> str:
        """渲染带编号上下文：``[id] source, p.N`` 头 + 原文。"""
        blocks: list[str] = []
        for citation in self.citations(results):
            location = f", p.{citation.page}" if citation.page is not None else ""
            blocks.append(f"[{citation.id}] {citation.source}{location}\n{citation.text.strip()}")
        return "\n\n".join(blocks)

    def build_messages(
        self,
        question: str,
        results: Sequence[RetrievalResult],
        *,
        history: Sequence[ChatMessage] = (),
    ) -> list[ChatMessage]:
        """组装 LLM 消息：system(prompt+上下文) + 历史 + 当前问题。"""
        # 用 replace 而非 format：模板或检索原文里的花括号不应被当作占位符。
        system = self._prompt_template.replace(_CONTEXT_PLACEHOLDER, self.build_context(results))
        return [ChatMessage("system", system), *history, ChatMessage("user", question)]

    def stream(
        self,
        question: str,
        results: Sequence[RetrievalResult],
        *,
        history: Sequence[ChatMessage] = (),
        trace: Any | None = None,
        **llm_kwargs: Any,
    ) -> Iterator[str]:
        """流式产出答案增量；检索为空时只产出 :data:`NOT_FOUND_MESSAGE` 且不调用 LLM。

        Raises:
            ValueError: ``question`` 为空。
            TypeError: ``results`` 类型不符。
            LLMRequestError: 流式过程中 LLM 失败（透传，由 ChatEngine 分类）。
        """
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        if not self.select(results):
            yield NOT_FOUND_MESSAGE
            return
        messages = self.build_messages(question, results, history=history)
        yield from self._llm.chat_stream(messages, trace=trace, **llm_kwargs)
