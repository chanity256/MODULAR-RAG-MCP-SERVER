"""ChunkRefiner 真实 LLM 集成测试（阶段 C5，验收必选项）。

⚠️ 本文件会产生真实 API 调用与费用。为避免意外计费，默认跳过；
设置环境变量 ``RAG_LLM_INTEGRATION=1`` 且在 ``config/settings.yaml``
配置可用 LLM（provider/model/api_key 或对应环境变量）后启用。

覆盖验收：
- 真实 LLM 调用成功且输出质量可接受（噪声减少、内容保留）。
- 无效模型名称时优雅降级到规则模式，不崩溃。
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.transform.chunk_refiner import ChunkRefiner
from libs.llm import LLMFactory

_INTEGRATION_ENABLED = os.environ.get("RAG_LLM_INTEGRATION") == "1"

_NOISY_TEXT = (
    "第 1 页 / 共 12 页\n\n"
    "# 系统架构\n\n"
    "系统由 MCP Server 层、Core 层与 Libs 可插拔层组成。\n"
    "<!-- draft note -->\n\n\n\n"
    "Libs 层通过工厂模式实现零代码切换后端。\n"
)


def _chunk(text: str) -> Chunk:
    return Chunk(id="doc_0000_ab12cd34", text=text, metadata={"source_path": "a.pdf"})


def _settings_with_llm():
    settings = load_settings("config/settings.yaml")
    chunk_refiner = replace(settings.ingestion.chunk_refiner, use_llm=True)
    return replace(settings, ingestion=replace(settings.ingestion, chunk_refiner=chunk_refiner))


@pytest.mark.integration
@pytest.mark.skipif(not _INTEGRATION_ENABLED, reason="需真实 API 费用：设置 RAG_LLM_INTEGRATION=1 启用")
def test_real_llm_refinement_succeeds():
    settings = _settings_with_llm()
    llm = LLMFactory.create(settings)
    refiner = ChunkRefiner(settings, llm=llm)
    out = refiner.transform([_chunk(_NOISY_TEXT)], trace=TraceContext())
    assert out[0].metadata["refined_by"] == "llm"
    assert "refine_fallback" not in out[0].metadata
    # 输出质量：噪声被清除，核心内容保留。
    assert "第 1 页" not in out[0].text
    assert "<!--" not in out[0].text
    assert "系统架构" in out[0].text
    assert len(out[0].text) > 20


@pytest.mark.integration
def test_invalid_model_gracefully_degrades():
    """无效模型名称（或 provider 不可用）：不崩溃，降级规则模式并标记原因。

    不产生成功计费：仅验证失败路径的降级行为。
    """
    settings = _settings_with_llm()
    settings = replace(settings, llm=replace(settings.llm, model="nonexistent-model-xyz"))
    refiner = ChunkRefiner(settings)  # 内部经 LLMFactory 创建（或创建失败）
    out = refiner.transform([_chunk(_NOISY_TEXT)])
    assert out[0].metadata["refined_by"] == "rule"
    assert "refine_fallback" in out[0].metadata
    # 规则模式兜底仍完成基本清洗。
    assert "第 1 页" not in out[0].text
    assert "系统架构" in out[0].text
