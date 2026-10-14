"""写接口、任务模型与 WS 任务事件集成测试（WC4.2 / WC4.3）。

全部离线：fake 摄取/评估/数据服务经 ``ServiceContainer`` 注入，任务库与上传目录都在 tmp。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from api.deps import ServiceContainer
from api.tasks import TaskManager
from core.settings import ApiSettings
from ingestion.document_manager import DeleteResult
from observability.dashboard.services.ingestion_service import IngestionService, IngestionSummary

TOKEN = "write-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WS_BASE = "/api/v1/tasks/{}/events"


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakeIngestion(IngestionService):
    """真实的上传落盘 + 可控的 ingest 行为。"""

    def __init__(self, upload_dir: Path) -> None:
        super().__init__(upload_dir=upload_dir)
        self.gate: threading.Event | None = None
        self.behavior = None
        self.calls: list[dict] = []

    def ingest(self, files, *, collection, force=False, on_progress=None):
        paths = [Path(p) for p in files]
        self.calls.append({"files": [p.name for p in paths], "collection": collection, "force": force})
        if on_progress:
            on_progress("load", 1, 2)
        if self.gate is not None:
            assert self.gate.wait(10), "test gate never released"
        if on_progress:
            on_progress("embed", 2, 2)
        if self.behavior is not None:
            return self.behavior(paths)
        return IngestionSummary(files=len(paths), chunks=3 * len(paths), images=0, failed=0)


class FakeData:
    def __init__(self) -> None:
        self.results: dict[tuple[str, str], DeleteResult] = {}
        self.calls: list[tuple[str, str]] = []

    def delete_document(self, source_path, collection):
        self.calls.append((source_path, collection))
        if not source_path:
            raise ValueError("source_path must be a non-empty string")
        return self.results.get((source_path, collection), DeleteResult(source_path, collection))


class FakeReport:
    def __init__(self, metrics: dict[str, float]) -> None:
        self._metrics = metrics

    def to_dict(self):
        return {"total": 2, "evaluated": 2, "failed": 0, "metrics": self._metrics, "per_query": []}


class FakeEvaluation:
    backends = ("composite", "custom", "ragas")

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.gate: threading.Event | None = None
        self.error: Exception | None = None

    def default_test_set_path(self):
        return Path("/golden/default.json")

    def run(self, test_set_path, *, backend="composite", top_k=None, filters=None):
        self.calls.append(
            {"path": str(test_set_path), "backend": backend, "top_k": top_k, "filters": filters}
        )
        if self.gate is not None:
            assert self.gate.wait(10), "test gate never released"
        if self.error is not None:
            raise self.error
        return FakeReport({"hit_rate": 0.5})


@pytest.fixture
def env(tmp_path):
    ingestion = FakeIngestion(tmp_path / "uploads")
    data = FakeData()
    evaluation = FakeEvaluation()
    manager = TaskManager(tmp_path / "tasks.db", max_workers=2)
    container = ServiceContainer(
        ingestion_service=ingestion,
        data_service=data,  # type: ignore[arg-type]
        evaluation_service=evaluation,
        task_manager=manager,
    )
    api_settings = ApiSettings(api_token=TOKEN, max_upload_mb=1)
    app = create_app(services=container, api_settings=api_settings)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            ingestion=ingestion,
            data=data,
            evaluation=evaluation,
            manager=manager,
            app=app,
            tmp=tmp_path,
        )
    manager.shutdown(wait=True)


def _upload(client, files, headers=AUTH):
    return client.post("/api/v1/uploads", files=files, headers=headers)


def _poll(client, task_id, *, until=("succeeded", "failed"), timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/tasks/{task_id}", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        if body["status"] in until:
            return body
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {until}")


def _submit_ingestion(client, files=("a.md",), collection="default", **extra):
    return client.post(
        "/api/v1/ingestions",
        json={"files": list(files), "collection": collection, **extra},
        headers=AUTH,
    )


# --------------------------------------------------------------------------- #
# uploads
# --------------------------------------------------------------------------- #
class TestUploads:
    def test_upload_saves_files(self, env):
        response = _upload(env.client, [("files", ("a.md", b"# A", "text/markdown")), ("files", ("b.txt", b"bb", "text/plain"))])
        assert response.status_code == 201
        assert response.json() == {"files": [{"name": "a.md", "size": 3}, {"name": "b.txt", "size": 2}]}
        assert (env.ingestion.upload_dir / "a.md").read_bytes() == b"# A"
        assert (env.ingestion.upload_dir / "b.txt").read_bytes() == b"bb"

    @pytest.mark.parametrize("name", ["../evil.txt", "..\\evil.txt", "sub/dir.txt", "/etc/passwd", ".."])
    def test_path_traversal_names_rejected(self, env, name):
        response = _upload(env.client, [("files", (name, b"x", "text/plain"))])
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_filename"
        assert not list(env.tmp.rglob("evil.txt"))
        assert not env.ingestion.upload_dir.exists() or not any(env.ingestion.upload_dir.iterdir())

    def test_bad_name_in_batch_saves_nothing(self, env):
        response = _upload(env.client, [("files", ("ok.md", b"x", "text/plain")), ("files", ("../bad", b"x", "text/plain"))])
        assert response.status_code == 400
        assert not (env.ingestion.upload_dir / "ok.md").exists()

    def test_oversized_file_rejected(self, env):
        big = b"x" * (1024 * 1024 + 1)
        response = _upload(env.client, [("files", ("big.bin", big, "application/octet-stream"))])
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"
        assert not (env.ingestion.upload_dir / "big.bin").exists()

    def test_file_at_limit_accepted(self, env):
        data = b"x" * (1024 * 1024)
        assert _upload(env.client, [("files", ("edge.bin", data, "application/octet-stream"))]).status_code == 201

    def test_missing_files_field_is_422(self, env):
        response = env.client.post("/api/v1/uploads", headers=AUTH)
        assert response.status_code == 422
        assert response.json()["success"] is False


# --------------------------------------------------------------------------- #
# ingestion tasks
# --------------------------------------------------------------------------- #
class TestIngestionTasks:
    def test_submit_poll_succeeded(self, env):
        _upload(env.client, [("files", ("a.md", b"# A", "text/markdown"))])
        response = _submit_ingestion(env.client, ["a.md"], collection="docs", force=True)
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending" and body["task"]["kind"] == "ingestion"

        final = _poll(env.client, body["task_id"])
        assert final["status"] == "succeeded"
        assert final["detail"]["params"] == {"files": ["a.md"], "collection": "docs", "force": True}
        assert final["detail"]["result"]["files"] == 1
        assert final["detail"]["result"]["chunks"] == 3
        assert final["detail"]["progress"] == {"stage": "embed", "current": 2, "total": 2}
        assert final["started_at"] and final["finished_at"]
        assert env.ingestion.calls == [{"files": ["a.md"], "collection": "docs", "force": True}]

    def test_progress_visible_while_running(self, env):
        _upload(env.client, [("files", ("a.md", b"x", "text/plain"))])
        env.ingestion.gate = threading.Event()
        task_id = _submit_ingestion(env.client).json()["task_id"]
        try:
            running = _poll(env.client, task_id, until=("running",))
            deadline = time.monotonic() + 5
            while "progress" not in running["detail"] and time.monotonic() < deadline:
                time.sleep(0.02)
                running = env.client.get(f"/api/v1/tasks/{task_id}", headers=AUTH).json()
            assert running["detail"]["progress"]["stage"] == "load"
        finally:
            env.ingestion.gate.set()
        assert _poll(env.client, task_id)["status"] == "succeeded"

    def test_pipeline_exception_marks_task_failed(self, env):
        _upload(env.client, [("files", ("a.md", b"x", "text/plain"))])

        def boom(paths):
            raise RuntimeError("pipeline exploded")

        env.ingestion.behavior = boom
        final = _poll(env.client, _submit_ingestion(env.client).json()["task_id"])
        assert final["status"] == "failed"
        assert final["detail"]["error"] == {"type": "RuntimeError", "message": "pipeline exploded"}

    def test_all_files_failed_is_failed_with_result(self, env):
        _upload(env.client, [("files", ("a.md", b"x", "text/plain"))])
        env.ingestion.behavior = lambda paths: IngestionSummary(
            files=1, failed=1, errors=[("a.md", "ValueError: bad")]
        )
        final = _poll(env.client, _submit_ingestion(env.client).json()["task_id"])
        assert final["status"] == "failed"
        assert final["detail"]["result"]["errors"] == [{"path": "a.md", "message": "ValueError: bad"}]

    def test_partial_failure_still_succeeds(self, env):
        _upload(env.client, [("files", ("a.md", b"x", "text/plain")), ("files", ("b.md", b"x", "text/plain"))])
        env.ingestion.behavior = lambda paths: IngestionSummary(
            files=2, chunks=1, failed=1, errors=[("b.md", "boom")]
        )
        final = _poll(env.client, _submit_ingestion(env.client, ["a.md", "b.md"]).json()["task_id"])
        assert final["status"] == "succeeded"
        assert final["detail"]["result"]["failed"] == 1

    def test_unknown_uploaded_file_is_404(self, env):
        response = _submit_ingestion(env.client, ["missing.md"])
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    @pytest.mark.parametrize("name", ["../secret.md", "sub/a.md", ".."])
    def test_submit_rejects_traversal(self, env, name):
        response = _submit_ingestion(env.client, [name])
        assert response.status_code == 400
        assert env.ingestion.calls == []

    def test_validation_errors(self, env):
        assert env.client.post("/api/v1/ingestions", json={"files": []}, headers=AUTH).status_code == 422
        assert env.client.post("/api/v1/ingestions", json={"files": ["a"], "extra": 1}, headers=AUTH).status_code == 422
        assert env.client.post("/api/v1/ingestions", json={"files": ["a"], "collection": ""}, headers=AUTH).status_code == 422

    def test_duplicate_active_submission_conflicts(self, env):
        _upload(env.client, [("files", ("a.md", b"x", "text/plain"))])
        env.ingestion.gate = threading.Event()
        first = _submit_ingestion(env.client, collection="docs").json()["task_id"]
        try:
            conflict = _submit_ingestion(env.client, collection="docs")
            assert conflict.status_code == 409
            assert conflict.json()["error"]["details"]["task_id"] == first
            other = _submit_ingestion(env.client, collection="other")
            assert other.status_code == 202
        finally:
            env.ingestion.gate.set()
        assert _poll(env.client, first)["status"] == "succeeded"
        assert _submit_ingestion(env.client, collection="docs").status_code == 202


# --------------------------------------------------------------------------- #
# delete
# --------------------------------------------------------------------------- #
class TestDeleteDocument:
    def test_delete_returns_counts(self, env):
        env.data.results[("docs/a.md", "docs")] = DeleteResult(
            "docs/a.md", "docs", chroma_deleted=4, bm25_deleted=4, images_deleted=1, integrity_removed=True
        )
        response = env.client.delete(
            f"/api/v1/documents/{quote('docs/a.md', safe='')}", params={"collection": "docs"}, headers=AUTH
        )
        assert response.status_code == 200
        assert response.json() == {
            "source_path": "docs/a.md",
            "collection": "docs",
            "chroma_deleted": 4,
            "bm25_deleted": 4,
            "images_deleted": 1,
            "integrity_removed": True,
        }
        assert env.data.calls == [("docs/a.md", "docs")]

    def test_history_only_removal_counts_as_deleted(self, env):
        env.data.results[("a.md", "default")] = DeleteResult("a.md", "default", integrity_removed=True)
        response = env.client.delete("/api/v1/documents/a.md", params={"collection": "default"}, headers=AUTH)
        assert response.status_code == 200

    def test_missing_document_is_404(self, env):
        response = env.client.delete("/api/v1/documents/ghost.md", params={"collection": "default"}, headers=AUTH)
        assert response.status_code == 404
        assert response.json()["success"] is False and response.json()["error"]["code"] == "not_found"

    def test_collection_is_required(self, env):
        response = env.client.delete("/api/v1/documents/a.md", headers=AUTH)
        assert response.status_code == 422
        assert env.data.calls == []


# --------------------------------------------------------------------------- #
# evaluation tasks
# --------------------------------------------------------------------------- #
class TestEvaluationTasks:
    def test_submit_poll_succeeded(self, env):
        response = env.client.post(
            "/api/v1/evaluations",
            json={"test_set_path": "tests/fixtures/g.json", "backend": "custom", "top_k": 3, "filters": {"collection": "x"}},
            headers=AUTH,
        )
        assert response.status_code == 202
        final = _poll(env.client, response.json()["task_id"])
        assert final["status"] == "succeeded" and final["kind"] == "evaluation"
        assert final["detail"]["result"]["metrics"] == {"hit_rate": 0.5}
        assert env.evaluation.calls == [
            {"path": "tests/fixtures/g.json", "backend": "custom", "top_k": 3, "filters": {"collection": "x"}}
        ]

    def test_default_test_set_used(self, env):
        task_id = env.client.post("/api/v1/evaluations", json={}, headers=AUTH).json()["task_id"]
        final = _poll(env.client, task_id)
        assert final["detail"]["params"]["test_set_path"] == "/golden/default.json"
        assert env.evaluation.calls[0]["backend"] == "composite"

    def test_runner_error_captured(self, env):
        env.evaluation.error = FileNotFoundError("golden set missing")
        final = _poll(env.client, env.client.post("/api/v1/evaluations", json={}, headers=AUTH).json()["task_id"])
        assert final["status"] == "failed"
        assert final["detail"]["error"]["type"] == "FileNotFoundError"

    @pytest.mark.parametrize("body", [{"backend": "nope"}, {"top_k": 0}, {"bogus": 1}])
    def test_validation(self, env, body):
        assert env.client.post("/api/v1/evaluations", json=body, headers=AUTH).status_code == 422

    def test_concurrent_evaluation_conflicts(self, env):
        env.evaluation.gate = threading.Event()
        first = env.client.post("/api/v1/evaluations", json={}, headers=AUTH).json()["task_id"]
        try:
            assert env.client.post("/api/v1/evaluations", json={}, headers=AUTH).status_code == 409
        finally:
            env.evaluation.gate.set()
        assert _poll(env.client, first)["status"] == "succeeded"


# --------------------------------------------------------------------------- #
# task queries
# --------------------------------------------------------------------------- #
class TestTaskQueries:
    def test_unknown_task_404_envelope(self, env):
        response = env.client.get("/api/v1/tasks/nope", headers=AUTH)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_list_filters_and_order(self, env):
        env.evaluation.error = None
        eval_id = env.client.post("/api/v1/evaluations", json={}, headers=AUTH).json()["task_id"]
        _poll(env.client, eval_id)
        _upload(env.client, [("files", ("a.md", b"x", "text/plain"))])
        ing_id = _submit_ingestion(env.client).json()["task_id"]
        _poll(env.client, ing_id)

        page = env.client.get("/api/v1/tasks", headers=AUTH).json()
        assert page["total"] == 2
        assert [t["task_id"] for t in page["items"]] == [ing_id, eval_id]

        only = env.client.get("/api/v1/tasks", params={"kind": "evaluation"}, headers=AUTH).json()
        assert [t["task_id"] for t in only["items"]] == [eval_id]
        done = env.client.get("/api/v1/tasks", params={"status": "succeeded", "limit": 1}, headers=AUTH).json()
        assert done["total"] == 2 and len(done["items"]) == 1
        assert env.client.get("/api/v1/tasks", params={"status": "weird"}, headers=AUTH).status_code == 422


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
class TestAuth:
    @pytest.mark.parametrize(
        ("method", "path", "kwargs"),
        [
            ("post", "/api/v1/uploads", {"files": [("files", ("a.md", b"x", "text/plain"))]}),
            ("post", "/api/v1/ingestions", {"json": {"files": ["a.md"]}}),
            ("post", "/api/v1/evaluations", {"json": {}}),
            ("delete", "/api/v1/documents/a.md?collection=default", {}),
            ("get", "/api/v1/tasks", {}),
            ("get", "/api/v1/tasks/abc", {}),
        ],
    )
    def test_write_and_task_endpoints_require_token(self, env, method, path, kwargs):
        response = getattr(env.client, method)(path, **kwargs)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
        wrong = getattr(env.client, method)(path, headers={"Authorization": "Bearer nope"}, **kwargs)
        assert wrong.status_code == 401
        assert env.ingestion.calls == [] and env.data.calls == [] and env.evaluation.calls == []
        assert not env.ingestion.upload_dir.exists()

    def test_ws_requires_token(self, env):
        record = env.manager.submit("demo", lambda ctx: 1)
        env.manager.wait(record.task_id, timeout=5)
        with pytest.raises(WebSocketDisconnect) as exc:
            with env.client.websocket_connect(WS_BASE.format(record.task_id)):
                pass
        assert exc.value.code == 4401
        with pytest.raises(WebSocketDisconnect):
            with env.client.websocket_connect(WS_BASE.format(record.task_id) + "?token=bad"):
                pass

    def test_ws_accepts_query_token_and_bearer_header(self, env):
        record = env.manager.submit("demo", lambda ctx: 1)
        env.manager.wait(record.task_id, timeout=5)
        for kwargs in ({"url": WS_BASE.format(record.task_id) + f"?token={TOKEN}"}, {"url": WS_BASE.format(record.task_id), "headers": AUTH}):
            with env.client.websocket_connect(**kwargs) as ws:
                assert ws.receive_json()["status"] == "succeeded"


# --------------------------------------------------------------------------- #
# WS task events
# --------------------------------------------------------------------------- #
def _ws_url(task_id):
    return WS_BASE.format(task_id) + f"?token={TOKEN}"


def _drain(ws):
    frames = []
    while True:
        try:
            frames.append(ws.receive_json())
        except WebSocketDisconnect as exc:
            return frames, exc.code


class TestTaskEventsWs:
    def test_streams_until_terminal_then_closes(self, env):
        _upload(env.client, [("files", ("a.md", b"x", "text/plain"))])
        env.ingestion.gate = threading.Event()
        task_id = _submit_ingestion(env.client).json()["task_id"]
        with env.client.websocket_connect(_ws_url(task_id)) as ws:
            first = ws.receive_json()
            assert first["type"] == "task_event" and first["task_id"] == task_id
            assert first["status"] in ("pending", "running")
            env.ingestion.gate.set()
            frames, code = _drain(ws)
        assert code == 1000
        frames = [first, *frames]
        assert frames[-1]["status"] == "succeeded"
        assert frames[-1]["detail"]["result"]["files"] == 1
        assert all(set(f) >= {"type", "task_id", "status", "detail", "timestamp"} for f in frames)
        versions = [f["version"] for f in frames]
        assert versions == sorted(set(versions))
        assert any(f["detail"].get("progress", {}).get("stage") == "embed" for f in frames)

    def test_failure_event(self, env):
        _upload(env.client, [("files", ("a.md", b"x", "text/plain"))])
        env.ingestion.gate = threading.Event()

        def boom(paths):
            raise RuntimeError("kaput")

        env.ingestion.behavior = boom
        task_id = _submit_ingestion(env.client).json()["task_id"]
        with env.client.websocket_connect(_ws_url(task_id)) as ws:
            first = ws.receive_json()
            env.ingestion.gate.set()
            frames, code = _drain(ws)
        last = (frames or [first])[-1]
        assert last["status"] == "failed" and last["detail"]["error"]["message"] == "kaput"
        assert code == 1000

    def test_terminal_task_replays_state_and_closes(self, env):
        record = env.manager.submit("demo", lambda ctx: {"v": 1})
        env.manager.wait(record.task_id, timeout=5)
        with env.client.websocket_connect(_ws_url(record.task_id)) as ws:
            frames, code = _drain(ws)
        assert [f["status"] for f in frames] == ["succeeded"]
        assert frames[0]["detail"]["result"] == {"v": 1}
        assert code == 1000

    def test_unknown_task_sends_error_frame_and_closes(self, env):
        with env.client.websocket_connect(_ws_url("missing")) as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error" and frame["code"] == "not_found"
            _, code = _drain(ws)
        assert code == 4404

    def test_disconnect_then_poll_recovers_final_state(self, env):
        _upload(env.client, [("files", ("a.md", b"x", "text/plain"))])
        env.ingestion.gate = threading.Event()
        task_id = _submit_ingestion(env.client).json()["task_id"]

        with env.client.websocket_connect(_ws_url(task_id)) as ws:
            assert ws.receive_json()["task_id"] == task_id
        # 客户端已断线；任务在无订阅者时继续运行并到达终态。
        env.ingestion.gate.set()
        final = _poll(env.client, task_id)
        assert final["status"] == "succeeded"
        assert final["detail"]["result"]["chunks"] == 3

        # 重连后回放的就是轮询看到的终态。
        with env.client.websocket_connect(_ws_url(task_id)) as ws:
            frames, _ = _drain(ws)
        assert frames[-1]["status"] == "succeeded"
        assert frames[-1]["detail"] == final["detail"]
        assert not env.manager._listeners

    def test_listeners_released_after_close(self, env):
        record = env.manager.submit("demo", lambda ctx: 1)
        env.manager.wait(record.task_id, timeout=5)
        with env.client.websocket_connect(_ws_url(record.task_id)) as ws:
            _drain(ws)
        assert env.manager._listeners == {}


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #
class TestWiring:
    def test_ingestion_service_uses_injected_settings(self, monkeypatch):
        seen = {}

        class FakePipeline:
            def __init__(self, settings):
                seen["settings"] = settings

        import ingestion.pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "IngestionPipeline", FakePipeline)
        sentinel = object()
        service = IngestionService(settings=sentinel)
        service._get_pipeline()
        assert seen["settings"] is sentinel

    def test_create_app_is_zero_io(self, tmp_path):
        db = tmp_path / "x" / "tasks.db"
        create_app(
            services=ServiceContainer(),
            api_settings=ApiSettings(tasks_db=str(db)),
        )
        assert not db.exists() and not db.parent.exists()

    def test_container_without_settings_cannot_build_evaluation(self):
        from api.errors import ApiError

        with pytest.raises(ApiError):
            ServiceContainer().evaluation
