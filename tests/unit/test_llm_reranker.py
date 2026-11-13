"""LLM Reranker 单元测试（阶段 B7.7，mock LLM）。

通过注入 mock LLM 验证：
- backend=llm 时 RerankerFactory 可创建。
- 正常重排：LLM 返回 JSON 数组，候选按新顺序返回，score 按排名递减。
- 解析鲁棒性：LLM 返回带额外文本的响应仍可提取 JSON 数组。
- 失败回退：LLM 异常 / 空响应 / 无 JSON 数组 / 全部越界 → 回退原序 + last_fallback=True。
- 候选截断：超过 top_m 的候选被截取。
- 未覆盖候选追加到末尾。
- prompt 模板可注入（不依赖文件）。
- 空候选返回空列表。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.llm.base_llm import BaseLLM, ChatResponse
from libs.reranker.base_reranker import RerankCandidate
from libs.reranker.llm_reranker import LLMReranker, RerankerParseError, register_llm_provider
from libs.reranker.reranker_factory import RerankerFactory

_VALID_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
    vision_llm:
      provider: azure
      model: gpt-4o
    vector_store:
      backend: chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)

_PROMPT_TEMPLATE = "Query: {query}\nCandidates:\n{candidates}\nOutput ranked indices as JSON array."


def _settings(tmp_path: Path, **rerank_overrides):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if rerank_overrides:
        settings = replace(settings, rerank=replace(settings.rerank, **rerank_overrides))
    return settings


class _MockLLM(BaseLLM):
    """可注入的 mock LLM，返回预设响应。"""

    def __init__(self, response: str = "[0, 1, 2]", *, raise_exc: Exception | None = None):
        self._response = response
        self._raise = raise_exc
        self.last_prompt: str | None = None

    def chat(self, messages, *, trace=None, **kwargs):
        # chat_text 会调用 chat，提取最后一条 user 消息内容
        if self._raise is not None:
            raise self._raise
        self.last_prompt = messages[-1].content if messages else ""
        return ChatResponse(content=self._response, model="mock")


@pytest.fixture
def registered():
    import libs.reranker  # noqa: F401
    yield RerankerFactory


# ---------------------------------------------------------------------------
# 工厂创建
# ---------------------------------------------------------------------------


def test_factory_creates_llm_reranker(tmp_path, registered):
    settings = _settings(tmp_path, backend="llm")
    reranker = registered.create(settings, llm=_MockLLM(), prompt_template=_PROMPT_TEMPLATE)
    assert isinstance(reranker, LLMReranker)


def test_factory_llm_requires_llm_kwarg(tmp_path, registered):
    settings = _settings(tmp_path, backend="llm")
    with pytest.raises(ValueError, match="llm"):
        registered.create(settings, prompt_template=_PROMPT_TEMPLATE)


def test_llm_registered_in_factory(registered):
    assert "llm" in RerankerFactory.available_backends()


# ---------------------------------------------------------------------------
# 正常重排
# ---------------------------------------------------------------------------


def test_basic_rerank_reorders_candidates():
    cfg = replace(load_settings.__self__ if False else None) if False else None  # unused
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [
        RerankCandidate(id="a", text="apple", score=0.9),
        RerankCandidate(id="b", text="banana", score=0.8),
        RerankCandidate(id="c", text="cherry", score=0.7),
    ]
    mock = _MockLLM("[2, 0, 1]")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("fruit", candidates)
    assert len(result) == 3
    assert result[0].id == "c"  # index 2
    assert result[1].id == "a"  # index 0
    assert result[2].id == "b"  # index 1


def test_rerank_scores_decreasing_by_rank():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [
        RerankCandidate(id="a", text="a", score=0.5),
        RerankCandidate(id="b", text="b", score=0.4),
        RerankCandidate(id="c", text="c", score=0.3),
    ]
    mock = _MockLLM("[1, 2, 0]")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    scores = [r.score for r in result]
    # score 应递减
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[-1]


def test_rerank_preserves_text_and_metadata():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [
        RerankCandidate(id="x", text="hello world", score=0.5, metadata={"page": 1}),
    ]
    mock = _MockLLM("[0]")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    assert result[0].text == "hello world"
    assert result[0].metadata == {"page": 1}


# ---------------------------------------------------------------------------
# 解析鲁棒性
# ---------------------------------------------------------------------------


def test_parse_extracts_json_array_with_extra_text():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [RerankCandidate(id=str(i), text=f"text_{i}") for i in range(3)]
    mock = _MockLLM("Sure, here is the ranking:\n[2, 0, 1]\nHope this helps!")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    assert [r.id for r in result] == ["2", "0", "1"]


def test_parse_handles_whitespace_in_array():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [RerankCandidate(id=str(i), text=f"t{i}") for i in range(3)]
    mock = _MockLLM("[ 2 ,  0 , 1 ]")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    assert [r.id for r in result] == ["2", "0", "1"]


def test_partial_coverage_appends_remaining():
    """LLM 只返回部分索引时，未覆盖候选追加到末尾（保持原序）。"""
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [RerankCandidate(id=str(i), text=f"t{i}") for i in range(4)]
    mock = _MockLLM("[3, 1]")  # 只排了 2 个
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    assert len(result) == 4
    assert result[0].id == "3"
    assert result[1].id == "1"
    # 剩余 0, 2 保持原序
    assert result[2].id == "0"
    assert result[3].id == "2"


# ---------------------------------------------------------------------------
# 失败回退
# ---------------------------------------------------------------------------


def test_llm_exception_falls_back_to_original_order():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [RerankCandidate(id=str(i), text=f"t{i}") for i in range(3)]
    mock = _MockLLM(raise_exc=RuntimeError("LLM timeout"))
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    assert [r.id for r in result] == ["0", "1", "2"]  # 原序
    assert reranker.last_fallback is True


def test_no_json_array_falls_back():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [RerankCandidate(id="a", text="a"), RerankCandidate(id="b", text="b")]
    mock = _MockLLM("I think both are relevant.")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    assert [r.id for r in result] == ["a", "b"]  # 原序
    assert reranker.last_fallback is True


def test_all_indices_out_of_range_falls_back():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [RerankCandidate(id="a", text="a"), RerankCandidate(id="b", text="b")]
    mock = _MockLLM("[99, 100]")  # 全部越界
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    assert len(result) == 2
    assert reranker.last_fallback is True


def test_duplicate_indices_ignored():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [RerankCandidate(id=str(i), text=f"t{i}") for i in range(3)]
    mock = _MockLLM("[0, 0, 1]")  # 重复 0
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    assert result[0].id == "0"
    assert result[1].id == "1"
    assert result[2].id == "2"  # 剩余


# ---------------------------------------------------------------------------
# top_m 截断
# ---------------------------------------------------------------------------


def test_top_m_truncates_candidates():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm", top_m=2)
    candidates = [RerankCandidate(id=str(i), text=f"t{i}") for i in range(5)]
    mock = _MockLLM("[1, 0]")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", candidates)
    assert len(result) == 2  # 只处理 top_m=2 个
    assert result[0].id == "1"
    assert result[1].id == "0"


# ---------------------------------------------------------------------------
# prompt 构建
# ---------------------------------------------------------------------------


def test_prompt_contains_query_and_candidates():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    candidates = [RerankCandidate(id="a", text="apple"), RerankCandidate(id="b", text="banana")]
    mock = _MockLLM("[0, 1]")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    reranker.rerank("fruit query", candidates)
    assert "fruit query" in mock.last_prompt
    assert "apple" in mock.last_prompt
    assert "banana" in mock.last_prompt
    assert "[0]" in mock.last_prompt
    assert "[1]" in mock.last_prompt


def test_prompt_loaded_from_file(tmp_path):
    """prompt 从文件读取（使用真实 rerank.txt 或临时文件）。"""
    from core.settings import RerankConfig

    prompt_file = tmp_path / "rerank.txt"
    prompt_file.write_text("Q: {query}\nC: {candidates}\nOut: [indices]", encoding="utf-8")
    rc = RerankConfig(backend="llm")
    mock = _MockLLM("[0]")
    reranker = LLMReranker(rc, mock, prompt_path=str(prompt_file))
    result = reranker.rerank("q", [RerankCandidate(id="a", text="a")])
    assert result[0].id == "a"


def test_missing_prompt_file_raises():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    with pytest.raises(FileNotFoundError):
        LLMReranker(rc, _MockLLM(), prompt_path="/nonexistent/rerank.txt")


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_empty():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    mock = _MockLLM("[0]")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", [])
    assert result == []


def test_single_candidate():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    mock = _MockLLM("[0]")
    reranker = LLMReranker(rc, mock, prompt_template=_PROMPT_TEMPLATE)
    result = reranker.rerank("query", [RerankCandidate(id="only", text="text")])
    assert len(result) == 1
    assert result[0].id == "only"


def test_non_list_candidates_raises():
    from core.settings import RerankConfig

    rc = RerankConfig(backend="llm")
    reranker = LLMReranker(rc, _MockLLM(), prompt_template=_PROMPT_TEMPLATE)
    with pytest.raises(ValueError):
        reranker.rerank("query", "not a list")  # type: ignore[arg-type]
