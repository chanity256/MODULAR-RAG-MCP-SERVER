"""``ingestion.transform.ChunkRefiner`` 单元测试（阶段 C5）。

- 规则模式：fixtures（tests/fixtures/noisy_chunks.json，8 场景）断言清洗效果；
  代码块逐字保留、幂等性、干净文本不过度清理。
- LLM 模式：Mock LLM 验证调用、prompt 注入与 ``refined_by: "llm"``。
- 降级：LLM 异常/空响应/工厂失败回退规则结果并标记原因，不阻塞。
- 契约：单 chunk 异常保留原文；chunk id/offset/source_ref 不变；trace 打点。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.transform.base_transform import BaseTransform
from ingestion.transform.chunk_refiner import ChunkRefiner
from libs.llm import BaseLLM, ChatResponse

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "noisy_chunks.json"

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


def _chunk(text: str, index: int = 0) -> Chunk:
    return Chunk(
        id=f"doc_{index:04d}_ab12cd34",
        text=text,
        metadata={"source_path": "a.pdf", "chunk_index": index},
        start_offset=index * 100,
        end_offset=index * 100 + len(text),
        source_ref="doc" + "0" * 61,
    )


class _MockLLM(BaseLLM):
    """可注入响应/异常的 Mock LLM。"""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        self.responses = list(responses or [])
        self.error = error
        self.prompts: list[str] = []

    def chat(self, messages, *, trace=None, **kwargs):
        self.prompts.append(messages[-1].content)
        if self.error is not None:
            raise self.error
        content = self.responses.pop(0) if self.responses else "mock refined text"
        return ChatResponse(content=content, model="mock")


def _scenarios() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["scenarios"]


# ---------------------------------------------------------------------------
# 规则模式（fixtures 驱动）
# ---------------------------------------------------------------------------


def test_noisy_fixtures_load_eight_scenarios():
    names = {s["name"] for s in _scenarios()}
    assert names == {
        "typical_noise_scenario",
        "ocr_errors",
        "page_header_footer",
        "excessive_whitespace",
        "format_markers",
        "clean_text",
        "code_blocks",
        "mixed_noise",
    }


@pytest.mark.parametrize("scenario", _scenarios(), ids=lambda s: s["name"])
def test_rule_based_scenario(tmp_path, scenario):
    """对 8 个噪声场景断言 contains / not_contains / expect_equal。"""
    refiner = ChunkRefiner(_settings(tmp_path))
    output = refiner._rule_based_refine(scenario["input"])
    for needle in scenario.get("contains", []):
        assert needle in output, f"[{scenario['name']}] expected to contain {needle!r}"
    for needle in scenario.get("not_contains", []):
        assert needle not in output, f"[{scenario['name']}] expected NOT to contain {needle!r}"
    if "expect_equal" in scenario:
        assert output == scenario["expect_equal"]


def test_rule_based_idempotent(tmp_path):
    refiner = ChunkRefiner(_settings(tmp_path))
    for scenario in _scenarios():
        once = refiner._rule_based_refine(scenario["input"])
        twice = refiner._rule_based_refine(once)
        assert once == twice, f"[{scenario['name']}] not idempotent"


def test_rule_based_outputs_have_no_excess_blank_lines(tmp_path):
    refiner = ChunkRefiner(_settings(tmp_path))
    for scenario in _scenarios():
        assert "\n\n\n" not in refiner._rule_based_refine(scenario["input"])


def test_base_transform_is_abstract():
    with pytest.raises(TypeError):
        BaseTransform()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# LLM 模式（Mock）
# ---------------------------------------------------------------------------


def test_llm_mode_refines_with_mock_llm(tmp_path):
    settings = _settings(tmp_path, "\ningestion:\n  chunk_refiner:\n    use_llm: true\n")
    llm = _MockLLM(responses=["LLM 重写后的干净文本。"])
    refiner = ChunkRefiner(settings, llm=llm)
    out = refiner.transform([_chunk("原始 噪声  文本\n\n\n\n尾部")])
    assert out[0].text == "LLM 重写后的干净文本。"
    assert out[0].metadata["refined_by"] == "llm"
    assert "refine_fallback" not in out[0].metadata
    assert len(llm.prompts) == 1


def test_llm_prompt_contains_chunk_text_and_strips_comments(tmp_path):
    settings = _settings(tmp_path, "\ningestion:\n  chunk_refiner:\n    use_llm: true\n")
    llm = _MockLLM(responses=["ok"])
    ChunkRefiner(settings, llm=llm).transform([_chunk("带占位符正文 [IMAGE: h_0_0]")])
    prompt = llm.prompts[0]
    assert "带占位符正文 [IMAGE: h_0_0]" in prompt
    # 默认模板来自 config/prompts/chunk_refinement.txt，# 注释行不应进入 prompt。
    assert not any(line.lstrip().startswith("#") for line in prompt.splitlines())
    assert "文档清洗与重组助手" in prompt


def test_llm_mode_custom_prompt_path(tmp_path):
    prompt_file = tmp_path / "custom_prompt.txt"
    prompt_file.write_text("# 说明\nREWRITE: {text}\n", encoding="utf-8")
    settings = _settings(tmp_path, "\ningestion:\n  chunk_refiner:\n    use_llm: true\n")
    llm = _MockLLM(responses=["out"])
    ChunkRefiner(settings, llm=llm, prompt_path=str(prompt_file)).transform([_chunk("原文内容")])
    assert llm.prompts == ["REWRITE: 原文内容"]


def test_llm_failure_falls_back_to_rule(tmp_path):
    settings = _settings(tmp_path, "\ningestion:\n  chunk_refiner:\n    use_llm: true\n")
    llm = _MockLLM(error=RuntimeError("provider 429"))
    refiner = ChunkRefiner(settings, llm=llm)
    out = refiner.transform([_chunk("正文  压缩空格。")])
    assert out[0].metadata["refined_by"] == "rule"
    assert "llm_error" in out[0].metadata["refine_fallback"]
    assert "429" in out[0].metadata["refine_fallback"]
    assert out[0].text == "正文 压缩空格。"


def test_llm_empty_response_falls_back(tmp_path):
    settings = _settings(tmp_path, "\ningestion:\n  chunk_refiner:\n    use_llm: true\n")
    llm = _MockLLM(responses=[""])
    out = ChunkRefiner(settings, llm=llm).transform([_chunk("正文。")])
    assert out[0].metadata["refined_by"] == "rule"
    assert out[0].metadata["refine_fallback"] == "llm_empty_response"


def test_llm_whitespace_response_falls_back(tmp_path):
    settings = _settings(tmp_path, "\ningestion:\n  chunk_refiner:\n    use_llm: true\n")
    llm = _MockLLM(responses=["   \n\n  "])
    out = ChunkRefiner(settings, llm=llm).transform([_chunk("正文。")])
    assert out[0].metadata["refined_by"] == "rule"
    assert out[0].metadata["refine_fallback"] == "llm_empty_response"


def test_use_llm_false_never_calls_llm(tmp_path):
    llm = _MockLLM()
    refiner = ChunkRefiner(_settings(tmp_path), llm=llm)  # 默认 use_llm=false
    out = refiner.transform([_chunk("正文  清洗。")])
    assert llm.prompts == []
    assert out[0].metadata["refined_by"] == "rule"


def test_use_llm_config_switch_parses_from_yaml(tmp_path):
    assert _settings(tmp_path).ingestion.chunk_refiner.use_llm is False
    on = _settings(tmp_path, "\ningestion:\n  chunk_refiner:\n    use_llm: true\n")
    assert on.ingestion.chunk_refiner.use_llm is True


def test_llm_factory_failure_degrades_to_rule(tmp_path, monkeypatch):
    """LLMFactory.create 抛错：构造不失败，transform 降级规则模式。"""
    from ingestion.transform import chunk_refiner as cr_module

    def _boom(settings, **kwargs):
        raise RuntimeError("no provider registered")

    monkeypatch.setattr(cr_module.LLMFactory, "create", staticmethod(_boom))
    settings = _settings(tmp_path, "\ningestion:\n  chunk_refiner:\n    use_llm: true\n")
    refiner = ChunkRefiner(settings)
    assert refiner.use_llm is True
    out = refiner.transform([_chunk("正文  清洗。")])
    assert out[0].metadata["refined_by"] == "rule"
    assert "llm_init_failed" in out[0].metadata["refine_fallback"]


# ---------------------------------------------------------------------------
# 契约与健壮性
# ---------------------------------------------------------------------------


def test_single_chunk_exception_keeps_original(tmp_path, monkeypatch):
    """单 chunk 精炼异常：保留原文，其余 chunk 正常处理。"""
    refiner = ChunkRefiner(_settings(tmp_path))
    original = cr_refine = ChunkRefiner._refine_one

    def flaky(self, text, trace):
        if text == "坏 chunk":
            raise RuntimeError("unexpected")
        return original(self, text, trace)

    monkeypatch.setattr(ChunkRefiner, "_refine_one", flaky)
    chunks = [_chunk("正常  文本 A", 0), _chunk("坏 chunk", 1), _chunk("正常  文本 B", 2)]
    out = refiner.transform(chunks)
    assert len(out) == 3
    assert out[1].text == "坏 chunk"  # 原文保留
    assert "refined_by" not in out[1].metadata  # 未处理，不加标记
    assert out[0].text == "正常 文本 A"
    assert out[2].metadata["refined_by"] == "rule"


def test_transform_preserves_chunk_contract(tmp_path):
    refiner = ChunkRefiner(_settings(tmp_path))
    chunk = _chunk("正文内容。", index=3)
    out = refiner.transform([chunk])[0]
    assert out.id == chunk.id
    assert out.source_ref == chunk.source_ref
    assert out.start_offset == chunk.start_offset
    assert out.end_offset == chunk.end_offset
    assert out.metadata["source_path"] == "a.pdf"
    assert out.metadata["chunk_index"] == 3
    assert out.metadata["refined_by"] == "rule"
    assert "refine_fallback" not in out.metadata
    # 原 chunk 不被修改（transform 返回新对象）。
    assert chunk.metadata.get("refined_by") is None


def test_transform_records_trace_stage(tmp_path):
    refiner = ChunkRefiner(_settings(tmp_path))
    trace = TraceContext()
    refiner.transform([_chunk("甲", 0), _chunk("乙  丙", 1)], trace=trace)
    record = trace.get_stage("transform")
    assert record is not None
    assert record["method"] == "chunk_refiner"
    assert record["details"]["chunks_in"] == 2
    assert record["details"]["chunks_out"] == 2
    assert record["details"]["fallback"] == 0


def test_prompt_missing_placeholder_raises(tmp_path):
    bad = tmp_path / "bad_prompt.txt"
    bad.write_text("没有占位符的模板\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc_info:
        ChunkRefiner(_settings(tmp_path), prompt_path=str(bad))
    assert "{text}" in str(exc_info.value)


def test_prompt_file_missing_uses_builtin_default(tmp_path):
    refiner = ChunkRefiner(_settings(tmp_path), prompt_path=str(tmp_path / "nope.txt"))
    assert "{text}" in refiner._prompt_template
    assert "文档清洗与重组助手" in refiner._prompt_template


def test_transform_empty_input_returns_empty(tmp_path):
    assert ChunkRefiner(_settings(tmp_path)).transform([]) == []


def test_settings_yaml_repo_default_use_llm_false():
    """仓库默认配置保持 use_llm=false（单元测试不产生真实 API 调用）。"""
    settings = load_settings("config/settings.yaml")
    assert settings.ingestion.chunk_refiner.use_llm is False
