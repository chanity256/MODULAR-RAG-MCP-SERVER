"""PI2 sidecar server 单测（fake adapter 注入，规格 §4 PI2 行）。

覆盖：鉴权 401、各端点 schema、`/index` render 错误前缀、`/index/delete`、
`/search` 空列表 fail-closed 不调 adapter、未索引路径 unindexed、越界证据
过滤、TreeEvidence 字段裁剪、loopback 绑定校验；模块导入不触发
pageindex / litellm（断言 sys.modules）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PAGEINDEX_DIR = Path(__file__).resolve().parents[2] / "scripts" / "pageindex"
sys.path.insert(0, str(PAGEINDEX_DIR))

import server as sidecar_server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from md_render import RenderFailed  # noqa: E402
from server import create_app, ensure_loopback, envelope  # noqa: E402

TOKEN = "test-token"


@dataclass
class FakeLoopResult:
    evidences: list = field(default_factory=list)
    llm_call_count: int = 0
    total_llm_tokens: int = 0
    telemetry: dict = field(default_factory=dict)


class FakeAdapter:
    """按 pi_adapter 接口形状的 fake（server 只依赖该方法面）。"""

    def __init__(self):
        self.records = [{
            "source_path": "/repo/data/documents/a.md", "file_hash": "h1",
            "format": "md", "doc_id": "d1", "node_count": 10,
            "indexed_at": "2026-11-23T00:00:00", "derived_pdf": "/db/derived/h1.pdf",
        }]
        self.index_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []
        self.search_calls: list[tuple] = []
        self.index_error: Exception | None = None
        self.search_error: Exception | None = None
        self.search_result = FakeLoopResult(
            evidences=[{
                "source_path": "/repo/data/documents/a.md", "doc_id": "d1",
                "node_id": "0003", "text": "证据文本", "score": 1.0,
                "section": "A > B", "page_start": None, "page_end": None,
                "line_start": 12, "line_end": 45, "truncated": False,
            }],
            llm_call_count=3, total_llm_tokens=8400,
            telemetry={"unindexed": 1})

    def status(self):
        return {"version": "1.0", "pageindex_version": "0.7.4",
                "model": "openai/glm-5.3-flash", "documents": len(self.records)}

    def documents(self):
        return list(self.records)

    def index(self, source_path: str, file_hash: str):
        if self.index_error:
            raise self.index_error
        self.index_calls.append((source_path, file_hash))
        return {"doc_id": "d9", "format": "md", "node_count": 4,
                "llm_tokens": 1234, "render_ms": 9000.5, "derived_pages": 306}

    def delete(self, source_path: str):
        self.delete_calls.append(source_path)
        return {"removed": source_path in {r["source_path"] for r in self.records}}

    def search(self, query, source_paths, *, max_nodes, max_loops, max_token_budget):
        if self.search_error:
            raise self.search_error
        self.search_calls.append((query, source_paths, max_nodes, max_loops,
                                  max_token_budget))
        return self.search_result


def client_for(adapter: FakeAdapter) -> TestClient:
    return TestClient(create_app(adapter, TOKEN))


def auth(token: str = TOKEN):
    return {"Authorization": f"Bearer {token}"}


# -- 鉴权 --------------------------------------------------------------------


def test_missing_token_is_401():
    adapter = FakeAdapter()
    client = client_for(adapter)
    assert client.get("/api/v1/status").status_code == 401
    assert client.post("/api/v1/search", json={"query": "q"}).status_code == 401


def test_wrong_token_is_401():
    client = client_for(FakeAdapter())
    assert client.get("/api/v1/status", headers=auth("wrong")).status_code == 401
    # 非 Bearer scheme 同样拒绝
    assert client.get("/api/v1/status",
                      headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401


def test_create_app_rejects_empty_token():
    with pytest.raises(ValueError):
        create_app(FakeAdapter(), "")


# -- 端点 schema ---------------------------------------------------------------


def test_status_envelope():
    data = client_for(FakeAdapter()).get("/api/v1/status", headers=auth()).json()
    assert set(data) == {"success", "data", "error"}
    assert data["success"] is True and data["error"] is None
    assert data["data"] == {"version": "1.0", "pageindex_version": "0.7.4",
                            "model": "openai/glm-5.3-flash", "documents": 1}


def test_documents_envelope():
    data = client_for(FakeAdapter()).get("/api/v1/documents", headers=auth()).json()
    assert data["success"] is True
    assert data["data"][0]["derived_pdf"] == "/db/derived/h1.pdf"  # 仅 MD


def test_index_request_validation():
    adapter = FakeAdapter()
    client = client_for(adapter)
    missing = client.post("/api/v1/index", headers=auth(),
                          json={"source_path": "/x.md"}).json()
    assert missing["success"] is False and "required" in missing["error"]
    relative = client.post("/api/v1/index", headers=auth(),
                           json={"source_path": "rel/a.md", "file_hash": "h"}).json()
    assert relative["success"] is False and "absolute" in relative["error"]
    assert adapter.index_calls == []


def test_index_success_passthrough():
    adapter = FakeAdapter()
    data = client_for(adapter).post(
        "/api/v1/index", headers=auth(),
        json={"source_path": "/repo/data/documents/b.md", "file_hash": "h2"}).json()
    assert data == {"success": True, "error": None,
                    "data": {"doc_id": "d9", "format": "md", "node_count": 4,
                             "llm_tokens": 1234, "render_ms": 9000.5,
                             "derived_pages": 306}}
    assert adapter.index_calls == [("/repo/data/documents/b.md", "h2")]


def test_index_render_error_uses_render_prefix():
    adapter = FakeAdapter()
    adapter.index_error = RenderFailed("pandoc failed (rc=1)")
    data = client_for(adapter).post(
        "/api/v1/index", headers=auth(),
        json={"source_path": "/repo/data/documents/a.md", "file_hash": "h1"}).json()
    assert data["success"] is False
    assert data["error"].startswith("render: ")


def test_index_unexpected_error_is_envelope_not_stack():
    adapter = FakeAdapter()
    adapter.index_error = RuntimeError("boom")
    data = client_for(adapter).post(
        "/api/v1/index", headers=auth(),
        json={"source_path": "/repo/data/documents/a.md", "file_hash": "h1"}).json()
    assert data["success"] is False and "RuntimeError" in data["error"]


def test_index_delete_returns_removed():
    adapter = FakeAdapter()
    client = client_for(adapter)
    ok = client.post("/api/v1/index/delete", headers=auth(),
                     json={"source_path": "/repo/data/documents/a.md"}).json()
    assert ok == {"success": True, "error": None, "data": {"removed": True}}
    miss = client.post("/api/v1/index/delete", headers=auth(),
                       json={"source_path": "/nope.md"}).json()
    assert miss["data"] == {"removed": False}
    no_body = client.post("/api/v1/index/delete", headers=auth(), json={}).json()
    assert no_body["success"] is False


# -- /search -------------------------------------------------------------------


def test_search_empty_source_paths_fails_closed_without_calling_adapter():
    adapter = FakeAdapter()
    data = client_for(adapter).post(
        "/api/v1/search", headers=auth(),
        json={"query": "q", "source_paths": []}).json()
    assert data["success"] is True
    assert data["data"]["evidences"] == []
    assert data["data"]["llm_call_count"] == 0
    assert data["data"]["telemetry"] == {"fail_closed": True}
    assert adapter.search_calls == []  # 不调用 adapter 检索（纵深防御）


def test_search_passes_nonempty_list_and_trims_evidence_fields():
    adapter = FakeAdapter()
    data = client_for(adapter).post(
        "/api/v1/search", headers=auth(),
        json={"query": "q", "source_paths": ["/repo/data/documents/a.md"],
              "max_nodes": 5, "max_loops": 3, "max_token_budget": 32000}).json()
    assert adapter.search_calls == [(
        "q", ["/repo/data/documents/a.md"], 5, 3, 32000)]
    assert data["success"] is True
    ev = data["data"]["evidences"][0]
    assert set(ev) == {"source_path", "doc_id", "node_id", "text", "score",
                       "section", "page_start", "page_end", "line_start",
                       "line_end"}  # truncated 被裁剪
    assert ev["line_start"] == 12 and ev["page_start"] is None


def test_search_null_source_paths_passes_none():
    adapter = FakeAdapter()
    client_for(adapter).post("/api/v1/search", headers=auth(),
                             json={"query": "q", "source_paths": None})
    assert adapter.search_calls[0][1] is None


def test_search_filters_out_of_scope_evidence():
    adapter = FakeAdapter()
    adapter.search_result.evidences.append({
        "source_path": "/repo/data/other.md", "doc_id": "d2", "node_id": "0009",
        "text": "越界", "score": 0.5, "section": "X", "page_start": None,
        "page_end": None, "line_start": None, "line_end": None})
    data = client_for(adapter).post(
        "/api/v1/search", headers=auth(),
        json={"query": "q", "source_paths": ["/repo/data/documents/a.md"]}).json()
    paths = [e["source_path"] for e in data["data"]["evidences"]]
    assert paths == ["/repo/data/documents/a.md"]  # 越界证据被过滤
    assert data["data"]["telemetry"]["out_of_scope_dropped"] == 1
    assert data["data"]["telemetry"]["unindexed"] == 1  # adapter 计数透传


def test_search_request_validation():
    adapter = FakeAdapter()
    client = client_for(adapter)
    bad_query = client.post("/api/v1/search", headers=auth(),
                            json={"query": "  "}).json()
    assert bad_query["success"] is False
    bad_paths = client.post("/api/v1/search", headers=auth(),
                            json={"query": "q", "source_paths": [1, 2]}).json()
    assert bad_paths["success"] is False
    assert adapter.search_calls == []


def test_search_adapter_error_is_envelope():
    adapter = FakeAdapter()
    adapter.search_error = RuntimeError("upstream broke")
    data = client_for(adapter).post("/api/v1/search", headers=auth(),
                                    json={"query": "q"}).json()
    assert data["success"] is False and "RuntimeError" in data["error"]


# -- 绑定与信封 -----------------------------------------------------------------


def test_ensure_loopback():
    assert ensure_loopback("127.0.0.1") == "127.0.0.1"
    assert ensure_loopback("localhost") == "localhost"
    assert ensure_loopback("[::1]") == "::1"
    with pytest.raises(ValueError):
        ensure_loopback("0.0.0.0")
    with pytest.raises(ValueError):
        ensure_loopback("192.168.1.5")


def test_envelope_shape():
    assert envelope(True, {"a": 1}) == {"success": True, "data": {"a": 1},
                                        "error": None}
    assert envelope(False, error="x") == {"success": False, "data": None,
                                          "error": "x"}


def test_import_does_not_pull_upstream_modules():
    """server 导入链零 pageindex / litellm（sidecar 红线的 server 侧保证）。"""
    assert "pageindex" not in sys.modules
    assert "litellm" not in sys.modules
    assert sidecar_server.API_VERSION
