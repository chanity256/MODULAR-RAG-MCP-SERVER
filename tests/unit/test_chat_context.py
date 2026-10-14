"""WC6.2: 相关性过滤（fail-open）与查询改写（失败用原句），注入 fake LLM。"""

from __future__ import annotations

import pytest

from core.chat.context import ContextProcessor
from core.chat.prompts import PROMPT_RELEVANCE, PROMPT_REWRITE, load_prompt
from libs.llm.base_llm import BaseLLM, ChatMessage, ChatResponse


class ScriptedLLM(BaseLLM):
    def __init__(self, reply: str | Exception = ""):
        self.reply = reply
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    def chat(self, messages, *, trace=None, **kwargs):
        self.prompts.append(messages[-1].content)
        self.kwargs.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return ChatResponse(content=self.reply)


def _history() -> list[ChatMessage]:
    return [
        ChatMessage("user", "什么是 BM25？"),
        ChatMessage("assistant", "BM25 是稀疏检索算法。"),
        ChatMessage("user", "今天天气如何"),
        ChatMessage("assistant", "我不知道天气。"),
    ]


def test_prompt_files_exist_and_have_placeholders():
    relevance = load_prompt(PROMPT_RELEVANCE)
    rewrite = load_prompt(PROMPT_REWRITE)
    for tpl in (relevance, rewrite):
        assert "{history}" in tpl and "{question}" in tpl


# ---- filter ----------------------------------------------------------------

def test_filter_keeps_selected_turns_only():
    llm = ScriptedLLM("[1]")
    out = ContextProcessor(llm).filter_history(_history(), "它的公式是什么？")
    assert [m.content for m in out.history] == ["什么是 BM25？", "BM25 是稀疏检索算法。"]
    assert out.dropped_turns == 1 and out.called and not out.failed
    assert "[1]" in llm.prompts[0] and "[2]" in llm.prompts[0]
    assert "它的公式是什么？" in llm.prompts[0]
    assert llm.kwargs[0] == {"temperature": 0.0}


def test_filter_empty_array_drops_everything():
    out = ContextProcessor(ScriptedLLM("[]")).filter_history(_history(), "新话题")
    assert out.history == [] and out.dropped_turns == 2 and not out.failed


def test_filter_tolerates_surrounding_text():
    out = ContextProcessor(ScriptedLLM("Relevant turns: [2]. Done.")).filter_history(_history(), "q")
    assert [m.content for m in out.history][0] == "今天天气如何"
    assert out.dropped_turns == 1


@pytest.mark.parametrize("reply", ["no idea", "", "[abc]", "{\"a\": 1}", "[1, \"x\"]", "[99]", "[true]"])
def test_filter_unparseable_output_keeps_history(reply):
    out = ContextProcessor(ScriptedLLM(reply)).filter_history(_history(), "q")
    assert len(out.history) == 4
    assert out.failed and out.dropped_turns == 0


def test_filter_llm_exception_keeps_history():
    out = ContextProcessor(ScriptedLLM(RuntimeError("boom"))).filter_history(_history(), "q")
    assert len(out.history) == 4 and out.failed and out.called


def test_filter_empty_history_makes_no_llm_call():
    llm = ScriptedLLM("[1]")
    out = ContextProcessor(llm).filter_history([], "q")
    assert out.history == [] and not out.called and llm.prompts == []


def test_filter_braces_in_history_are_not_treated_as_placeholders():
    llm = ScriptedLLM("[1]")
    hist = [ChatMessage("user", "解释 {question} 占位符"), ChatMessage("assistant", "{history}")]
    out = ContextProcessor(llm).filter_history(hist, "真正的问题")
    assert len(out.history) == 2
    assert "真正的问题" in llm.prompts[0]


# ---- rewrite ---------------------------------------------------------------

def test_rewrite_uses_llm_output():
    llm = ScriptedLLM("  BM25 的评分公式是什么  \n")
    out = ContextProcessor(llm).rewrite_query("它的公式是什么？", _history()[:2])
    assert out.query == "BM25 的评分公式是什么" and out.rewritten and out.called and not out.failed
    assert "它的公式是什么？" in llm.prompts[0] and "BM25 是稀疏检索算法" in llm.prompts[0]


def test_rewrite_strips_quotes_and_takes_first_line():
    out = ContextProcessor(ScriptedLLM("\"改写后的查询\"\n解释：...")).rewrite_query("q", _history())
    assert out.query == "改写后的查询"


def test_rewrite_unchanged_is_not_flagged_rewritten():
    out = ContextProcessor(ScriptedLLM("独立问题")).rewrite_query("独立问题", _history())
    assert out.query == "独立问题" and not out.rewritten


def test_rewrite_exception_falls_back_to_original():
    out = ContextProcessor(ScriptedLLM(RuntimeError("down"))).rewrite_query("原句", _history())
    assert out.query == "原句" and out.failed and not out.rewritten


@pytest.mark.parametrize("reply", ["", "   \n  "])
def test_rewrite_blank_output_falls_back(reply):
    out = ContextProcessor(ScriptedLLM(reply)).rewrite_query("原句", _history())
    assert out.query == "原句" and out.failed


def test_rewrite_runaway_output_falls_back():
    out = ContextProcessor(ScriptedLLM("答" * 2000)).rewrite_query("原句", _history())
    assert out.query == "原句" and out.failed


def test_rewrite_without_history_skips_llm():
    llm = ScriptedLLM("should not be used")
    out = ContextProcessor(llm).rewrite_query("原句", [])
    assert out.query == "原句" and not out.called and llm.prompts == []


def test_custom_templates_and_kwargs():
    llm = ScriptedLLM("[1]")
    proc = ContextProcessor(llm, relevance_template="H={history}|Q={question}", llm_kwargs={"max_tokens": 16})
    proc.filter_history(_history()[:2], "qq")
    assert llm.prompts[0].startswith("H=[1] User:") and llm.prompts[0].endswith("|Q=qq")
    assert llm.kwargs[0] == {"max_tokens": 16}
