"""``AnswerGenerator``（WC5.4）：编号与 CitationGenerator 同序、空检索短路、prompt 组装。"""

from __future__ import annotations

from typing import Any, Iterator, Sequence

import pytest

from core.chat import AnswerGenerator
from core.chat.answer_generator import NOT_FOUND_MESSAGE
from core.chat.prompts import PROMPTS_DIR, load_prompt
from core.response.citation_generator import CitationGenerator
from core.response.response_builder import EMPTY_RESULT_MESSAGE
from core.types import RetrievalResult
from libs.llm.base_llm import BaseLLM, ChatMessage, ChatResponse
from libs.llm.openai_llm import LLMRequestError


class _StreamLLM(BaseLLM):
    def __init__(self, chunks: Sequence[str] = ("Ans", "wer [1]"), fail_after: int | None = None):
        self.chunks = list(chunks)
        self.fail_after = fail_after
        self.stream_calls: list[dict[str, Any]] = []

    def chat(self, messages, *, trace=None, **kwargs) -> ChatResponse:  # pragma: no cover - 不应被调用
        raise AssertionError("chat() must not be called")

    def chat_stream(self, messages, *, trace=None, **kwargs) -> Iterator[str]:
        self.stream_calls.append({"messages": list(messages), "trace": trace, "kwargs": kwargs})
        for i, c in enumerate(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise LLMRequestError("fake", "cut", status=503)
            yield c


def _results(n: int = 3) -> list[RetrievalResult]:
    out = []
    for i in range(1, n + 1):
        meta: dict[str, Any] = {"source_path": f"docs/file{i}.pdf"}
        if i % 2:
            meta["page"] = i
        out.append(RetrievalResult(chunk_id=f"c{i}", score=1.0 / i, text=f"text of chunk {i}", metadata=meta))
    return out


def test_context_numbers_match_citation_ids_and_order():
    results = _results(3)
    gen = AnswerGenerator(_StreamLLM())
    context = gen.build_context(results)
    citations = CitationGenerator().generate(results)
    positions = [context.index(f"[{c.id}] {c.source}") for c in citations]
    assert positions == sorted(positions)
    assert [c.id for c in gen.citations(results)] == [1, 2, 3]
    assert "[1] docs/file1.pdf, p.1\ntext of chunk 1" in context
    assert "[2] docs/file2.pdf\ntext of chunk 2" in context  # 无页码不输出 p.


def test_max_context_chunks_truncates_context_and_citations_consistently():
    results = _results(5)
    gen = AnswerGenerator(_StreamLLM(), max_context_chunks=2)
    assert [r.chunk_id for r in gen.select(results)] == ["c1", "c2"]
    ctx = gen.build_context(results)
    assert "[2]" in ctx and "[3]" not in ctx and "text of chunk 3" not in ctx
    assert [c.chunk_id for c in gen.citations(results)] == ["c1", "c2"]


def test_invalid_max_context_chunks():
    for bad in (0, -1, True, "8", None):
        with pytest.raises(ValueError):
            AnswerGenerator(_StreamLLM(), max_context_chunks=bad)  # type: ignore[arg-type]


def test_stream_builds_messages_with_system_history_and_question():
    llm = _StreamLLM()
    gen = AnswerGenerator(llm)
    history = [ChatMessage("user", "earlier q"), ChatMessage("assistant", "earlier a")]
    out = list(gen.stream("what is X?", _results(2), history=history, trace="T", temperature=0.1))
    assert out == ["Ans", "wer [1]"]
    call = llm.stream_calls[0]
    msgs = call["messages"]
    assert [m.role for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[-1].content == "what is X?"
    assert "[1] docs/file1.pdf, p.1" in msgs[0].content
    assert "{context}" not in msgs[0].content
    assert call["trace"] == "T"
    assert call["kwargs"] == {"temperature": 0.1}


def test_system_prompt_requires_bracket_citations():
    system = AnswerGenerator(_StreamLLM()).build_messages("q", _results(1))[0].content
    assert "[1]" in system and "square brackets" in system


def test_empty_retrieval_short_circuits_without_calling_llm():
    llm = _StreamLLM()
    out = list(AnswerGenerator(llm).stream("anything", []))
    assert out == [NOT_FOUND_MESSAGE]
    assert NOT_FOUND_MESSAGE == EMPTY_RESULT_MESSAGE
    assert llm.stream_calls == []


def test_braces_in_chunk_text_and_template_are_not_interpreted():
    llm = _StreamLLM()
    res = [RetrievalResult(chunk_id="c1", score=1.0, text="json {\"a\": {context}} {0}", metadata={})]
    gen = AnswerGenerator(llm, prompt_template="Sys {x}\n{context}")
    list(gen.stream("q", res))
    system = llm.stream_calls[0]["messages"][0].content
    assert system.startswith("Sys {x}\n[1] unknown\n")
    assert "{context}}" in system


def test_llm_error_midstream_propagates_after_partial_output():
    gen = AnswerGenerator(_StreamLLM(fail_after=1))
    it = gen.stream("q", _results(1))
    assert next(it) == "Ans"
    with pytest.raises(LLMRequestError) as ei:
        next(it)
    assert ei.value.status == 503


def test_rejects_blank_question_and_non_list_results():
    gen = AnswerGenerator(_StreamLLM())
    with pytest.raises(ValueError):
        list(gen.stream("  ", _results(1)))
    with pytest.raises(TypeError):
        list(gen.stream("q", "not a list"))  # type: ignore[arg-type]


def test_stream_is_lazy():
    llm = _StreamLLM()
    it = AnswerGenerator(llm).stream("q", _results(1))
    assert llm.stream_calls == []
    next(it)
    assert len(llm.stream_calls) == 1


def test_works_with_default_chat_stream_fallback():
    class _SyncOnly(BaseLLM):
        def chat(self, messages, *, trace=None, **kwargs):
            return ChatResponse(content="whole answer [1]")

    assert list(AnswerGenerator(_SyncOnly()).stream("q", _results(1))) == ["whole answer [1]"]


# -- prompt 文件 -------------------------------------------------------------


@pytest.mark.parametrize("name", ["chat_answer", "chat_relevance", "chat_rewrite"])
def test_prompt_files_exist_and_nonempty(name):
    assert (PROMPTS_DIR / f"{name}.txt").is_file()
    assert load_prompt(name).strip()


def test_prompt_placeholders():
    assert "{context}" in load_prompt("chat_answer")
    for name in ("chat_relevance", "chat_rewrite"):
        text = load_prompt(name)
        assert "{history}" in text and "{question}" in text
        # 模板须可被 str.format 安全渲染（无多余花括号）。
        text.format(history="h", question="q")


def test_load_prompt_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_prompt("nope", prompts_dir=tmp_path)
    (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
    assert load_prompt("x", prompts_dir=tmp_path) == "hello"


def test_default_prompt_loaded_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    AnswerGenerator(_StreamLLM())
