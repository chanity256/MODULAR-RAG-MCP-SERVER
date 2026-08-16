"""``ingestion.transform.MetadataEnricher`` 契约测试（阶段 C6）。

覆盖：
- 规则模式：title/summary/tags 三字段必非空；Markdown 标题优先；确定性。
- LLM 模式：Mock LLM 严格 JSON 解析、代码围栏兼容、``enriched_by: "llm"``。
- 降级：JSON 非法/字段缺失/LLM 异常回退规则结果并标记 ``enrich_fallback``。
- 契约：单 chunk 异常保留原文；id/source_ref/offset 不变；trace 打点。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.transform.metadata_enricher import MetadataEnricher
from libs.llm import BaseLLM, ChatResponse

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


def _settings(tmp_path: Path, ingestion_yaml: str = ""):
    path = Path(tmp_path) / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VALID_YAML + ingestion_yaml, encoding="utf-8")
    return load_settings(path)


def _llm_settings(tmp_path: Path):
    return _settings(tmp_path, "\ningestion:\n  metadata_enricher:\n    use_llm: true\n")


def _chunk(text: str, index: int = 0) -> Chunk:
    return Chunk(
        id=f"doc_{index:04d}_ab12cd34",
        text=text,
        metadata={"source_path": "docs/arch.pdf", "doc_type": "pdf", "chunk_index": index},
        start_offset=index * 10,
        end_offset=index * 10 + len(text),
        source_ref="doc" + "0" * 61,
    )


class _MockLLM(BaseLLM):
    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        self.responses = list(responses or [])
        self.error = error
        self.prompts: list[str] = []

    def chat(self, messages, *, trace=None, **kwargs):
        self.prompts.append(messages[-1].content)
        if self.error is not None:
            raise self.error
        content = self.responses.pop(0) if self.responses else "{}"
        return ChatResponse(content=content, model="mock")


# ---------------------------------------------------------------------------
# 规则模式（兜底）
# ---------------------------------------------------------------------------


def test_rule_mode_fills_nonempty_title_summary_tags(tmp_path):
    out = MetadataEnricher(_settings(tmp_path)).transform([
        _chunk("# 系统架构\n\n系统由三层组成：MCP Server、Core 与 Libs。")
    ])[0]
    assert out.metadata["title"]
    assert out.metadata["summary"]
    assert isinstance(out.metadata["tags"], list) and out.metadata["tags"]
    assert out.metadata["enriched_by"] == "rule"
    assert "enrich_fallback" not in out.metadata


def test_rule_mode_title_prefers_markdown_heading(tmp_path):
    out = MetadataEnricher(_settings(tmp_path)).transform([
        _chunk("引言正文第一行。\n\n## 检索模块设计\n\n内容。")
    ])[0]
    assert out.metadata["title"] == "检索模块设计"


def test_rule_mode_title_falls_back_to_first_line(tmp_path):
    out = MetadataEnricher(_settings(tmp_path)).transform([
        _chunk("没有标题的段落首行。后续内容。")
    ])[0]
    assert out.metadata["title"].startswith("没有标题的段落首行")


def test_rule_mode_summary_contains_first_sentence(tmp_path):
    text = "混合检索结合稠密与稀疏两路召回。RRF 负责融合排序。后续内容较长。"
    out = MetadataEnricher(_settings(tmp_path)).transform([_chunk(text)])[0]
    assert "混合检索结合稠密与稀疏两路召回" in out.metadata["summary"]


def test_rule_mode_summary_truncated_to_limit(tmp_path):
    long_text = "长" * 500
    out = MetadataEnricher(_settings(tmp_path)).transform([_chunk(long_text)])[0]
    assert 0 < len(out.metadata["summary"]) <= 162  # 160 + 省略号


def test_rule_mode_tags_from_latin_keywords(tmp_path):
    text = "The chroma store and the chroma query are fast. Embedding via openai."
    out = MetadataEnricher(_settings(tmp_path)).transform([_chunk(text)])[0]
    tags = out.metadata["tags"]
    assert "chroma" in tags  # 高频词
    assert "the" not in tags  # 停用词过滤
    assert len(tags) <= 5


def test_rule_mode_tags_nonempty_for_cjk_and_empty_text(tmp_path):
    out = MetadataEnricher(_settings(tmp_path)).transform([_chunk("中文内容没有拉丁词。")])[0]
    assert out.metadata["tags"]  # 含 CJK 时补"中文"标签
    empty = MetadataEnricher(_settings(tmp_path)).transform([_chunk("")])[0]
    assert empty.metadata["title"]  # source_path stem 兜底
    assert empty.metadata["summary"]
    assert empty.metadata["tags"] == ["pdf"]  # doc_type 兜底


def test_rule_mode_deterministic(tmp_path):
    enricher = MetadataEnricher(_settings(tmp_path))
    c = _chunk("Chroma 向量库支持 upsert 与 query。Chroma 是默认后端。")
    a = enricher.transform([c])[0]
    b = enricher.transform([c])[0]
    assert a.metadata["title"] == b.metadata["title"]
    assert a.metadata["summary"] == b.metadata["summary"]
    assert a.metadata["tags"] == b.metadata["tags"]


# ---------------------------------------------------------------------------
# LLM 模式（Mock）
# ---------------------------------------------------------------------------


def test_llm_mode_uses_parsed_json(tmp_path):
    llm = _MockLLM(responses=['{"title": "混合检索设计", "summary": "两路召回与融合。", "tags": ["检索", "RRF"]}'])
    out = MetadataEnricher(_llm_settings(tmp_path), llm=llm).transform([_chunk("正文")])[0]
    assert out.metadata["enriched_by"] == "llm"
    assert out.metadata["title"] == "混合检索设计"
    assert out.metadata["summary"] == "两路召回与融合。"
    assert out.metadata["tags"] == ["检索", "RRF"]
    assert "enrich_fallback" not in out.metadata


def test_llm_mode_tolerates_code_fence_wrapping(tmp_path):
    raw = '```json\n{"title": "T", "summary": "S", "tags": ["a"]}\n```'
    llm = _MockLLM(responses=[raw])
    out = MetadataEnricher(_llm_settings(tmp_path), llm=llm).transform([_chunk("正文")])[0]
    assert out.metadata["enriched_by"] == "llm"
    assert out.metadata["title"] == "T"


def test_llm_mode_prompt_contains_chunk_text(tmp_path):
    llm = _MockLLM(responses=['{"title": "T", "summary": "S", "tags": ["a"]}'])
    MetadataEnricher(_llm_settings(tmp_path), llm=llm).transform([_chunk("独占正文标记")])
    assert "独占正文标记" in llm.prompts[0]


def test_llm_invalid_json_falls_back_to_rule(tmp_path):
    llm = _MockLLM(responses=["这不是 JSON 格式的回答。"])
    out = MetadataEnricher(_llm_settings(tmp_path), llm=llm).transform([
        _chunk("# 标题\n正文内容。")
    ])[0]
    assert out.metadata["enriched_by"] == "rule"
    assert out.metadata["enrich_fallback"] == "llm_invalid_json"
    assert out.metadata["title"] == "标题"


def test_llm_missing_fields_fall_back_to_rule(tmp_path):
    for raw in [
        '{"title": "", "summary": "S", "tags": ["a"]}',
        '{"title": "T", "summary": "S", "tags": []}',
        '{"title": "T", "summary": "S"}',
        '{"title": "T", "summary": "S", "tags": ["a", 1]}',
    ]:
        llm = _MockLLM(responses=[raw])
        out = MetadataEnricher(_llm_settings(tmp_path), llm=llm).transform([_chunk("正文")])[0]
        assert out.metadata["enriched_by"] == "rule", raw
        assert out.metadata["enrich_fallback"] == "llm_invalid_json"


def test_llm_exception_falls_back_to_rule(tmp_path):
    llm = _MockLLM(error=RuntimeError("timeout 5000ms"))
    out = MetadataEnricher(_llm_settings(tmp_path), llm=llm).transform([_chunk("正文。")])[0]
    assert out.metadata["enriched_by"] == "rule"
    assert "llm_error" in out.metadata["enrich_fallback"]
    assert "timeout" in out.metadata["enrich_fallback"]


def test_use_llm_false_never_calls_llm(tmp_path):
    llm = _MockLLM()
    MetadataEnricher(_settings(tmp_path), llm=llm).transform([_chunk("正文")])
    assert llm.prompts == []


def test_llm_factory_failure_degrades(tmp_path, monkeypatch):
    from ingestion.transform import metadata_enricher as me_module

    monkeypatch.setattr(
        me_module.LLMFactory, "create", staticmethod(lambda s, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    )
    enricher = MetadataEnricher(_llm_settings(tmp_path))
    out = enricher.transform([_chunk("正文。")])[0]
    assert out.metadata["enriched_by"] == "rule"
    assert "llm_init_failed" in out.metadata["enrich_fallback"]


# ---------------------------------------------------------------------------
# 配置 / 契约 / 健壮性
# ---------------------------------------------------------------------------


def test_use_llm_config_switch_parses(tmp_path):
    assert _settings(tmp_path).ingestion.metadata_enricher.use_llm is False
    assert _llm_settings(tmp_path).ingestion.metadata_enricher.use_llm is True


def test_transform_preserves_chunk_contract(tmp_path):
    chunk = _chunk("# 标题\n正文。", index=2)
    out = MetadataEnricher(_settings(tmp_path)).transform([chunk])[0]
    assert out.id == chunk.id
    assert out.text == chunk.text
    assert out.source_ref == chunk.source_ref
    assert out.start_offset == chunk.start_offset
    assert out.metadata["chunk_index"] == 2
    assert "enriched_by" not in chunk.metadata  # 原 chunk 不被修改


def test_single_chunk_exception_keeps_original(tmp_path, monkeypatch):
    enricher = MetadataEnricher(_settings(tmp_path))
    original = MetadataEnricher._rule_enrich

    def flaky(self, text, metadata):
        if "毒" in text:
            raise RuntimeError("boom")
        return original(self, text, metadata)

    monkeypatch.setattr(MetadataEnricher, "_rule_enrich", flaky)
    out = enricher.transform([_chunk("正常文本。"), _chunk("毒 chunk")])
    assert len(out) == 2
    assert "title" not in out[1].metadata  # 异常 chunk 保留原样
    assert out[0].metadata["title"]


def test_transform_records_trace_stage(tmp_path):
    trace = TraceContext()
    MetadataEnricher(_settings(tmp_path)).transform([_chunk("甲"), _chunk("乙")], trace=trace)
    record = trace.get_stage("transform")
    assert record["method"] == "metadata_enricher"
    assert record["details"]["chunks_in"] == 2
    assert record["details"]["fallback"] == 0


def test_transform_empty_input_returns_empty(tmp_path):
    assert MetadataEnricher(_settings(tmp_path)).transform([]) == []
