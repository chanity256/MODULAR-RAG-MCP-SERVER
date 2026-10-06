"""E2E：Dashboard 冒烟测试（阶段 I2 + S3.2 + S4.3）。

用 Streamlit ``AppTest``（headless）验证各页面在有数据时可正常
渲染、无 Python 异常。``AppTest.from_function`` 会剥离模块 globals
（imports 不可见），因此采用生成 driver 脚本 + ``AppTest.from_file``
的方式（与真实页面脚本执行语义一致）。

注入策略：
- 追踪页：真 ``TraceService`` 读临时 traces.jsonl（真实解析链路）。
- 数据页：fake DataService（真 DocumentManager 会在仓库 data/ 下建
  SQLite/目录，超出冒烟关注点）。
- 评估页：fake run_eval（真检索栈需网络）。
- 在线检索页（S4）：fake SearchService（真服务会构建 HybridSearch/sidecar）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from streamlit.testing.v1 import AppTest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"

_INGEST_TRACE = {
    "trace_id": "t-ing-1",
    "trace_type": "ingestion",
    "started_at": "2026-08-18T10:00:00+00:00",
    "total_elapsed_ms": 420.0,
    "metadata": {"source_path": "azure_setup.pdf", "collection": "docs"},
    "stages": [
        {"stage": "integrity", "method": "pipeline", "elapsed_ms": 1.0, "details": {"skipped": False}},
        {"stage": "load", "method": "pipeline", "elapsed_ms": 50.0, "details": {"source_path": "azure_setup.pdf"}},
        {"stage": "split", "method": "pipeline", "elapsed_ms": 40.0},
        {"stage": "store", "method": "pipeline", "elapsed_ms": 100.0, "details": {"chunks": 2, "images": 0}},
    ],
}

_QUERY_TRACE = {
    "trace_id": "t-qry-1",
    "trace_type": "query",
    "started_at": "2026-08-18T10:05:00+00:00",
    "total_elapsed_ms": 88.0,
    "metadata": {"query": "bm25 scoring"},
    "stages": [
        {"stage": "query_processing", "method": "query_processor", "elapsed_ms": 2.0},
        {"stage": "dense_retrieval", "method": "dense_retriever", "elapsed_ms": 20.0, "details": {"preview": [{"chunk_id": "a", "score": 0.9}]}},
        {"stage": "sparse_retrieval", "method": "sparse_retriever", "elapsed_ms": 8.0, "details": {"preview": [{"chunk_id": "b", "score": 1.9}]}},
        {"stage": "fusion", "method": "rrf", "elapsed_ms": 1.0, "details": {"preview": [{"chunk_id": "a", "score": 0.03}]}},
        {"stage": "hybrid", "method": "hybrid_search", "elapsed_ms": 30.0},
        {"stage": "rerank", "method": "reranker", "elapsed_ms": 5.0, "details": {"backend": "none", "fallback": False, "before": [{"chunk_id": "a"}], "after": [{"chunk_id": "a", "score": 0.03}]}},
    ],
}

_FAKE_SERVICES = dedent(
    """
    class FakeDataService:
        def list_documents(self, collection=None):
            return [
                DocumentInfo("azure_setup.pdf", "docs", 2, 0, title="Azure Setup"),
                DocumentInfo("bm25_guide.pdf", "docs", 1, 1, title="BM25 Guide"),
            ]

        def get_document_detail(self, doc_id, collection=None):
            return DocumentDetail(
                source_path=doc_id,
                collection=collection or "docs",
                chunk_count=1,
                chunks=[{
                    "id": doc_id + "_0000_x1",
                    "text": "sample chunk text about azure and bm25",
                    "metadata": {"title": "T", "page": 1, "collection": collection or "docs"},
                }],
                images=[{"image_id": "img1", "path": "/nonexistent/img1.png"}],
            )

        def get_collection_stats(self, collection=None):
            return [CollectionStats(name="docs", document_count=2, chunk_count=3, image_count=1)]

        def list_images(self, collection=None, doc_hash=None):
            return [{"image_id": "img1", "file_path": "/nonexistent/img1.png"}]

        def image_bytes(self, image_id):
            return None

        def delete_document(self, source_path, collection):
            raise NotImplementedError

    def fake_run_eval(path, *, top_k=None, filters=None):
        return EvalReport(
            total=2,
            evaluated=2,
            failed=0,
            metrics={"hit_rate": 1.0, "mrr": 0.75},
            per_query=[
                QueryEvalResult("bm25 scoring", ["b"], ["b"], {"hit_rate": 1.0, "mrr": 1.0}),
                QueryEvalResult("azure config", ["a"], ["a"], {"hit_rate": 1.0, "mrr": 0.5}),
            ],
        )
    """
)

_PRELUDE = (
    "import sys\n"
    f"sys.path.insert(0, {str(_SRC_DIR)!r})\n"
    "\n"
    "from ingestion.document_manager import (\n"
    "    CollectionStats,\n"
    "    DocumentDetail,\n"
    "    DocumentInfo,\n"
    ")\n"
    "from observability.evaluation.eval_runner import EvalReport, QueryEvalResult\n"
    + _FAKE_SERVICES
)


def _driver(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"driver_{name}.py"
    path.write_text(_PRELUDE + "\n" + dedent(body), encoding="utf-8")
    return path


def _run(driver: Path) -> AppTest:
    at = AppTest.from_file(str(driver), default_timeout=30)
    at.run()
    return at


@pytest.fixture()
def traces_path(tmp_path: Path) -> Path:
    path = tmp_path / "traces.jsonl"
    lines = [_INGEST_TRACE, _QUERY_TRACE]
    path.write_text("\n".join(json.dumps(item) for item in lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------- #
# 六页面冒烟（spec I2：均可加载、不抛异常）
# --------------------------------------------------------------------- #

def test_overview_page(tmp_path, traces_path):
    driver = _driver(
        tmp_path,
        "overview",
        f"""
        from observability.dashboard.pages import overview
        from observability.dashboard.services.config_service import ConfigService
        from observability.dashboard.services.trace_service import TraceService

        overview.render(
            config_service=ConfigService(config_path={str(_REPO_ROOT / 'config' / 'settings.yaml')!r}),
            data_service=FakeDataService(),
            trace_service=TraceService(traces_path={str(traces_path)!r}),
        )
        """,
    )
    at = _run(driver)
    assert not at.exception
    assert any("系统总览" in str(h.value) for h in at.header)


def test_data_browser_page(tmp_path):
    driver = _driver(
        tmp_path,
        "data_browser",
        """
        from observability.dashboard.pages import data_browser

        data_browser.render(data_service=FakeDataService())
        """,
    )
    assert not _run(driver).exception


def test_ingestion_manager_page(tmp_path):
    driver = _driver(
        tmp_path,
        "ingestion_manager",
        """
        from observability.dashboard.pages import ingestion_manager

        ingestion_manager.render(data_service=FakeDataService(), run_ingest=lambda *a, **k: None)
        """,
    )
    assert not _run(driver).exception


def test_ingestion_traces_page(tmp_path, traces_path):
    driver = _driver(
        tmp_path,
        "ingestion_traces",
        f"""
        from observability.dashboard.pages import ingestion_traces
        from observability.dashboard.services.trace_service import TraceService

        ingestion_traces.render(trace_service=TraceService(traces_path={str(traces_path)!r}))
        """,
    )
    assert not _run(driver).exception


def test_query_traces_page(tmp_path, traces_path):
    driver = _driver(
        tmp_path,
        "query_traces",
        f"""
        from observability.dashboard.pages import query_traces
        from observability.dashboard.services.trace_service import TraceService

        query_traces.render(trace_service=TraceService(traces_path={str(traces_path)!r}))
        """,
    )
    assert not _run(driver).exception


def test_evaluation_panel_page(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    driver = _driver(
        tmp_path,
        "evaluation_panel",
        f"""
        from core.settings import load_settings
        from observability.dashboard.pages import evaluation_panel

        evaluation_panel.render(
            settings=load_settings({str(_REPO_ROOT / 'config' / 'settings.yaml')!r}),
            run_eval=fake_run_eval,
        )
        """,
    )
    assert not _run(driver).exception


# --------------------------------------------------------------------- #
# Agentic 展示冒烟（S3.2）
# --------------------------------------------------------------------- #

_QUERY_TRACE_AGENTIC = {
    "trace_id": "t-qry-agentic",
    "trace_type": "query",
    "started_at": "2026-08-18T10:10:00+00:00",
    "total_elapsed_ms": 220.0,
    "metadata": {"query": "agentic deep dive"},
    "stages": [
        {"stage": "query_processing", "method": "query_processor", "elapsed_ms": 2.0},
        {
            "stage": "hybrid",
            "method": "hybrid_search",
            "elapsed_ms": 60.0,
            "details": {
                "routes": {"dense": 10, "sparse": 8, "agentic": 3},
                "agentic_trigger": {"strategy": "fallback", "triggered": True, "reason": "top_score < 0.5"},
            },
        },
        {
            "stage": "fusion",
            "method": "rrf",
            "elapsed_ms": 1.0,
            "details": {"routes": {"dense": 10, "sparse": 8, "agentic": 3}, "fused": 15},
        },
        {
            "stage": "agentic_search",
            "method": "sirchmunk_http",
            "elapsed_ms": 120.0,
            "details": {
                "backend": "sirchmunk_http",
                "mode": "FAST",
                "evidences": 2,
                "evidence_files": ["/tmp/docs/azure_setup.pdf", "/tmp/docs/bm25_guide.pdf"],
                "llm_call_count": 4,
                "total_llm_tokens": 1830,
                "elapsed_ms": 118.5,
            },
        },
    ],
}


@pytest.fixture()
def agentic_traces_path(tmp_path: Path) -> Path:
    path = tmp_path / "traces_agentic.jsonl"
    lines = [_QUERY_TRACE, _QUERY_TRACE_AGENTIC]
    path.write_text("\n".join(json.dumps(item) for item in lines) + "\n", encoding="utf-8")
    return path


def test_query_traces_page_renders_agentic(tmp_path, agentic_traces_path):
    """含 agentic 阶段的 trace：展示 mode/tokens/调用次数/证据列表与路由信息。"""
    driver = _driver(
        tmp_path,
        "query_traces_agentic",
        f"""
        from observability.dashboard.pages import query_traces
        from observability.dashboard.services.trace_service import TraceService

        query_traces.render(trace_service=TraceService(traces_path={str(agentic_traces_path)!r}))
        """,
    )
    at = _run(driver)
    assert not at.exception
    # 默认选中第一条（最新：agentic trace）。
    subheaders = [h.value for h in at.subheader]
    assert "Agentic 检索" in subheaders
    assert "Agentic 路由" in subheaders
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["模式"] == "FAST"
    assert metrics["LLM Tokens"] == "1830"
    assert metrics["LLM 调用次数"] == "4"
    assert metrics["证据文件数"] == "2"
    captions = [c.value for c in at.caption]
    assert any("backend=sirchmunk_http" in c for c in captions)
    assert any("reason=top_score < 0.5" in c for c in captions)


def test_query_traces_page_hides_agentic(tmp_path, traces_path):
    """无 agentic 阶段的 trace：不渲染 agentic 区块、页面正常。"""
    driver = _driver(
        tmp_path,
        "query_traces_no_agentic",
        f"""
        from observability.dashboard.pages import query_traces
        from observability.dashboard.services.trace_service import TraceService

        query_traces.render(trace_service=TraceService(traces_path={str(traces_path)!r}))
        """,
    )
    at = _run(driver)
    assert not at.exception
    all_text = "\n".join(str(block.value) for block in list(at.subheader) + list(at.caption) + list(at.markdown))
    assert "Agentic" not in all_text


def test_overview_page_shows_agentic_disabled(tmp_path, traces_path):
    """Overview 配置卡：enabled=false 显示 disabled 关闭状态。

    用临时配置固定关闭态，不依赖仓库 settings.yaml 的当前取值。
    """
    import re

    config = tmp_path / "settings_disabled.yaml"
    config.write_text(
        re.sub(
            r"(agentic_search:.*?)enabled: (true|false)",
            r"\1enabled: false",
            (_REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"),
            flags=re.S,
        ),
        encoding="utf-8",
    )
    driver = _driver(
        tmp_path,
        "overview_agentic",
        f"""
        from observability.dashboard.pages import overview
        from observability.dashboard.services.config_service import ConfigService
        from observability.dashboard.services.trace_service import TraceService

        overview.render(
            config_service=ConfigService(config_path={str(config)!r}),
            data_service=FakeDataService(),
            trace_service=TraceService(traces_path={str(traces_path)!r}),
        )
        """,
    )
    at = _run(driver)
    assert not at.exception
    assert any("Agentic Search" in str(c.value) for c in at.subheader)
    # 关闭状态：headline 写出 disabled，extras caption 只带 enabled=False。
    assert any("disabled" in str(b.value) for b in list(at.markdown) + list(at.caption))
    assert any("enabled=False" in str(c.value) for c in at.caption)


# --------------------------------------------------------------------- #
# 在线检索页冒烟（S4.3）
# --------------------------------------------------------------------- #

_PLAYGROUND_PRELUDE = """
import streamlit as st

from observability.dashboard.pages import query_playground
from observability.dashboard.services.search_service import SearchOutcome, SearchService

if "fake_search_service" not in st.session_state:
    st.session_state["fake_search_service"] = None


class FakeAvail:
    def __init__(self, available, reason=None):
        self.available = available
        self.reason = reason


class FakeSearchService(SearchService):
    \"\"\"离线假服务：记录 run 调用，按 enabled/allow_deep 伪造可用性。\"\"\"

    def __init__(self, *, enabled=True, allow_deep=True, outcome=None):
        super().__init__(settings=object())
        self._enabled = enabled
        self._allow_deep = allow_deep
        self.outcome = outcome
        self.calls = []

    def availability(self, settings=None):
        fast = self._enabled
        deep = self._enabled and self._allow_deep
        return {
            "normal": FakeAvail(True),
            "fast": FakeAvail(fast, None if fast else "need agentic_search.enabled"),
            "deep": FakeAvail(deep, None if deep else "need allow_deep"),
        }

    def run(self, mode, query, top_k=None, collection=None):
        self.calls.append({"mode": mode, "query": query, "top_k": top_k, "collection": collection})
        return self.outcome
"""

_PLAYGROUND_MOUNT = """
# AppTest 每次重跑 driver 会重建模块 globals；实例必须经 session_state
# 存活，且只在首次运行时创建（后续 rerun 复用同一实例累计 run 调用）。
if st.session_state["fake_search_service"] is None:
    st.session_state["fake_search_service"] = {service_expr}
query_playground.render(search_service=st.session_state["fake_search_service"])
"""


def _playground_driver(tmp_path: Path, name: str, service_expr: str) -> Path:
    body = _PLAYGROUND_PRELUDE + _PLAYGROUND_MOUNT.format(service_expr=service_expr)
    return _driver(tmp_path, name, body)


def test_query_playground_default_render(tmp_path):
    """默认渲染：header 存在、Normal 默认选中（primary）、collection 仅 Normal 显示。"""
    driver = _playground_driver(tmp_path, "playground_default", "FakeSearchService()")
    at = _run(driver)
    assert not at.exception
    assert any("在线检索" in str(h.value) for h in at.header)

    buttons = {b.label: b for b in at.button}
    assert buttons["Normal"].proto.disabled is False
    assert buttons["Normal"].proto.type == "primary"  # 默认选中 Normal
    assert buttons["Fast"].proto.type != "primary"
    # collection 输入仅在 Normal 模式显示。
    assert "qp_collection" in [t.key for t in at.text_input]


def test_query_playground_agentic_disabled_greyed(tmp_path):
    """agentic 关闭（仓库默认）：Fast/Deep 置灰，help 提示开启方法。"""
    driver = _playground_driver(
        tmp_path, "playground_disabled", "FakeSearchService(enabled=False, allow_deep=False)"
    )
    at = _run(driver)
    assert not at.exception
    buttons = {b.label: b for b in at.button}
    assert buttons["Fast"].proto.disabled is True
    assert buttons["Deep"].proto.disabled is True
    assert "agentic_search.enabled" in buttons["Fast"].help
    assert "allow_deep" in buttons["Deep"].help


def test_query_playground_collection_hidden_for_agentic(tmp_path):
    """Fast/Deep 模式下 collection 输入隐藏；单击模式按钮当轮即高亮。"""
    driver = _playground_driver(tmp_path, "playground_collection", "FakeSearchService()")
    at = _run(driver)
    # 单击 Fast 一次：当轮渲染即变 primary（S4.2 修复的时序问题）。
    at.button(key="qp_btn_fast").click().run()
    buttons = {b.label: b for b in at.button}
    assert buttons["Fast"].proto.type == "primary"
    assert buttons["Normal"].proto.type != "primary"
    assert "qp_collection" not in [t.key for t in at.text_input]


def test_query_playground_runs_three_modes(tmp_path):
    """注入 fake service 后三模式检索：正确分发、Markdown 答案与引用表渲染。"""
    outcome = (
        "SearchOutcome(mode='normal', markdown='answer md', "
        "citations=[{'id': 1, 'source': 'docs/a.pdf', 'page': 1, 'chunk_id': 'c1', 'text': 't', 'score': 0.9}], "
        "elapsed_ms=12.0)"
    )
    driver = _playground_driver(tmp_path, "playground_run", f"FakeSearchService(outcome={outcome})")
    at = _run(driver)
    assert not at.exception
    at.text_input(key="qp_query").set_value("bm25 scoring").run()

    service = at.session_state["fake_search_service"]

    # Normal（默认选中）。
    at.button(key="qp_run").click().run()
    assert service.calls[-1]["mode"] == "normal"
    assert service.calls[-1]["query"] == "bm25 scoring"
    assert service.calls[-1]["top_k"] == 5
    assert not at.exception
    assert any("answer md" in str(m.value) for m in at.markdown)

    # Fast。
    at.button(key="qp_btn_fast").click().run()
    at.button(key="qp_run").click().run()
    assert service.calls[-1]["mode"] == "fast"

    # Deep。
    at.button(key="qp_btn_deep").click().run()
    at.button(key="qp_run").click().run()
    assert service.calls[-1]["mode"] == "deep"

    # 引用表渲染（来源/页码/分数）。
    assert any("docs/a.pdf" in t.value.to_string() for t in at.table)


def test_query_playground_deep_outcome_meta(tmp_path):
    """Deep 结果：agentic 元数据（模式/Token/LLM 调用/耗时/证据文件）渲染。"""
    outcome = (
        "SearchOutcome(mode='deep', markdown='agentic md', "
        "citations=[], elapsed_ms=9000.0, "
        "agentic_meta={'mode': 'DEEP', 'total_llm_tokens': 1234, 'llm_call_count': 3, "
        "'elapsed_ms': 8765.0, 'agentic_answer': 'deep answer', 'evidence_files': ['/tmp/docs/a.md']})"
    )
    driver = _playground_driver(tmp_path, "playground_deep", f"FakeSearchService(outcome={outcome})")
    at = _run(driver)
    at.button(key="qp_btn_deep").click().run()
    at.text_input(key="qp_query").set_value("multi hop").run()
    at.button(key="qp_run").click().run()
    assert not at.exception

    subheaders = [h.value for h in at.subheader]
    assert "Agentic 检索详情" in subheaders
    metrics = {m.label: m.value for m in at.metric}
    assert str(metrics["模式"]) == "DEEP"
    assert str(metrics["Token"]) == "1234"
    assert str(metrics["LLM 调用"]) == "3"
    assert str(metrics["耗时"]) == "8765 ms"
    all_text = "\n".join(str(e.value) for e in list(at.markdown) + list(at.caption))
    assert "/tmp/docs/a.md" in all_text


def test_query_playground_error_display(tmp_path):
    """isError 结果：st.error 友好提示，页面不崩溃。"""
    outcome = (
        "SearchOutcome(mode='fast', markdown='Agentic search is currently unavailable: sidecar down', "
        "is_error=True, error='Agentic search is currently unavailable: sidecar down')"
    )
    driver = _playground_driver(
        tmp_path, "playground_error", f"FakeSearchService(outcome={outcome})"
    )
    at = _run(driver)
    at.button(key="qp_btn_fast").click().run()
    at.text_input(key="qp_query").set_value("q").run()
    at.button(key="qp_run").click().run()
    assert not at.exception
    assert any("sidecar down" in str(e.value) for e in at.error)
