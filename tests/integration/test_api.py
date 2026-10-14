"""FastAPI 骨架与只读路由集成测试（WC2.1 / WC2.2 / WC2.3）。

全部离线：注入 fake DataService、真实 TraceService（读 tmp jsonl）与真实
ConfigService（读仓库配置文件，不触网）。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import ServiceContainer
from core.settings import ApiSettings, ConfigError, load_settings
from ingestion.document_manager import CollectionStats, DocumentDetail, DocumentInfo
from observability.dashboard.services.config_service import ConfigService
from observability.dashboard.services.trace_service import TraceService

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
TOKEN = "unit-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"

V1_GET_PATHS = [
    "/api/v1/overview",
    "/api/v1/collections",
    "/api/v1/collections/docs",
    "/api/v1/documents",
    "/api/v1/documents/a.md",
    "/api/v1/images",
    "/api/v1/images/img1",
    "/api/v1/traces",
    "/api/v1/traces/t-ing",
]


# --------------------------------------------------------------------------- #
# fakes / fixtures
# --------------------------------------------------------------------------- #
class FakeData:
    """与 DataService 读接口同形的 fake。"""

    def __init__(self, documents=(), details=None, images=(), image_files=None, stats_error=None):
        self.documents = list(documents)
        self.details = dict(details or {})
        self.images = list(images)
        self.image_files = dict(image_files or {})
        self.stats_error = stats_error

    def list_documents(self, collection=None):
        return [d for d in self.documents if collection in (None, d.collection)]

    def get_document_detail(self, doc_id, collection=None):
        detail = self.details.get(doc_id)
        if detail is not None and collection not in (None, detail.collection):
            return None
        return detail

    def get_collection_stats(self, collection=None):
        if self.stats_error is not None:
            raise self.stats_error
        buckets: dict[str, CollectionStats] = {}
        for d in self.documents:
            b = buckets.setdefault(d.collection, CollectionStats(name=d.collection))
            b.document_count += 1
            b.chunk_count += d.chunk_count
            b.image_count += d.image_count
        if collection is not None:
            return [buckets.get(collection, CollectionStats(name=collection))]
        return [buckets[n] for n in sorted(buckets)]

    def list_images(self, collection=None, doc_hash=None):
        return [
            dict(i)
            for i in self.images
            if collection in (None, i.get("collection")) and doc_hash in (None, i.get("doc_hash"))
        ]

    def image_bytes(self, image_id):
        return self.image_files.get(image_id)


def _doc(source, collection="docs", chunks=2, images=0, title=None):
    return DocumentInfo(
        source_path=source,
        collection=collection,
        chunk_count=chunks,
        image_count=images,
        ingested_at="2026-10-01T00:00:00",
        doc_hash=f"hash-{source}",
        title=title or source,
    )


def _detail(source, collection="docs"):
    return DocumentDetail(
        source_path=source,
        collection=collection,
        chunk_count=2,
        chunks=[
            {"id": "c1", "text": "hello world", "metadata": {"title": "T", "page": 1}},
            {"id": "c2", "text": "second chunk", "metadata": {}},
        ],
        images=[
            {
                "image_id": "img1",
                "file_path": "/secret/server/path.png",
                "collection": collection,
                "doc_hash": "h1",
                "page_num": 1,
                "created_at": "2026-10-01",
            }
        ],
        ingested_at="2026-10-01T00:00:00",
        doc_hash="h1",
        title="Title",
    )


def _stage(name, ms, method="pipeline", **details):
    return {"stage": name, "elapsed_ms": ms, "method": method, "details": details}


INGESTION_TRACE = {
    "trace_id": "t-ing",
    "trace_type": "ingestion",
    "started_at": "2026-10-02T10:00:00+00:00",
    "finished_at": "2026-10-02T10:00:01+00:00",
    "total_elapsed_ms": 120.5,
    "metadata": {"source_path": "/docs/guide.pdf", "collection": "docs"},
    "stages": [
        _stage("integrity", 1.0, skipped=False),
        _stage("load", 10.0, source_path="/docs/guide.pdf"),
        _stage("split", 5.0, chunks=7),
        _stage("store", 40.0, chunks=7, images=1),
    ],
}
INGESTION_TRACE_OLD = {
    "trace_id": "t-ing-old",
    "trace_type": "ingestion",
    "started_at": "2026-10-01T10:00:00+00:00",
    "total_elapsed_ms": 3.0,
    "metadata": {"collection": "docs"},
    "stages": [_stage("integrity", 1.0, skipped=True), _stage("load", 1.0, source_path="/docs/old.md")],
}
QUERY_TRACE = {
    "trace_id": "t-query",
    "trace_type": "query",
    "started_at": "2026-10-03T10:00:00+00:00",
    "finished_at": "2026-10-03T10:00:01+00:00",
    "total_elapsed_ms": 55.0,
    "metadata": {"query": "How does RRF work?", "collection": None},
    "stages": [
        _stage("dense_retrieval", 12.0, method="dense", preview=[{"chunk_id": "c1", "score": 0.9}]),
        _stage("rerank", 8.0, method="reranker", backend="none", before=[], after=[]),
    ],
}


def _write_traces(path: Path, records) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def settings():
    loaded = load_settings(_REPO_CONFIG)
    # 仓库配置可能启用 agentic；关掉以免 /health 默认探针去连真实 sidecar（测试零网络）
    return replace(loaded, agentic_search=replace(loaded.agentic_search, enabled=False))


@pytest.fixture
def traces_path(tmp_path):
    return _write_traces(tmp_path / "traces.jsonl", [INGESTION_TRACE_OLD, INGESTION_TRACE, QUERY_TRACE])


@pytest.fixture
def data():
    return FakeData(
        documents=[
            _doc("a.md", "docs", chunks=3, images=1, title="Alpha Guide"),
            _doc("b.md", "docs", chunks=2),
            _doc("/abs/path/c.pdf", "other", chunks=5, title="Gamma"),
        ],
        details={"a.md": _detail("a.md"), "/abs/path/c.pdf": _detail("/abs/path/c.pdf", "other")},
        images=[
            {"image_id": "img1", "file_path": "/secret/p1.png", "collection": "docs", "doc_hash": "h1",
             "page_num": 1, "created_at": "x"},
            {"image_id": "img2", "file_path": "/secret/p2.png", "collection": "other", "doc_hash": "h2",
             "page_num": None, "created_at": "y"},
        ],
        image_files={"img1": (PNG_BYTES, "image/png")},
    )


def make_app(settings, *, data=None, traces_path=None, api=None, **container_kwargs):
    container = ServiceContainer(
        settings,
        config_service=ConfigService(settings),
        data_service=data if data is not None else FakeData(),
        trace_service=TraceService(traces_path) if traces_path is not None else TraceService("/nonexistent/t.jsonl"),
        **container_kwargs,
    )
    return create_app(api_settings=api or ApiSettings(), services=container)


def make_client(settings, **kwargs):
    return TestClient(make_app(settings, **kwargs), raise_server_exceptions=False)


@pytest.fixture
def client(settings, data, traces_path):
    return make_client(settings, data=data, traces_path=traces_path)


@pytest.fixture
def empty_client(settings):
    return make_client(settings)


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert "details" in body["error"]
    return body["error"]


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #
class TestOverview:
    def test_normal(self, client):
        body = client.get("/api/v1/overview").json()
        names = [c["name"] for c in body["components"]]
        assert {"LLM", "Embedding", "Splitter", "Reranker", "Evaluator", "Agentic Search"} <= set(names)
        llm = next(c for c in body["components"] if c["name"] == "LLM")
        assert llm["headline"].startswith(llm["provider"])
        assert body["totals"] == {"collections": 2, "documents": 3, "chunks": 10, "images": 1}
        assert [c["name"] for c in body["collections"]] == ["docs", "other"]
        assert body["latest_ingestion"]["trace_id"] == "t-ing"
        assert body["latest_query"]["trace_id"] == "t-query"
        assert body["warnings"] == []

    def test_empty(self, empty_client):
        body = empty_client.get("/api/v1/overview").json()
        assert body["totals"] == {"collections": 0, "documents": 0, "chunks": 0, "images": 0}
        assert body["collections"] == []
        assert body["latest_ingestion"] is None
        assert body["latest_query"] is None
        assert body["components"]

    def test_stats_failure_degrades_with_warning(self, settings, traces_path):
        client = make_client(settings, data=FakeData(stats_error=RuntimeError("chroma down")), traces_path=traces_path)
        response = client.get("/api/v1/overview")
        assert response.status_code == 200
        body = response.json()
        assert body["collections"] == []
        assert body["warnings"] == ["collection stats unavailable"]
        assert "chroma down" not in response.text
        assert body["latest_query"]["trace_id"] == "t-query"


# --------------------------------------------------------------------------- #
# collections
# --------------------------------------------------------------------------- #
class TestCollections:
    def test_list(self, client):
        body = client.get("/api/v1/collections").json()
        assert body["items"] == [
            {"name": "docs", "document_count": 2, "chunk_count": 5, "image_count": 1},
            {"name": "other", "document_count": 1, "chunk_count": 5, "image_count": 0},
        ]

    def test_empty(self, empty_client):
        assert empty_client.get("/api/v1/collections").json() == {"items": []}

    def test_get_one(self, client):
        assert client.get("/api/v1/collections/other").json()["chunk_count"] == 5

    def test_get_missing_is_404(self, client):
        error = assert_error(client.get("/api/v1/collections/nope"), 404, "not_found")
        assert "nope" in error["message"]


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #
class TestDocuments:
    def test_list(self, client):
        body = client.get("/api/v1/documents").json()
        assert body["total"] == 3
        assert body["limit"] == 100 and body["offset"] == 0
        first = body["items"][0]
        assert first["source_path"] == "a.md"
        assert first["title"] == "Alpha Guide"
        assert first["chunk_count"] == 3
        assert first["doc_hash"] == "hash-a.md"

    def test_empty(self, empty_client):
        assert empty_client.get("/api/v1/documents").json() == {"items": [], "total": 0, "limit": 100, "offset": 0}

    def test_filter_collection(self, client):
        body = client.get("/api/v1/documents", params={"collection": "other"}).json()
        assert [d["source_path"] for d in body["items"]] == ["/abs/path/c.pdf"]

    def test_keyword_matches_title_and_path_case_insensitive(self, client):
        by_title = client.get("/api/v1/documents", params={"keyword": "ALPHA"}).json()
        assert [d["source_path"] for d in by_title["items"]] == ["a.md"]
        by_path = client.get("/api/v1/documents", params={"keyword": "abs/path"}).json()
        assert by_path["total"] == 1
        assert client.get("/api/v1/documents", params={"keyword": "zzz"}).json()["total"] == 0

    def test_pagination(self, client):
        body = client.get("/api/v1/documents", params={"limit": 1, "offset": 1}).json()
        assert body["total"] == 3
        assert [d["source_path"] for d in body["items"]] == ["b.md"]
        past_end = client.get("/api/v1/documents", params={"offset": 10}).json()
        assert past_end["items"] == [] and past_end["total"] == 3

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"limit": "x"}])
    def test_invalid_params_are_422(self, client, params):
        error = assert_error(client.get("/api/v1/documents", params=params), 422, "validation_error")
        assert error["details"] and {"loc", "msg", "type"} <= set(error["details"][0])

    def test_detail(self, client):
        body = client.get("/api/v1/documents/a.md").json()
        assert body["source_path"] == "a.md"
        assert [c["id"] for c in body["chunks"]] == ["c1", "c2"]
        assert body["chunks"][0]["metadata"]["page"] == 1
        assert body["images"][0]["image_id"] == "img1"

    def test_detail_never_exposes_server_file_path(self, client):
        assert "/secret/server/path.png" not in client.get("/api/v1/documents/a.md").text

    def test_detail_with_url_encoded_absolute_path(self, client):
        response = client.get("/api/v1/documents/" + quote("/abs/path/c.pdf", safe=""), params={"collection": "other"})
        assert response.status_code == 200
        assert response.json()["source_path"] == "/abs/path/c.pdf"

    def test_detail_missing_is_404(self, client):
        assert_error(client.get("/api/v1/documents/missing.md"), 404, "not_found")

    def test_detail_wrong_collection_is_404(self, client):
        assert_error(client.get("/api/v1/documents/a.md", params={"collection": "other"}), 404, "not_found")

    def test_detail_on_empty_store_is_404(self, empty_client):
        assert_error(empty_client.get("/api/v1/documents/a.md"), 404, "not_found")


# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #
class TestImages:
    def test_list_omits_file_path(self, client):
        response = client.get("/api/v1/images")
        items = response.json()
        assert [i["image_id"] for i in items] == ["img1", "img2"]
        assert all("file_path" not in i for i in items)
        assert "/secret" not in response.text

    def test_filters(self, client):
        assert [i["image_id"] for i in client.get("/api/v1/images", params={"collection": "other"}).json()] == ["img2"]
        assert [i["image_id"] for i in client.get("/api/v1/images", params={"doc_hash": "h1"}).json()] == ["img1"]

    def test_empty(self, empty_client):
        assert empty_client.get("/api/v1/images").json() == []

    def test_bytes(self, client):
        response = client.get("/api/v1/images/img1")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == PNG_BYTES

    def test_unknown_or_missing_file_is_404(self, client):
        assert_error(client.get("/api/v1/images/img2"), 404, "not_found")  # 索引有记录但文件缺失
        assert_error(client.get("/api/v1/images/ghost"), 404, "not_found")


# --------------------------------------------------------------------------- #
# traces
# --------------------------------------------------------------------------- #
class TestTraces:
    def test_list_newest_first(self, client):
        body = client.get("/api/v1/traces").json()
        assert body["total"] == 3
        assert [t["trace_id"] for t in body["items"]] == ["t-query", "t-ing", "t-ing-old"]

    def test_filter_by_type(self, client):
        ing = client.get("/api/v1/traces", params={"trace_type": "ingestion"}).json()
        assert [t["trace_id"] for t in ing["items"]] == ["t-ing", "t-ing-old"]
        qry = client.get("/api/v1/traces", params={"trace_type": "query"}).json()
        assert [t["trace_id"] for t in qry["items"]] == ["t-query"]

    def test_invalid_type_is_422(self, client):
        assert_error(client.get("/api/v1/traces", params={"trace_type": "chat"}), 422, "validation_error")

    def test_keyword_on_query_source_and_id(self, client):
        assert [t["trace_id"] for t in client.get("/api/v1/traces", params={"keyword": "rrf"}).json()["items"]] == ["t-query"]
        by_source = client.get("/api/v1/traces", params={"keyword": "guide.pdf"}).json()
        assert [t["trace_id"] for t in by_source["items"]] == ["t-ing"]
        by_old_source = client.get("/api/v1/traces", params={"keyword": "old.md"}).json()  # 源路径只在 load 阶段
        assert [t["trace_id"] for t in by_old_source["items"]] == ["t-ing-old"]
        assert client.get("/api/v1/traces", params={"keyword": "t-ing-o"}).json()["total"] == 1

    def test_pagination(self, client):
        body = client.get("/api/v1/traces", params={"limit": 2, "offset": 2}).json()
        assert body["total"] == 3
        assert [t["trace_id"] for t in body["items"]] == ["t-ing-old"]

    def test_empty_when_no_trace_file(self, empty_client):
        assert empty_client.get("/api/v1/traces").json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_summary_derived_fields(self, client):
        items = {t["trace_id"]: t for t in client.get("/api/v1/traces").json()["items"]}
        ing = items["t-ing"]
        assert ing["source_path"] == "/docs/guide.pdf"
        assert ing["collection"] == "docs"
        assert ing["skipped"] is False
        assert ing["chunks"] == 7 and ing["images"] == 1
        assert ing["stage_count"] == 4
        assert items["t-ing-old"]["skipped"] is True
        assert items["t-ing-old"]["source_path"] == "/docs/old.md"
        qry = items["t-query"]
        assert qry["query"] == "How does RRF work?"
        assert qry["skipped"] is None and qry["chunks"] is None

    def test_detail_ingestion(self, client):
        body = client.get("/api/v1/traces/t-ing").json()
        assert body["trace_id"] == "t-ing"
        assert [s["stage"] for s in body["stages"]] == ["integrity", "load", "split", "store"]
        assert body["stages"][2]["details"] == {"chunks": 7}
        assert body["stages"][1]["method"] == "pipeline"
        # 瀑布图只含 ingestion 约定阶段（integrity 不在其中），且同名阶段已累加
        assert body["timings"] == [
            {"stage": "load", "elapsed_ms": 10.0},
            {"stage": "split", "elapsed_ms": 5.0},
            {"stage": "store", "elapsed_ms": 40.0},
        ]

    def test_detail_query_keeps_stage_details(self, client):
        body = client.get("/api/v1/traces/t-query").json()
        assert body["query"] == "How does RRF work?"
        dense = body["stages"][0]
        assert dense["details"]["preview"][0]["chunk_id"] == "c1"
        assert [t["stage"] for t in body["timings"]] == ["dense_retrieval", "rerank"]

    def test_detail_falls_back_to_generic_timings(self, settings, tmp_path):
        trace = {"trace_id": "t-x", "trace_type": "query", "metadata": {},
                 "stages": [{"stage": "custom_stage", "elapsed_ms": 4.0, "method": "m"}]}
        client = make_client(settings, traces_path=_write_traces(tmp_path / "t.jsonl", [trace]))
        body = client.get("/api/v1/traces/t-x").json()
        assert body["timings"] == [{"stage": "custom_stage", "elapsed_ms": 4.0}]
        assert body["stages"][0]["details"] == {}

    def test_detail_missing_is_404(self, client, empty_client):
        assert_error(client.get("/api/v1/traces/nope"), 404, "not_found")
        assert_error(empty_client.get("/api/v1/traces/nope"), 404, "not_found")


# --------------------------------------------------------------------------- #
# unified error format
# --------------------------------------------------------------------------- #
class TestErrorFormat:
    def test_unknown_route(self, client):
        assert_error(client.get("/api/v1/does-not-exist"), 404, "not_found")

    def test_method_not_allowed(self, client):
        assert_error(client.post("/api/v1/overview"), 405, "method_not_allowed")

    def test_unhandled_exception_is_generic_500(self, settings):
        class Boom(FakeData):
            def get_collection_stats(self, collection=None):
                raise RuntimeError("secret-internal-detail")

        client = make_client(settings, data=Boom())
        response = client.get("/api/v1/collections")
        error = assert_error(response, 500, "internal_error")
        assert error["details"] is None
        assert "secret-internal-detail" not in response.text

    def test_500_still_carries_security_headers(self, settings):
        class Boom(FakeData):
            def get_collection_stats(self, collection=None):
                raise RuntimeError("x")

        response = make_client(settings, data=Boom()).get("/api/v1/collections")
        assert response.status_code == 500
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_500_carries_cors_headers_for_allowed_origin(self, settings):
        class Boom(FakeData):
            def get_collection_stats(self, collection=None):
                raise RuntimeError("x")

        response = make_client(settings, data=Boom()).get(
            "/api/v1/collections", headers={"Origin": "http://localhost:3000"}
        )
        assert response.status_code == 500
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


# --------------------------------------------------------------------------- #
# auth matrix
# --------------------------------------------------------------------------- #
class TestAuth:
    def test_loopback_without_token_is_open(self, client):
        assert client.get("/api/v1/overview").status_code == 200

    def test_unresolved_placeholder_means_open_on_loopback(self, settings):
        client = make_client(settings, api=ApiSettings(api_token="${RAG_API_TOKEN}"))
        assert client.get("/api/v1/collections").status_code == 200
        # 字面量占位符不能被当作 token 使用
        wrong = client.get("/api/v1/collections", headers={"Authorization": "Bearer ${RAG_API_TOKEN}"})
        assert wrong.status_code == 200  # 未配置 token 时不校验 header

    def test_unresolved_placeholder_is_not_a_valid_token_when_real_token_set(self, settings):
        client = make_client(settings, api=ApiSettings(api_token=TOKEN))
        response = client.get("/api/v1/collections", headers={"Authorization": "Bearer ${RAG_API_TOKEN}"})
        assert response.status_code == 401

    def test_token_required(self, settings, data):
        client = make_client(settings, data=data, api=ApiSettings(api_token=TOKEN))
        missing = client.get("/api/v1/collections")
        error = assert_error(missing, 401, "unauthorized")
        assert missing.headers["www-authenticate"] == "Bearer"
        assert TOKEN not in json.dumps(error)

    @pytest.mark.parametrize(
        "headers",
        [
            {"Authorization": "Bearer wrong"},
            {"Authorization": f"Basic {TOKEN}"},
            {"Authorization": TOKEN},
            {"Authorization": "Bearer "},
            {"X-API-Token": TOKEN},
        ],
    )
    def test_wrong_credentials_rejected(self, settings, headers):
        client = make_client(settings, api=ApiSettings(api_token=TOKEN))
        assert_error(client.get("/api/v1/collections", headers=headers), 401, "unauthorized")

    def test_correct_token_accepted(self, settings, data):
        client = make_client(settings, data=data, api=ApiSettings(api_token=TOKEN))
        assert client.get("/api/v1/collections", headers=AUTH).status_code == 200

    def test_non_ascii_header_does_not_crash(self, settings):
        client = make_client(settings, api=ApiSettings(api_token=TOKEN))
        response = client.get("/api/v1/collections", headers={"Authorization": "Bearer tök".encode("latin-1")})
        assert response.status_code == 401

    @pytest.mark.parametrize("path", V1_GET_PATHS)
    def test_every_v1_route_is_protected(self, settings, data, traces_path, path):
        client = make_client(settings, data=data, traces_path=traces_path, api=ApiSettings(api_token=TOKEN))
        assert_error(client.get(path), 401, "unauthorized")
        assert client.get(path, headers=AUTH).status_code in (200, 404)

    def test_non_loopback_without_token_fails_at_startup(self, settings):
        with pytest.raises(ConfigError, match="RAG_API_TOKEN"):
            make_app(settings, api=ApiSettings(host="0.0.0.0"))

    def test_non_loopback_with_placeholder_fails_at_startup(self, settings):
        with pytest.raises(ConfigError):
            make_app(settings, api=ApiSettings(host="0.0.0.0", api_token="${RAG_API_TOKEN}"))

    def test_non_loopback_with_token_starts(self, settings):
        client = TestClient(make_app(settings, api=ApiSettings(host="0.0.0.0", api_token=TOKEN)))
        assert client.get("/health").status_code == 200

    def test_create_app_from_settings_object(self, settings, monkeypatch):
        monkeypatch.delenv("RAG_API_TOKEN", raising=False)
        app = create_app(replace(settings, api=ApiSettings(api_token=TOKEN)))
        assert app.state.api_token == TOKEN
        bad = replace(settings, api=ApiSettings(host="0.0.0.0"))
        with pytest.raises(ConfigError):
            create_app(bad)

    def test_create_app_does_not_touch_storage(self, settings):
        # 不注入 services：容器懒构建，create_app 本身零 IO
        local = replace(settings, api=ApiSettings())
        app = create_app(local)
        container = app.state.container
        assert container.settings is local
        assert container._data is None and container._traces is None and container._config is None


# --------------------------------------------------------------------------- #
# CORS / security headers
# --------------------------------------------------------------------------- #
class TestCorsAndHeaders:
    def test_allowed_origin_is_echoed(self, client):
        response = client.get("/api/v1/overview", headers={"Origin": "http://localhost:3000"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"

    def test_unlisted_origin_gets_no_cors_headers(self, client):
        response = client.get("/api/v1/overview", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in response.headers

    def test_custom_allowlist(self, settings):
        client = make_client(settings, api=ApiSettings(allowed_origins=["https://ui.example"]))
        ok = client.get("/health", headers={"Origin": "https://ui.example"})
        assert ok.headers["access-control-allow-origin"] == "https://ui.example"
        default = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert "access-control-allow-origin" not in default.headers

    def test_preflight_succeeds_without_token(self, settings):
        client = make_client(settings, api=ApiSettings(api_token=TOKEN))
        response = client.options(
            "/api/v1/overview",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert "authorization" in response.headers["access-control-allow-headers"].lower()

    def test_preflight_from_unlisted_origin_rejected(self, client):
        response = client.options(
            "/api/v1/overview",
            headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
        )
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers

    def test_security_headers_on_success_and_errors(self, settings):
        client = make_client(settings, api=ApiSettings(api_token=TOKEN))
        for response in (
            client.get("/health"),
            client.get("/api/v1/overview", headers=AUTH),
            client.get("/api/v1/overview"),  # 401
            client.get("/nope"),  # 404
        ):
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["referrer-policy"] == "no-referrer"


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
class TestHealth:
    def test_open_even_when_token_configured(self, settings):
        client = make_client(settings, api=ApiSettings(api_token=TOKEN))
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] in ("ok", "degraded")

    def test_sidecar_disabled_when_agentic_off(self, settings):
        body = make_client(settings).get("/health").json()
        assert body["sidecar"] == {"status": "disabled", "enabled": False}

    def test_all_ok(self, settings):
        client = make_client(
            settings, sidecar_probe=lambda: True, storage_probe=lambda: {"vector_store": True, "traces": True}
        )
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["sidecar"]["status"] == "ok"
        assert body["storage"] == {"status": "ok", "components": {"vector_store": True, "traces": True}}

    def test_sidecar_unreachable_degrades_but_never_fails(self, settings):
        client = make_client(settings, sidecar_probe=lambda: False)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["sidecar"]["status"] == "unreachable"
        assert response.json()["status"] == "degraded"

    def test_probe_exceptions_never_propagate(self, settings):
        def boom():
            raise RuntimeError("probe exploded: secret")

        client = make_client(settings, sidecar_probe=boom, storage_probe=boom)
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["sidecar"]["status"] == "unreachable"
        assert body["storage"]["status"] == "degraded"
        assert body["status"] == "degraded"
        assert "secret" not in response.text

    def test_storage_degraded_when_component_missing(self, settings):
        client = make_client(settings, storage_probe=lambda: {"vector_store": False, "traces": True})
        body = client.get("/health").json()
        assert body["storage"]["status"] == "degraded"
        assert body["status"] == "degraded"

    def test_default_storage_probe_reads_filesystem_only(self, settings, tmp_path):
        local = replace(
            settings,
            vector_store=replace(settings.vector_store, persist_path=str(tmp_path / "chroma")),
            observability=replace(settings.observability, log_file=str(tmp_path / "logs" / "t.jsonl")),
        )
        container = ServiceContainer(local)
        assert container.storage_health()["status"] == "degraded"
        (tmp_path / "chroma").mkdir()
        (tmp_path / "logs").mkdir()
        assert container.storage_health() == {
            "status": "ok",
            "components": {"vector_store": True, "traces": True},
        }

    def test_default_sidecar_probe_used_when_agentic_enabled(self, settings, monkeypatch):
        calls = []
        monkeypatch.setattr("api.deps.probe_sidecar", lambda url: calls.append(url) or True)
        agentic = replace(settings.agentic_search, enabled=True, allowed_roots=["/tmp"])
        container = ServiceContainer(replace(settings, agentic_search=agentic))
        assert container.sidecar_health() == {"status": "ok", "enabled": True}
        assert calls == [agentic.base_url]


# --------------------------------------------------------------------------- #
# OpenAPI
# --------------------------------------------------------------------------- #
class TestOpenApi:
    def test_schema_generates_and_lists_routes(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["openapi"].startswith("3.")
        paths = schema["paths"]
        for expected in (
            "/health",
            "/api/v1/overview",
            "/api/v1/collections",
            "/api/v1/collections/{name}",
            "/api/v1/documents",
            "/api/v1/documents/{document_id}",
            "/api/v1/images",
            "/api/v1/images/{image_id}",
            "/api/v1/traces",
            "/api/v1/traces/{trace_id}",
        ):
            assert expected in paths, expected
            assert list(paths[expected]) == ["get"]  # WC2 只读：没有写方法

    def test_schema_documents_models_and_errors(self, client):
        schema = client.get("/openapi.json").json()
        models = schema["components"]["schemas"]
        for name in ("OverviewResponse", "DocumentDetailModel", "TraceDetail", "ErrorResponse", "HealthResponse"):
            assert name in models, name
        get_trace = schema["paths"]["/api/v1/traces/{trace_id}"]["get"]
        assert "404" in get_trace["responses"] and "401" in get_trace["responses"]
        assert "HTTPBearer" in schema["components"]["securitySchemes"]
        assert "security" not in schema["paths"]["/health"]["get"]
        assert schema["paths"]["/api/v1/overview"]["get"]["security"]

    def test_docs_are_served(self, client):
        assert client.get("/docs").status_code == 200
