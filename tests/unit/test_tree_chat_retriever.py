"""PI5：Chat ``longdoc`` 模式接入（PAGEINDEX_TREE_RETRIEVAL_SPEC §3.2 / §4）。全部离线。

覆盖：TreeChatRetriever（定界 / PathGuard 过滤 / trace 打点）、classify_error
的 TreeSearch*Error 映射、engine longdoc 帧序列与重试、build_context 位置头、
Citation 只增字段、capabilities 冷启动推断与配置 fail-fast。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from api.chat_wiring import compute_capabilities
from core.chat.answer_generator import AnswerGenerator
from core.chat.engine import ERROR_UPSTREAM, ChatEngine, classify_error
from core.chat.retriever import (
    RETRIEVAL_AUTO,
    RETRIEVAL_LONGDOC,
    RETRIEVAL_MODES,
    ChatRetrievalResult,
)
from core.chat.session_store import SessionStore
from core.response.citation_generator import Citation, CitationGenerator
from core.settings import (
    CHAT_RETRIEVAL_MODES,
    MODE_ROUTER_FALLBACK_MODES,
    ChatSettings,
    ConfigError,
    load_settings,
    validate_settings,
)
from core.types import RetrievalResult
from libs.tree_search.base_tree_search import (
    TreeEvidence,
    TreeSearchBackendError,
    TreeSearchError,
    TreeSearchRequest,
    TreeSearchResponse,
    TreeSearchResponseError,
    TreeSearchTimeoutError,
)
from observability.dashboard.services.chat_retrievers import (
    TreeChatRetriever,
    build_chat_retrievers,
)

_MINIMAL_YAML = """\
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


def _write(tmp_path: Path, section: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML + section, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- fakes


class FakeTreeBackend:
    """确定性树检索后端：plans 依次消费（异常实例 = 抛出）。"""

    backend_name = "fake_tree"

    def __init__(self, plans=None):
        self.plans = list(plans if plans is not None else [])
        self.requests: list[TreeSearchRequest] = []

    def search(self, request: TreeSearchRequest) -> TreeSearchResponse:
        self.requests.append(request)
        plan = self.plans[min(len(self.requests) - 1, len(self.plans) - 1)]
        if isinstance(plan, Exception):
            raise plan
        return plan


class FakeHistory:
    def __init__(self, records: list[dict[str, Any]]):
        self._records = records

    def list_processed(self) -> list[dict[str, Any]]:
        return list(self._records)


class FakeTrace:
    def __init__(self):
        self.stages: list[tuple[str, str, dict[str, Any]]] = []

    def record_stage(self, name: str, *, method: str, **kwargs: Any) -> None:
        self.stages.append((name, method, kwargs.get("details") or {}))


class FakeLLM:
    """最小流式 LLM（engine 帧测试用）。"""

    def __init__(self):
        self.last_usage = None

    def chat(self, messages, *, trace=None, **kwargs):
        raise AssertionError("not used in these tests")

    def chat_stream(self, messages, *, trace=None, **kwargs):
        yield "答案 [1]"


def _evidence(
    source_path: str,
    *,
    section: str | None = "部署 > 向量库",
    page_start: int | None = None,
    page_end: int | None = None,
    line_start: int | None = 12,
    line_end: int | None = 45,
    node_id: str = "n1",
    doc_id: str = "d1",
    text: str = "树证据文本",
) -> TreeEvidence:
    return TreeEvidence(
        source_path=source_path,
        doc_id=doc_id,
        node_id=node_id,
        text=text,
        score=1.0,
        section=section,
        page_start=page_start,
        page_end=page_end,
        line_start=line_start,
        line_end=line_end,
    )


def _response(evidences=None, *, telemetry: dict[str, Any] | None = None) -> TreeSearchResponse:
    return TreeSearchResponse(
        evidences=list(evidences if evidences is not None else []),
        llm_call_count=3,
        total_llm_tokens=1024,
        elapsed_ms=12.5,
        telemetry=telemetry,
    )


def _tree_settings(tmp_path: Path, *, enabled: bool = True) -> Any:
    root = tmp_path / "corpus"
    root.mkdir(exist_ok=True)
    return SimpleNamespace(
        tree_search=SimpleNamespace(
            enabled=enabled,
            allowed_roots=[str(root)],
            denied_substrings=[".env", ".git", "data/db"],
        )
    )


def _make_md(tmp_path: Path, name: str = "a.md") -> Path:
    """PathGuard 证据过滤要求文件真实存在。"""
    path = tmp_path / "corpus" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("# A\n正文\n", encoding="utf-8")
    return path


def _retriever(tmp_path: Path, backend, *, history=None) -> TreeChatRetriever:
    return TreeChatRetriever(backend, settings=_tree_settings(tmp_path), history=history)


# --------------------------------------------------------------------------- TreeChatRetriever


class TestTreeChatRetriever:
    def test_no_collection_sends_null_scope_and_maps_metadata(self, tmp_path):
        path = _make_md(tmp_path)
        backend = FakeTreeBackend([_response([_evidence(str(path))])])
        retriever = _retriever(tmp_path, backend)

        result = retriever.retrieve("q", trace=None)

        assert len(backend.requests) == 1
        assert backend.requests[0].source_paths is None  # 未给 collection → null
        assert len(result.results) == 1
        first = result.results[0]
        assert first.chunk_id == "pageindex:d1:n1"
        assert first.metadata["retrieval_route"] == "longdoc"
        assert first.metadata["source_path"] == str(path)
        assert first.metadata["section"] == "部署 > 向量库"
        assert first.metadata["line_start"] == 12 and first.metadata["line_end"] == 45

    def test_pdf_evidence_maps_pages(self, tmp_path):
        path = _make_md(tmp_path, "r.pdf")
        backend = FakeTreeBackend(
            [_response([_evidence(str(path), section=None, page_start=4, page_end=7, line_start=None, line_end=None)])]
        )
        result = _retriever(tmp_path, backend).retrieve("q")
        metadata = result.results[0].metadata
        assert metadata["page"] == 4 and metadata["page_end"] == 7
        assert metadata["line_start"] is None and metadata["line_end"] is None

    def test_empty_scoped_set_short_circuits_without_backend_call(self, tmp_path):
        _make_md(tmp_path)
        backend = FakeTreeBackend([_response()])
        history = FakeHistory([])  # collection 无 success 记录 → 定界集为空
        trace = FakeTrace()

        result = _retriever(tmp_path, backend, history=history).retrieve("q", collection="c", trace=trace)

        assert backend.requests == []  # D3 先例：空定界集不调用 sidecar
        assert result.results == []
        assert len(trace.stages) == 1
        name, method, details = trace.stages[0]
        assert (name, method) == ("tree_search", "scoped_empty")
        assert details["collection"] == "c" and details["scoped_paths"] == 0

    def test_collection_scope_second_filters_out_of_scope_evidence(self, tmp_path):
        inside = _make_md(tmp_path, "a.md")
        outside = _make_md(tmp_path, "b.md")
        # 历史只登记 a.md；sidecar 越界返回 b.md → 二次过滤剔除（AC2 同构）。
        history = FakeHistory(
            [{"status": "success", "collection": "c", "source_path": str(inside)}]
        )
        backend = FakeTreeBackend([_response([_evidence(str(inside)), _evidence(str(outside))])])
        trace = FakeTrace()

        result = _retriever(tmp_path, backend, history=history).retrieve("q", collection="c", trace=trace)

        assert [r.metadata["source_path"] for r in result.results] == [str(inside)]
        _, method, details = trace.stages[0]
        assert method == "fake_tree"
        assert details["scoped_paths"] == 1
        assert details["evidences"] == 1 and details["filtered_out"] == 1
        assert result.metadata["filtered_out"] == 1

    def test_path_guard_filters_out_of_whitelist_evidence(self, tmp_path):
        _make_md(tmp_path)
        stranger_dir = tmp_path / "elsewhere"
        stranger_dir.mkdir(exist_ok=True)
        stranger = stranger_dir / "x.md"
        stranger.write_text("x", encoding="utf-8")
        backend = FakeTreeBackend([_response([_evidence(str(stranger))])])
        trace = FakeTrace()

        result = _retriever(tmp_path, backend).retrieve("q", trace=trace)

        assert result.results == []  # PathGuard 静默剔除越权证据
        _, method, details = trace.stages[0]
        assert details["evidences"] == 0 and details["filtered_out"] == 1

    def test_trace_details_full_and_leak_free(self, tmp_path):
        path = _make_md(tmp_path)
        telemetry = {"unindexed": 2, "invalid_output": 0}
        backend = FakeTreeBackend([_response([_evidence(str(path))], telemetry=telemetry)])
        trace = FakeTrace()

        _retriever(tmp_path, backend).retrieve("q", trace=trace)

        _, method, details = trace.stages[0]
        assert method == "fake_tree"
        for key in (
            "backend",
            "collection",
            "scoped_paths",
            "evidences",
            "filtered_out",
            "unindexed",
            "llm_call_count",
            "total_llm_tokens",
            "elapsed_ms",
            "tree_telemetry",
        ):
            assert key in details, key
        assert details["unindexed"] == 2
        serialized = json.dumps(details, ensure_ascii=False, default=str)
        assert "api_token" not in serialized
        assert "base_url" not in serialized
        assert "树证据文本" not in serialized  # 证据原文不进 trace

    def test_backend_error_recorded_then_reraised(self, tmp_path):
        backend = FakeTreeBackend([TreeSearchTimeoutError("boom")])
        trace = FakeTrace()
        retriever = _retriever(tmp_path, backend)

        with pytest.raises(TreeSearchTimeoutError):
            retriever.retrieve("q", trace=trace)

        _, method, details = trace.stages[0]
        assert (method, details["error_type"]) == ("error", "TreeSearchTimeoutError")

    def test_top_k_truncates_results(self, tmp_path):
        path = _make_md(tmp_path)
        backend = FakeTreeBackend(
            [_response([_evidence(str(path), node_id=f"n{i}") for i in range(1, 4)])]
        )
        result = _retriever(tmp_path, backend).retrieve("q", top_k=2)
        assert len(result.results) == 2


# --------------------------------------------------------------------------- build_chat_retrievers


class TestBuildChatRetrievers:
    def test_disabled_by_default_no_longdoc(self, tmp_path):
        from unittest.mock import MagicMock

        settings = SimpleNamespace(
            agentic_search=SimpleNamespace(enabled=False),
            tree_search=SimpleNamespace(enabled=False),
        )
        query_service = MagicMock()
        retrievers = build_chat_retrievers(settings, query_service=query_service)
        assert set(retrievers) == {"hybrid"}

    def test_enabled_registers_longdoc(self, tmp_path):
        from unittest.mock import MagicMock

        settings = SimpleNamespace(
            agentic_search=SimpleNamespace(enabled=False),
            tree_search=SimpleNamespace(enabled=True),
        )
        backend = FakeTreeBackend()
        retrievers = build_chat_retrievers(
            settings, query_service=MagicMock(), tree_backend=backend
        )
        assert set(retrievers) == {"hybrid", "longdoc"}
        assert isinstance(retrievers["longdoc"], TreeChatRetriever)


# --------------------------------------------------------------------------- classify_error


class TestClassifyTreeSearchErrors:
    @pytest.mark.parametrize(
        "exc,transient",
        [
            (TreeSearchTimeoutError("timeout"), True),
            (TreeSearchBackendError("unreachable"), True),
            (TreeSearchResponseError("bad json"), False),
            (TreeSearchError("other"), False),
        ],
    )
    def test_mapping_matches_agentic_semantics(self, exc, transient):
        info = classify_error(exc)
        assert info.code == ERROR_UPSTREAM
        assert info.transient is transient

    def test_response_error_message(self):
        assert classify_error(TreeSearchResponseError("x")).message == "检索服务返回了无法解析的响应。"

    def test_not_internal_so_no_stack_trace(self):
        # engine 仅对 INTERNAL 打堆栈；TreeSearch* 全部归 UPSTREAM 即不打堆栈。
        assert classify_error(TreeSearchError("x")).code != "INTERNAL"


# --------------------------------------------------------------------------- engine frames / retry


class TestEngineLongdocMode:
    @pytest.fixture
    def store(self, tmp_path):
        s = SessionStore(tmp_path / "chat.db")
        yield s
        s.close()

    def _engine(self, store, tmp_path, backend, *, settings=None):
        from dataclasses import replace as dc_replace

        sleeps: list[float] = []
        # 关闭 relevance/rewrite：FakeLLM.chat 未实现，本组用例只测检索与帧结构。
        chat_settings = dc_replace(settings or ChatSettings(), relevance_filter=False, query_rewrite=False)
        retrievers = {"longdoc": _retriever(tmp_path, backend)}
        return ChatEngine(
            retrievers=retrievers,
            llm=FakeLLM(),
            session_store=store,
            settings=chat_settings,
            persist_trace=lambda trace: None,
            sleep=sleeps.append,
        )

    def test_frame_sequence_and_citations_carry_position(self, tmp_path, store):
        path = _make_md(tmp_path)
        backend = FakeTreeBackend([_response([_evidence(str(path))])])
        engine = self._engine(store, tmp_path, backend)

        frames = list(engine.run_turn("问题", retrieval="longdoc"))

        types = [f["type"] for f in frames]
        assert types[-1] == "result"
        assert "sources" in types and "stream" in types
        sources = next(f for f in frames if f["type"] == "sources")
        citation = sources["citations"][0]
        assert citation["section"] == "部署 > 向量库"
        assert citation["line_start"] == 12 and citation["line_end"] == 45
        # D8：既有键不变，只增新键。
        assert {"id", "source", "page", "chunk_id", "text", "score"} <= set(citation)
        assert citation["page"] is None  # MD 证据无页码

    def test_timeout_retried_then_success(self, tmp_path, store):
        path = _make_md(tmp_path)
        backend = FakeTreeBackend(
            [TreeSearchTimeoutError("boom"), _response([_evidence(str(path))])]
        )
        settings = ChatSettings(pipeline_max_retries=1)
        engine = self._engine(store, tmp_path, backend, settings=settings)

        frames = list(engine.run_turn("问题", retrieval="longdoc"))

        assert len(backend.requests) == 2  # 超时按 pipeline_max_retries 重试
        assert frames[-1]["type"] == "result"

    def test_longdoc_not_in_auto_candidates(self, tmp_path, store):
        """D10：auto 候选仍取三档，longdoc 只经显式请求值使用。"""
        assert RETRIEVAL_AUTO not in RETRIEVAL_MODES
        assert "longdoc" in RETRIEVAL_MODES


# --------------------------------------------------------------------------- build_context / Citation


def _result(metadata: dict[str, Any]) -> RetrievalResult:
    return RetrievalResult(chunk_id="c1", score=0.9, text="  正文  ", metadata=metadata)


class TestBuildContextPositionHeader:
    def _gen(self):
        # 只测 build_context 的纯渲染；llm 仅被存引用、不被调用。
        return AnswerGenerator(FakeLLM())

    def test_section_and_line_range(self):
        gen = self._gen()
        results = [_result({"source_path": "a.md", "section": "部署 > 向量库", "line_start": 12, "line_end": 45})]
        assert gen.build_context(results) == "[1] a.md · 部署 > 向量库 · L12–45\n正文"

    def test_single_line(self):
        gen = self._gen()
        results = [_result({"source_path": "a.md", "section": "部署", "line_start": 12, "line_end": 12})]
        assert gen.build_context(results) == "[1] a.md · 部署 · L12\n正文"

    def test_section_only(self):
        gen = self._gen()
        results = [_result({"source_path": "a.md", "section": "部署", "line_start": None, "line_end": None})]
        assert gen.build_context(results) == "[1] a.md · 部署\n正文"

    def test_page_and_section_combined(self):
        gen = self._gen()
        results = [_result({"source_path": "a.pdf", "page": 4, "section": "Overview", "line_start": 1, "line_end": 9})]
        assert gen.build_context(results) == "[1] a.pdf, p.4 · Overview · L1–9\n正文"

    def test_no_new_fields_byte_identical(self):
        gen = self._gen()
        results = [_result({"source_path": "a.pdf", "page": 4})]
        assert gen.build_context(results) == "[1] a.pdf, p.4\n正文"


class TestCitationAddedFields:
    def test_to_dict_without_new_fields_unchanged(self):
        citation = Citation(id=1, source="a.pdf", page=4, chunk_id="c", text="t", score=0.9)
        assert citation.to_dict() == {
            "id": 1,
            "source": "a.pdf",
            "page": 4,
            "chunk_id": "c",
            "text": "t",
            "score": 0.9,
        }

    def test_generator_carries_metadata_fields(self):
        results = [
            _result({"source_path": "a.md", "section": "S", "line_start": 3, "line_end": 8}),
            _result({"source_path": "b.md", "section": 42, "line_start": "3", "line_end": True}),  # 类型不符 → 缺失
        ]
        citations = CitationGenerator().generate(results)
        assert citations[0].to_dict()["section"] == "S"
        assert citations[0].to_dict()["line_start"] == 3
        assert "section" not in citations[1].to_dict()
        assert "line_start" not in citations[1].to_dict()


# --------------------------------------------------------------------------- capabilities / config


class TestCapabilitiesLongdoc:
    def test_defaults_off_without_settings(self):
        caps = compute_capabilities(None)
        assert caps["longdoc_enabled"] is False
        assert "longdoc" not in caps["retrievals"]

    def test_cold_start_inference_includes_longdoc(self, tmp_path):
        yaml = (
            "tree_search:\n"
            "  enabled: true\n"
            "  api_token: tok\n"
            f"  allowed_roots: ['{tmp_path / 'corpus'}']\n"
        )
        settings = load_settings(_write(tmp_path, yaml))
        caps = compute_capabilities(settings, available=None)
        assert caps["longdoc_enabled"] is True
        # agentic 关闭：冷启动推断 = hybrid + longdoc（longdoc 在末尾）。
        assert caps["retrievals"] == ["hybrid", "longdoc"]

    def test_full_stack_order_longdoc_last(self, tmp_path):
        yaml = (
            "agentic_search:\n"
            "  enabled: true\n"
            f"  allowed_roots: ['{tmp_path}']\n"
            "tree_search:\n"
            "  enabled: true\n"
            "  api_token: tok\n"
            f"  allowed_roots: ['{tmp_path}']\n"
        )
        settings = load_settings(_write(tmp_path, yaml))
        caps = compute_capabilities(settings, available=None)
        assert caps["retrievals"] == ["hybrid", "agentic_fast", "agentic_deep", "longdoc"]
        # D10：longdoc 不进 auto 候选语义由 _ALL_MODES 保持；auto_routing 逻辑不受影响。
        assert caps["auto_routing"] is False  # mode_router 未启用

    def test_retrieval_modes_constant(self):
        assert RETRIEVAL_LONGDOC == "longdoc"
        assert RETRIEVAL_MODES == ("hybrid", "agentic_fast", "agentic_deep", "longdoc")
        assert CHAT_RETRIEVAL_MODES[-1] == "longdoc"
        assert MODE_ROUTER_FALLBACK_MODES == ("hybrid", "agentic_fast", "agentic_deep")

    def test_fallback_retrieval_rejects_longdoc(self, tmp_path):
        yaml = (
            "tree_search:\n"
            "  enabled: true\n"
            "  api_token: tok\n"
            f"  allowed_roots: ['{tmp_path}']\n"
            "mode_router:\n"
            "  enabled: true\n"
            "  account_id: acct\n"
            "  api_token: tok\n"
            "  fallback_retrieval: longdoc\n"
        )
        with pytest.raises(ConfigError, match="fallback_retrieval"):
            validate_settings(load_settings(_write(tmp_path, yaml)))
