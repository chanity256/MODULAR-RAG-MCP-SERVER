"""WC6.3: SessionStore（SQLite 两表 CRUD、JSON 往返、并发写、重启持久化）。"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from core.chat.session_store import SessionNotFoundError, SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "nested" / "chat.db")
    yield s
    s.close()


def test_schema_has_spec_columns(tmp_path):
    db = tmp_path / "c.db"
    SessionStore(db).close()
    conn = sqlite3.connect(db)
    sessions = [r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)")]
    messages = [r[1] for r in conn.execute("PRAGMA table_info(chat_messages)")]
    conn.close()
    assert sessions == ["session_id", "title", "created_at", "updated_at", "settings_json", "message_count"]
    assert messages == ["id", "session_id", "role", "content", "sources_json", "metadata_json", "created_at"]


def test_create_and_get_session(store):
    created = store.create_session(title="hello", settings={"retrieval": "hybrid"})
    got = store.get_session(created["session_id"])
    assert got["title"] == "hello"
    assert got["settings"] == {"retrieval": "hybrid"}
    assert got["message_count"] == 0 and got["messages"] == []
    assert store.session_exists(created["session_id"])
    assert store.get_session("missing") is None


def test_explicit_session_id_and_duplicate(store):
    store.create_session(session_id="fixed")
    with pytest.raises(ValueError):
        store.create_session(session_id="fixed")


def test_append_message_updates_count_and_roundtrips_json(store):
    sid = store.create_session()["session_id"]
    sources = [{"id": 1, "source": "文档.pdf", "page": None, "score": 0.9123, "text": "中文 \"quote\""}]
    meta = {"grounded": True, "usage": {"prompt_tokens": 3}, "cancelled": True, "rewritten_query": "改写"}
    store.append_message(sid, "user", "问题")
    msg = store.append_message(sid, "assistant", "答案 [1]", sources=sources, metadata=meta)
    session = store.get_session(sid)
    assert session["message_count"] == 2
    assert [m["role"] for m in session["messages"]] == ["user", "assistant"]
    last = session["messages"][-1]
    assert last["id"] == msg["id"]
    assert last["sources"] == sources and last["metadata"] == meta
    assert session["messages"][0]["sources"] == [] and session["messages"][0]["metadata"] == {}


def test_message_order_is_insertion_order(store):
    sid = store.create_session(session_id="s")["session_id"]
    for i in range(20):
        store.append_message(sid, "user", f"m{i}")
    assert [m["content"] for m in store.get_messages(sid)] == [f"m{i}" for i in range(20)]


def test_append_to_missing_session_raises_and_writes_nothing(store):
    with pytest.raises(SessionNotFoundError):
        store.append_message("nope", "user", "x")
    assert store.get_messages("nope") == []


def test_append_validation(store):
    sid = store.create_session()["session_id"]
    with pytest.raises(ValueError):
        store.append_message(sid, "bot", "x")
    with pytest.raises(TypeError):
        store.append_message(sid, "user", None)  # type: ignore[arg-type]


def test_unserializable_metadata_rolls_back_count(store):
    sid = store.create_session()["session_id"]
    with pytest.raises(TypeError):
        store.append_message(sid, "assistant", "x", metadata={"bad": object()})
    assert store.get_session(sid)["message_count"] == 0


def test_list_sessions_orders_by_recent_update_and_paginates(tmp_path):
    ticks = iter(f"2026-01-01T00:00:{i:02d}+00:00" for i in range(100))
    s = SessionStore(tmp_path / "c.db", clock=lambda: next(ticks))
    a = s.create_session(title="a")["session_id"]
    b = s.create_session(title="b")["session_id"]
    c = s.create_session(title="c")["session_id"]
    s.append_message(a, "user", "bump a")
    assert [x["session_id"] for x in s.list_sessions()] == [a, c, b]
    assert [x["session_id"] for x in s.list_sessions(limit=1, offset=1)] == [c]
    assert "messages" not in s.list_sessions()[0]
    s.close()


def test_update_title_and_settings(store):
    sid = store.create_session(title="old")["session_id"]
    updated_before = store.get_session(sid)["updated_at"]
    assert store.update_title(sid, "新标题")
    assert store.update_settings(sid, {"collection": "c1"})
    got = store.get_session(sid, with_messages=False)
    assert got["title"] == "新标题" and got["settings"] == {"collection": "c1"}
    assert got["updated_at"] == updated_before
    assert "messages" not in got
    assert not store.update_title("missing", "x")


def test_delete_session_cascades_messages(tmp_path):
    db = tmp_path / "c.db"
    s = SessionStore(db)
    sid = s.create_session()["session_id"]
    s.append_message(sid, "user", "x")
    assert s.delete_session(sid)
    assert not s.delete_session(sid)
    assert s.get_session(sid) is None
    s.close()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
    conn.close()


def test_persistence_across_reopen(tmp_path):
    db = tmp_path / "c.db"
    s1 = SessionStore(db)
    sid = s1.create_session(title="persist")["session_id"]
    s1.append_message(sid, "user", "hi")
    s1.append_message(sid, "assistant", "partial", metadata={"cancelled": True})
    s1.close()

    s2 = SessionStore(db)
    got = s2.get_session(sid)
    assert got["title"] == "persist" and got["message_count"] == 2
    assert got["messages"][1]["metadata"] == {"cancelled": True}
    s2.append_message(sid, "user", "again")
    assert s2.get_session(sid)["message_count"] == 3
    s2.close()


def test_concurrent_appends_from_many_threads(store):
    sid = store.create_session()["session_id"]
    n_threads, per_thread = 8, 25
    errors: list[BaseException] = []

    def worker(k: int) -> None:
        try:
            for i in range(per_thread):
                store.append_message(sid, "user", f"{k}-{i}", metadata={"k": k})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    session = store.get_session(sid)
    assert session["message_count"] == n_threads * per_thread
    assert len(session["messages"]) == n_threads * per_thread
    assert len({m["id"] for m in session["messages"]}) == n_threads * per_thread


def test_concurrent_session_creation(store):
    ids: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        sid = store.create_session()["session_id"]
        with lock:
            ids.append(sid)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(ids)) == 20 and len(store.list_sessions(limit=100)) == 20


def test_count_sessions_tracks_create_and_delete(tmp_path):
    s = SessionStore(tmp_path / "count.db")
    try:
        assert s.count_sessions() == 0
        a = s.create_session(title="a")
        s.create_session(title="b")
        assert s.count_sessions() == 2
        s.delete_session(a["session_id"])
        assert s.count_sessions() == 1
    finally:
        s.close()
