"""AC4: Hybrid 第三路白名单修复 + collection 定界单元测试（离线）。

覆盖 AGENTIC_COLLECTION_ISOLATION_SPEC §4 AC4 六组：无 collection 发
``allowed_roots``、collection 定界与空集短路（D3）、证据两级过滤、
PathGuard 构造失败降级、鸭子类型 fake 兼容、S2.3 未触发回归红线。
既有第三路测试文件（``test_query_agentic`` / ``test_hybrid_agentic_route``
/ ``test_agentic_dedup``）零改动即通过（基线由「裸构造 = 无守卫模式」保证）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine.agentic_trigger import TRIGGER_ALWAYS, TRIGGER_TOOL_ONLY
from core.query_engine.hybrid_search import HybridSearch
from core.settings import AgenticSearchConfig, load_settings
from core.types import RetrievalResult
from libs.agentic_search.adapter import AgenticRouteAdapter, build_agentic_retriever
from libs.agentic_search.base_agentic_search import AgenticEvidence
from libs.agentic_search.fake_backend import FakeAgenticSearch

pytestmark = pytest.mark.unit

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
      persist_path: ./data/db/chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
      top_k_dense: 20
      top_k_sparse: 20
      top_k_final: 10
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _r(chunk_id: str, score: float) -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id, score=score, text=chunk_id, metadata={"src": chunk_id})


class _FakeDense:
    def retrieve(self, query, top_k, filters=None, *, trace=None):
        return [_r("d1", 0.9), _r("d2", 0.8)][:top_k]


class _FakeSparse:
    def retrieve(self, keywords, top_k, *, trace=None):
        return [_r("s1", 12.0), _r("s2", 9.0)][:top_k]


class _DuckRetriever:
    """既有鸭子类型 fake 的调用形态：``retrieve(query, top_k, trace=None)``。"""

    def __init__(self) -> None:
        self.calls = 0

    def retrieve(self, query, top_k, trace=None):
        self.calls += 1
        return [_r("agentic_1", 0.7)]


class FakeHistory:
    def __init__(self, records: list[dict]) -> None:
        self._records = records

    def list_processed(self) -> list[dict]:
        return list(self._records)


def _rec(source_path: str, collection: str) -> dict:
    return {"source_path": source_path, "collection": collection, "status": "success"}


@pytest.fixture()
def env(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("alpha", encoding="utf-8")
    (docs / "b.md").write_text("beta", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("leak", encoding="utf-8")
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(_VALID_YAML, encoding="utf-8")
    return {
        "docs": docs,
        "outside": outside,
        "a": str(docs / "a.md"),
        "b": str(docs / "b.md"),
        "settings": load_settings(config_path),
    }


def _equipped(settings, allowed_roots, trigger=TRIGGER_ALWAYS, history=None, backend=None):
    cfg = AgenticSearchConfig(
        enabled=True, trigger=trigger, allowed_roots=allowed_roots,
        denied_substrings=[".env", ".git", "data/db"],
    )
    scoped_settings = replace(settings, agentic_search=cfg)
    adapter = AgenticRouteAdapter(
        backend if backend is not None else FakeAgenticSearch(),
        settings=scoped_settings,
        history=history,
    )
    return adapter, scoped_settings


def _hybrid(settings, adapter, trigger=TRIGGER_ALWAYS):
    cfg = replace(settings.agentic_search, enabled=True, trigger=trigger)
    return HybridSearch(
        replace(settings, agentic_search=cfg),
        dense_retriever=_FakeDense(),
        sparse_retriever=_FakeSparse(),
        agentic_retriever=adapter,
    )


# AC4-1：无 collection → paths 恒非 None，为 allowed_roots 绝对路径
class TestWhitelistPaths:
    def test_no_collection_sends_allowed_roots(self, env):
        backend = FakeAgenticSearch()
        adapter, _ = _equipped(env["settings"], [str(env["docs"])], backend=backend)
        adapter.retrieve("q")
        assert backend.last_request is not None
        assert backend.last_request.paths == [str(env["docs"].resolve())]

    def test_build_agentic_retriever_equips_settings(self, env):
        cfg = AgenticSearchConfig(enabled=True, allowed_roots=[str(env["docs"])])
        settings = replace(env["settings"], agentic_search=cfg)
        adapter = build_agentic_retriever(settings)
        assert isinstance(adapter, AgenticRouteAdapter)
        assert adapter._settings is settings  # 装配模式（懒构造守卫与历史句柄）


# AC4-2：collection → 定界集；空定界集 → [] 且不调用后端（D3）
class TestCollectionScoping:
    def test_collection_scopes_paths(self, env):
        backend = FakeAgenticSearch()
        history = FakeHistory([_rec(env["a"], "A"), _rec(env["b"], "B")])
        adapter, _ = _equipped(env["settings"], [str(env["docs"])], history=history, backend=backend)
        adapter.retrieve("q", collection="A")
        assert backend.last_request.paths == [str(Path(env["a"]).resolve())]

    def test_empty_scope_short_circuits_without_backend_call(self, env):
        backend = FakeAgenticSearch()
        adapter, _ = _equipped(env["settings"], [str(env["docs"])], backend=backend)
        results = adapter.retrieve("q", collection="EMPTY")
        assert results == []
        assert backend.last_request is None  # D3：未调用 sidecar


# AC4-3：证据恒经白名单过滤；带 collection 再按定界集收窄
class TestEvidenceFiltering:
    def _backend(self, env) -> FakeAgenticSearch:
        evidences = [
            AgenticEvidence(source_path=env["a"], snippet="in scope", score=1.0),
            AgenticEvidence(source_path=env["b"], snippet="other collection", score=0.8),
            AgenticEvidence(source_path=str(env["outside"] / "leak.md"), snippet="leak", score=0.7),
        ]
        return FakeAgenticSearch(evidences=evidences, source_files=[env["a"], env["b"], str(env["outside"] / "leak.md")])

    def test_scoped_call_drops_out_of_scope_evidence(self, env):
        backend = self._backend(env)
        history = FakeHistory([_rec(env["a"], "A"), _rec(env["b"], "B")])
        adapter, _ = _equipped(env["settings"], [str(env["docs"])], history=history, backend=backend)
        results = adapter.retrieve("q", collection="A")
        assert adapter.last_response.source_files == [str(Path(env["a"]).resolve())]
        assert len(results) == 1
        assert results[0].metadata["source_path"] == str(Path(env["a"]).resolve())

    def test_unscoped_call_still_filters_whitelist(self, env):
        backend = self._backend(env)
        adapter, _ = _equipped(env["settings"], [str(env["docs"])], backend=backend)
        results = adapter.retrieve("q")
        assert set(adapter.last_response.source_files) == {str(Path(env["a"]).resolve()), str(Path(env["b"]).resolve())}
        assert len(results) == 2


# AC4-4：PathGuard 构造失败 → 不调用后端，HybridSearch 降级为两路
class TestGuardFailureDegrade:
    def test_missing_root_degrades_to_two_routes(self, env, tmp_path):
        backend = FakeAgenticSearch()
        missing = tmp_path / "missing-root"
        adapter, scoped_settings = _equipped(env["settings"], [str(missing)], backend=backend)
        hybrid = _hybrid(scoped_settings, adapter)
        results = hybrid.search("q")
        assert backend.last_request is None  # fail-closed：未调用 sidecar
        assert "agentic" in hybrid.last_route_failures
        assert hybrid.last_agentic_results == []
        assert results  # dense+sparse 两路正常返回


# AC4-5：兼容 —— filters 不含 collection 时调用形态与基线相同
class TestDuckTypingCompat:
    def test_old_signature_fake_receives_positional_call_only(self, env):
        duck = _DuckRetriever()
        cfg = AgenticSearchConfig(enabled=True, trigger=TRIGGER_ALWAYS, allowed_roots=[str(env["docs"])])
        settings = replace(env["settings"], agentic_search=cfg)
        hybrid = HybridSearch(
            settings, dense_retriever=_FakeDense(), sparse_retriever=_FakeSparse(), agentic_retriever=duck
        )
        results = hybrid.search("q")
        assert duck.calls == 1  # 位置参数形态，未传未知关键字（否则 TypeError 降级）
        assert hybrid.last_route_failures == {}
        assert hybrid.last_agentic_results

    def test_old_signature_fake_with_collection_degrades_gracefully(self, env):
        duck = _DuckRetriever()
        cfg = AgenticSearchConfig(enabled=True, trigger=TRIGGER_ALWAYS, allowed_roots=[str(env["docs"])])
        settings = replace(env["settings"], agentic_search=cfg)
        hybrid = HybridSearch(
            settings, dense_retriever=_FakeDense(), sparse_retriever=_FakeSparse(), agentic_retriever=duck
        )
        results = hybrid.search("q", filters={"collection": "c1"})
        # 旧签名 fake 收到未知关键字 → TypeError → 既有异常分支降级，不抛错。
        assert "agentic" in hybrid.last_route_failures
        assert hybrid.last_agentic_results == []
        assert results


# AC4-6：S2.3 回归红线 —— 未触发第三路时主链路与基线一致
class TestUntriggeredBaseline:
    def test_tool_only_never_calls_route_and_matches_plain_hybrid(self, env):
        backend = FakeAgenticSearch()
        adapter, scoped_settings = _equipped(
            env["settings"], [str(env["docs"])], trigger=TRIGGER_TOOL_ONLY, backend=backend
        )
        hybrid = _hybrid(scoped_settings, adapter, trigger=TRIGGER_TOOL_ONLY)
        results = hybrid.search("q")
        assert backend.last_request is None
        assert hybrid.last_agentic_results == []
        assert hybrid.last_route_failures == {}

        plain_cfg = replace(env["settings"].agentic_search, enabled=False)
        plain = HybridSearch(
            replace(env["settings"], agentic_search=plain_cfg),
            dense_retriever=_FakeDense(),
            sparse_retriever=_FakeSparse(),
        )
        assert [r.chunk_id for r in plain.search("q")] == [r.chunk_id for r in results]
