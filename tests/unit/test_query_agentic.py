"""``scripts/query.py --agentic`` 第三路 Agentic CLI 单元测试（阶段 S2.5）。

纯离线：经 importlib 按路径加载 scripts/query.py（scripts 非 package），
第三路后端注入 :class:`~libs.agentic_search.fake_backend.FakeAgenticSearch`
（确定性 Fake，零网络零子进程，不触真实 sidecar）；主路 dense/sparse 用
可控 fake 检索器注入真 ``HybridSearch``（S2.3 ``agentic_retriever`` 注入点）。

覆盖：
- 参数解析：``--agentic`` 注册与默认 False（未传时行为不变，零回归）。
- 适配器：``AgenticRouteAdapter`` 把 ``BaseAgenticSearch`` 包成
  ``retrieve(query, top_k, trace=...)`` 契约（转换/截断/Token/耗时记录）。
- ``--agentic --verbose`` 全链路：退出码 0，verbose 输出 agentic 段
  （证据来源 source_path / total_llm_tokens / 耗时）。
- ``--agentic`` 但 ``settings.agentic_search.enabled=false``：明确报错提示
  如何开启，退出码 2（D7 用法/配置错误语义，无新增退出码）。
- 未传 ``--agentic``：verbose 输出不含 agentic 段（主链路零改动）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

from core.query_engine.hybrid_search import HybridSearch
from core.settings import load_settings
from core.types import RetrievalResult
from libs.agentic_search import AgenticSearchRequest, FakeAgenticSearch

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "query.py"
_MODULE_NAME = "scripts_query_agentic"


def _load_cli():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


_BASE_YAML = dedent(
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
      persist_path: ./data/db/chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
      top_k_final: 10
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _write_config(tmp_path: Path, agentic_section: str) -> Path:
    config = tmp_path / "settings.yaml"
    config.write_text(_BASE_YAML + agentic_section, encoding="utf-8")
    return config


_AGENTIC_ENABLED_YAML = dedent(
    """\

    agentic_search:
      enabled: true
      backend: fake
      trigger: always
      allowed_roots: ["/tmp"]
    """
)


# ---------------------------------------------------------------------------
# 主路可控 fake 检索器（对齐 test_hybrid_agentic_route.py 手法）
# ---------------------------------------------------------------------------


def _r(chunk_id: str, score: float, source: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        score=score,
        text=f"text of {chunk_id}",
        metadata={"source_path": source},
    )


class _FakeDense:
    def retrieve(self, query, top_k, filters=None, *, trace=None):
        return [_r("dense_1", 0.9, "docs/a.md")][:top_k]


class _FakeSparse:
    def retrieve(self, keywords, top_k, *, trace=None):
        return [_r("sparse_1", 0.8, "docs/b.md")][:top_k]


def _build_search(settings, adapter) -> HybridSearch:
    """真 HybridSearch + fake 主路 + 注入 adapter 的第三路（触发策略 always）。"""
    return HybridSearch(
        settings,
        dense_retriever=_FakeDense(),
        sparse_retriever=_FakeSparse(),
        agentic_retriever=adapter,
    )


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def test_parser_registers_agentic_flag_default_false():
    args = cli.parse_args(["--query", "q"])
    assert args.agentic is False


def test_parser_agentic_flag_true():
    args = cli.parse_args(["--query", "q", "--agentic"])
    assert args.agentic is True


# ---------------------------------------------------------------------------
# 适配器：BaseAgenticSearch -> retrieve 契约
# ---------------------------------------------------------------------------


def test_adapter_wraps_fake_backend_contract(tmp_path):
    backend = FakeAgenticSearch()
    adapter = cli.AgenticRouteAdapter(backend)
    results = adapter.retrieve("怎么部署", top_k=5)
    assert backend.last_request == AgenticSearchRequest(query="怎么部署")
    assert adapter.last_response is not None
    assert adapter.last_response.total_llm_tokens == 1024
    assert adapter.last_elapsed_ms is not None
    assert len(results) == 1
    assert results[0].metadata["retrieval_route"] == "agentic"
    assert results[0].metadata["source_path"] == "fake/doc.md"
    assert results[0].chunk_id.startswith("agentic:")


def test_adapter_top_k_truncates():
    evidences = [
        __import__(
            "libs.agentic_search.base_agentic_search",
            fromlist=["AgenticEvidence"],
        ).AgenticEvidence(
            source_path=f"fake/{i}.md", snippet=f"s{i}", score=0.9 - i
        )
        for i in range(5)
    ]
    adapter = cli.AgenticRouteAdapter(FakeAgenticSearch(evidences=evidences))
    assert len(adapter.retrieve("q", top_k=2)) == 2


# ---------------------------------------------------------------------------
# main 全链路（fake 后端注入，离线）
# ---------------------------------------------------------------------------


def test_main_agentic_verbose_full_chain(tmp_path, capsys):
    config = _write_config(tmp_path, _AGENTIC_ENABLED_YAML)
    settings = load_settings(str(config))
    backend = FakeAgenticSearch()
    adapter = cli.AgenticRouteAdapter(backend)
    search = _build_search(settings, adapter)

    code = cli.main(
        ["--query", "部署密钥", "--agentic", "--verbose", "--no-rerank", "--config", str(config)],
        search=search,
        agentic_backend=backend,
    )
    assert code == cli.EXIT_OK
    output = capsys.readouterr().out
    # verbose agentic 段：触发 + 证据来源 + Token + 耗时
    assert "--- agentic (" in output
    assert "fake/doc.md" in output
    assert "tokens=1024" in output
    assert "elapsed=" in output
    # 第三路确经适配器调用（请求透传查询文本）
    assert backend.last_request is not None
    assert backend.last_request.query == "部署密钥"
    # 融合结果含 agentic 路条目（确定性 agentic: ID 前缀）
    assert any(r.chunk_id.startswith("agentic:") for r in search.last_fused_results)


def test_main_agentic_disabled_usage_error(tmp_path, capsys):
    config = _write_config(tmp_path, "")  # 无 agentic 节 => enabled 默认 false
    code = cli.main(
        ["--query", "q", "--agentic", "--no-rerank", "--config", str(config)],
    )
    assert code == cli.EXIT_USAGE
    output = capsys.readouterr().out
    assert "agentic_search.enabled" in output
    assert "true" in output


def test_main_agentic_disabled_does_not_build_search(tmp_path, capsys, monkeypatch):
    """enabled=false 时在组件构建前报错退出（不初始化查询栈）。"""
    config = _write_config(tmp_path, "")
    called = {"search": False}

    def _boom(_settings):
        called["search"] = True
        raise AssertionError("build_search must not be called")

    monkeypatch.setattr(cli, "build_search", _boom)
    code = cli.main(
        ["--query", "q", "--agentic", "--no-rerank", "--config", str(config)],
    )
    assert code == cli.EXIT_USAGE
    assert called["search"] is False


# ---------------------------------------------------------------------------
# 零回归：未传 --agentic 时无 agentic 段、行为与基线一致
# ---------------------------------------------------------------------------


def test_verbose_without_agentic_flag_has_no_agentic_section():
    outcome = cli.QueryOutcome(query="q", top_k=1)
    outcome.dense = [_r("dense_1", 0.9, "docs/a.md")]
    text = cli.render_verbose_sections(outcome)
    assert "agentic" not in text


def test_verbose_agentic_not_triggered_section():
    outcome = cli.QueryOutcome(query="q", top_k=1)
    outcome.agentic_requested = True
    outcome.agentic_triggered = False
    outcome.agentic_trigger_reason = "main-route signals sufficient"
    text = cli.render_verbose_sections(outcome)
    assert "--- agentic (not triggered: main-route signals sufficient) ---" in text
