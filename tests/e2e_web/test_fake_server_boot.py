"""Keeps the Playwright fake backend (``fake_api_server.py``) from rotting.

Offline and fast: builds the app in-process and exercises UI hosting, a seeded read route
and one scripted chat turn. The browser flows themselves are opt-in (``cd frontend && npm run e2e``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.e2e_web.fake_api_server import DEFAULT_STATIC_DIR, build_app

pytestmark = pytest.mark.skipif(
    not (DEFAULT_STATIC_DIR / "index.html").is_file(),
    reason="frontend/out is not built (cd frontend && npm run build)",
)


@pytest.fixture
def client(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as test_client:
        yield test_client
    app.state.e2e_cleanup()


def test_serves_ui_and_seeded_api(client, tmp_path):
    assert client.get("/overview/").status_code == 200
    overview = client.get("/api/v1/overview").json()
    assert overview["totals"]["documents"] == 4
    chat_traces = client.get("/api/v1/traces", params={"routes": "chat"}).json()
    assert [t["trace_id"] for t in chat_traces["items"]] == ["seed-query-chat"]
    assert (tmp_path / "traces.jsonl").is_file()


def test_reset_restores_deleted_documents(client):
    assert client.delete("/api/v1/documents/delete-me.md", params={"collection": "e2e-docs"}).status_code == 200
    assert client.get("/api/v1/overview").json()["totals"]["documents"] == 3
    assert client.post("/__e2e__/reset").json() == {"ok": True}
    assert client.get("/api/v1/overview").json()["totals"]["documents"] == 4


def test_scripted_chat_turn_streams_cited_answer_and_persists_trace(client):
    with client.websocket_connect("/api/v1/chat") as ws:
        ws.send_json({"type": "user_message", "message": "what is rag?", "retrieval": "hybrid"})
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("result", "error"):
                break
    result = frames[-1]
    assert result["type"] == "result" and result["grounded"] is True
    assert "[1]" in result["content"]
    sources = next(f for f in frames if f["type"] == "sources")
    assert sources["citations"][0]["source"] == "guide.md"
    traces = client.get("/api/v1/traces", params={"routes": "chat"}).json()["items"]
    assert result["trace_id"] in {t["trace_id"] for t in traces}
