"""单端口静态托管 + SPA 路由回退（WC3.4）。使用 tmp 静态目录，不依赖真实前端构建。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from api.deps import ServiceContainer
from api.tasks import TaskManager
from core.settings import ApiSettings, ConfigError

TOKEN = "static-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def static_dir(tmp_path):
    root = tmp_path / "out"
    (root / "overview").mkdir(parents=True)
    (root / "_next" / "static").mkdir(parents=True)
    (root / "index.html").write_text("<html>ROOT</html>", encoding="utf-8")
    (root / "overview" / "index.html").write_text("<html>OVERVIEW</html>", encoding="utf-8")
    (root / "about.html").write_text("<html>ABOUT</html>", encoding="utf-8")
    (root / "404.html").write_text("<html>NOT FOUND PAGE</html>", encoding="utf-8")
    (root / "_next" / "static" / "app.js").write_text("console.log('app');", encoding="utf-8")
    (root / "favicon.ico").write_bytes(b"\x00\x00icon")
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    return root


@pytest.fixture
def manager(tmp_path):
    mgr = TaskManager(tmp_path / "tasks.db")
    yield mgr
    mgr.shutdown(wait=True)


def _client(static_dir, manager, **overrides):
    settings = ApiSettings(api_token=TOKEN, serve_ui=True, static_dir=str(static_dir), **overrides)
    app = create_app(services=ServiceContainer(task_manager=manager), api_settings=settings)
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client(static_dir, manager):
    with _client(static_dir, manager) as c:
        yield c


class TestRouteFallback:
    def test_root_serves_index(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert response.text == "<html>ROOT</html>"
        assert response.headers["content-type"].startswith("text/html")

    @pytest.mark.parametrize("path", ["/overview/", "/overview"])
    def test_route_with_and_without_trailing_slash(self, client, path):
        response = client.get(path)
        assert response.status_code == 200, "no redirect expected"
        assert response.text == "<html>OVERVIEW</html>"

    def test_route_html_file_form(self, client):
        assert client.get("/about").text == "<html>ABOUT</html>"
        assert client.get("/about/").text == "<html>ABOUT</html>"
        assert client.get("/about.html").text == "<html>ABOUT</html>"

    @pytest.mark.parametrize("path", ["/chat", "/chat/session-123", "/overview/deep/link"])
    def test_unknown_client_routes_fall_back_to_root_index(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        assert response.text == "<html>ROOT</html>"

    def test_head_request(self, client):
        response = client.head("/overview/")
        assert response.status_code == 200 and response.content == b""


class TestAssets:
    def test_serves_exact_files(self, client):
        js = client.get("/_next/static/app.js")
        assert js.status_code == 200
        assert js.text == "console.log('app');"
        assert "javascript" in js.headers["content-type"]
        assert client.get("/favicon.ico").content == b"\x00\x00icon"

    @pytest.mark.parametrize("path", ["/_next/static/missing.js", "/missing.png", "/_next/data/x.json"])
    def test_missing_asset_is_404_not_index(self, client, path):
        response = client.get(path)
        assert response.status_code == 404
        assert "ROOT" not in response.text

    def test_path_traversal_cannot_escape_static_dir(self, client):
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/_next/../../secret.txt"):
            response = client.get(path)
            assert "TOP SECRET" not in response.text, path

    def test_security_headers_apply(self, client):
        assert client.get("/").headers["x-content-type-options"] == "nosniff"


class TestApiPrecedence:
    def test_health_and_authenticated_api_still_served(self, client, manager):
        assert client.get("/health").json()["status"] in ("ok", "degraded")
        tasks = client.get("/api/v1/tasks", headers=AUTH)
        assert tasks.status_code == 200 and tasks.json()["total"] == 0

    def test_api_still_requires_token(self, client):
        assert client.get("/api/v1/tasks").status_code == 401

    @pytest.mark.parametrize("path", ["/api/v1/nope", "/api/nope", "/api", "/api/", "/api/v1/tasks/x/y/z"])
    def test_unknown_api_paths_return_json_404(self, client, path):
        for method in ("get", "post"):
            response = getattr(client, method)(path, headers=AUTH)
            assert response.status_code == 404, (method, path)
            assert response.headers["content-type"].startswith("application/json")
            body = response.json()
            assert body["success"] is False and body["error"]["code"] == "not_found"

    def test_ws_route_registered_before_static_mount(self, client, manager):
        record = manager.submit("demo", lambda ctx: 1)
        manager.wait(record.task_id, timeout=5)
        with client.websocket_connect(f"/api/v1/tasks/{record.task_id}/events?token={TOKEN}") as ws:
            assert ws.receive_json()["status"] == "succeeded"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/v1/tasks/{record.task_id}/events"):
                pass

    def test_openapi_and_docs_still_served(self, client):
        assert client.get("/openapi.json").json()["openapi"].startswith("3.")
        assert client.get("/docs").status_code == 200


class TestConfiguration:
    def test_disabled_by_default_returns_json_404_at_root(self, manager):
        app = create_app(services=ServiceContainer(task_manager=manager), api_settings=ApiSettings(api_token=TOKEN))
        with TestClient(app) as c:
            response = c.get("/")
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"

    def test_missing_directory_fails_fast(self, tmp_path, manager):
        settings = ApiSettings(api_token=TOKEN, serve_ui=True, static_dir=str(tmp_path / "nope"))
        with pytest.raises(ConfigError, match="index.html"):
            create_app(services=ServiceContainer(task_manager=manager), api_settings=settings)

    def test_directory_without_index_fails_fast(self, tmp_path, manager):
        (tmp_path / "empty").mkdir()
        settings = ApiSettings(api_token=TOKEN, serve_ui=True, static_dir=str(tmp_path / "empty"))
        with pytest.raises(ConfigError, match="npm run build"):
            create_app(services=ServiceContainer(task_manager=manager), api_settings=settings)

    def test_relative_static_dir_resolves_against_cwd(self, static_dir, manager, monkeypatch):
        monkeypatch.chdir(static_dir.parent)
        settings = ApiSettings(api_token=TOKEN, serve_ui=True, static_dir="./out")
        app = create_app(services=ServiceContainer(task_manager=manager), api_settings=settings)
        with TestClient(app) as c:
            assert c.get("/overview").text == "<html>OVERVIEW</html>"
