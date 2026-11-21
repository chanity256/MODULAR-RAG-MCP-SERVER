"""AC5: 同名上传 409 防护单元测试（AGENTIC_COLLECTION_ISOLATION_SPEC，离线）。

覆盖 spec §4 AC5 五组：被引用且内容不同 → 冲突且磁盘不变、同哈希幂等、
孤儿覆盖放行、批量一个冲突整批拒绝且零写入、RA3 前相对记录识别；
另含路由级 409 响应体（TestClient + create_app 注入真实 IngestionService）。
AR2 删除联动既有用例（``test_upload_source_cleanup.py``）零改动。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import ServiceContainer
from api.errors import ApiError
from api.routes.ingestion import _save_with_name_guard
from api.tasks import TaskManager
from core.settings import ApiSettings
from observability.dashboard.services.ingestion_service import IngestionService

pytestmark = pytest.mark.unit

TOKEN = "upload-guard-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeHistory:
    """fake 历史表句柄（``list_processed()`` 契约）。"""

    def __init__(self, records: list[dict]) -> None:
        self._records = records

    def list_processed(self) -> list[dict]:
        return list(self._records)


def _rec(source_path: str, collection: str) -> dict:
    return {"source_path": source_path, "collection": collection, "status": "success"}


@pytest.fixture()
def scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """项目根 + 上传目录布局（RAG_PROJECT_ROOT 锚定，覆盖路由级默认解析）。"""
    root = tmp_path / "proj"
    uploads = root / "data" / "uploads"
    uploads.mkdir(parents=True)
    monkeypatch.setenv("RAG_PROJECT_ROOT", str(root))
    return {"root": root, "uploads": uploads}


def _service(scope: dict, records: list[dict]) -> IngestionService:
    return IngestionService(upload_dir=scope["uploads"], history=FakeHistory(records))


class TestConflictSemantics:
    def test_referenced_name_with_different_content_conflicts(self, scope):
        """AC5-1：被引用且内容不同 → 冲突、磁盘文件不变、409 语义正确。"""
        (scope["uploads"] / "x.md").write_bytes(b"old")
        service = _service(scope, [_rec(str(scope["uploads"] / "x.md"), "A")])
        assert service.upload_conflicts([("x.md", b"new")]) == [
            {"name": "x.md", "collections": ["A"]}
        ]
        assert (scope["uploads"] / "x.md").read_bytes() == b"old"  # 预检不落盘
        with pytest.raises(ApiError) as excinfo:
            _save_with_name_guard(service, [("x.md", b"new")])
        assert excinfo.value.status_code == 409
        assert excinfo.value.code == "upload_name_in_use"
        assert excinfo.value.details == {"conflicts": [{"name": "x.md", "collections": ["A"]}]}

    def test_multiple_collections_listed_sorted(self, scope):
        (scope["uploads"] / "x.md").write_bytes(b"old")
        service = _service(
            scope,
            [_rec(str(scope["uploads"] / "x.md"), "B"), _rec(str(scope["uploads"] / "x.md"), "A")],
        )
        assert service.upload_conflicts([("x.md", b"new")]) == [
            {"name": "x.md", "collections": ["A", "B"]}
        ]

    def test_same_hash_is_idempotent(self, scope):
        """AC5-2：同哈希重传幂等放行（重写相同字节）。"""
        (scope["uploads"] / "x.md").write_bytes(b"same-bytes")
        service = _service(scope, [_rec(str(scope["uploads"] / "x.md"), "A")])
        assert service.upload_conflicts([("x.md", b"same-bytes")]) == []
        saved = _save_with_name_guard(service, [("x.md", b"same-bytes")])
        assert saved[0].read_bytes() == b"same-bytes"

    def test_orphan_file_overwrite_allowed(self, scope):
        """AC5-3：无历史引用（孤儿文件）→ 现状覆盖放行。"""
        (scope["uploads"] / "orphan.md").write_bytes(b"old")
        service = _service(scope, [_rec(str(scope["uploads"] / "other.md"), "A")])
        assert service.upload_conflicts([("orphan.md", b"new")]) == []
        saved = _save_with_name_guard(service, [("orphan.md", b"new")])
        assert saved[0].read_bytes() == b"new"

    def test_batch_conflict_rejects_everything_without_writes(self, scope):
        """AC5-4：批量一个冲突 → 整批拒绝，批内所有文件均未写入。"""
        (scope["uploads"] / "a.md").write_bytes(b"old-a")
        service = _service(scope, [_rec(str(scope["uploads"] / "a.md"), "A")])
        with pytest.raises(ApiError) as excinfo:
            _save_with_name_guard(service, [("a.md", b"new-a"), ("fresh.md", b"brand-new")])
        assert (scope["uploads"] / "a.md").read_bytes() == b"old-a"  # 冲突文件未变
        assert not (scope["uploads"] / "fresh.md").exists()  # 不冲突的新文件也未写入
        assert excinfo.value.details == {"conflicts": [{"name": "a.md", "collections": ["A"]}]}

    def test_relative_legacy_record_recognized(self, scope):
        """AC5-5：RA3 前相对形式 ``data/uploads/<name>`` 同样识别为引用。"""
        (scope["uploads"] / "x.md").write_bytes(b"old")
        service = _service(scope, [_rec("data/uploads/x.md", "B")])
        conflicts = service.upload_conflicts([("x.md", b"new")], project_root=scope["root"])
        assert conflicts == [{"name": "x.md", "collections": ["B"]}]


class TestRouteResponse:
    def test_upload_route_returns_409_and_idempotent_201(self, scope, tmp_path):
        """路由级：409 统一错误体 + 同哈希重传幂等 201。"""
        (scope["uploads"] / "x.md").write_bytes(b"old")
        ingestion = _service(scope, [_rec(str(scope["uploads"] / "x.md"), "A")])
        container = ServiceContainer(
            ingestion_service=ingestion,
            data_service=SimpleNamespace(delete_document=lambda *a, **k: None),
            evaluation_service=SimpleNamespace(),
            task_manager=TaskManager(tmp_path / "tasks.db", max_workers=1),
        )
        app = create_app(
            services=container, api_settings=ApiSettings(api_token=TOKEN, max_upload_mb=1)
        )
        with TestClient(app) as client:
            conflict = client.post(
                "/api/v1/uploads",
                files=[("files", ("x.md", b"new", "text/plain"))],
                headers=AUTH,
            )
            assert conflict.status_code == 409
            body = conflict.json()
            assert body["success"] is False
            assert body["error"]["code"] == "upload_name_in_use"
            assert body["error"]["details"] == {
                "conflicts": [{"name": "x.md", "collections": ["A"]}]
            }
            assert (scope["uploads"] / "x.md").read_bytes() == b"old"

            idempotent = client.post(
                "/api/v1/uploads",
                files=[("files", ("x.md", b"old", "text/plain"))],
                headers=AUTH,
            )
            assert idempotent.status_code == 201
            assert idempotent.json()["files"][0]["name"] == "x.md"

            fresh = client.post(
                "/api/v1/uploads",
                files=[("files", ("fresh.md", b"brand-new", "text/plain"))],
                headers=AUTH,
            )
            assert fresh.status_code == 201
            assert (scope["uploads"] / "fresh.md").read_bytes() == b"brand-new"
