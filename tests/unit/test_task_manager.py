"""TaskStore / TaskManager 单测（WC4.1）：状态机、持久化、失败捕获、事件发布。"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

import pytest

from api.tasks import (
    TaskContext,
    TaskFailure,
    TaskManager,
    TaskStatus,
    TaskStore,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "db" / "tasks.db"


@pytest.fixture
def manager(db_path):
    mgr = TaskManager(db_path, max_workers=2)
    yield mgr
    mgr.shutdown(wait=True)


def _collect(mgr: TaskManager, task_id: str) -> list:
    events: list = []
    mgr.add_listener(task_id, events.append)
    return events


class TestStateMachine:
    def test_store_is_lazy(self, db_path):
        TaskManager(db_path)
        assert not db_path.exists()

    def test_create_is_pending(self, db_path):
        store = TaskStore(db_path)
        record = store.create("ingestion", {"params": {"a": 1}})
        assert record.status == TaskStatus.PENDING
        assert record.version == 0
        assert record.started_at is None and record.finished_at is None
        assert record.detail == {"params": {"a": 1}}

    def test_success_path_and_timestamps(self, manager):
        gate = threading.Event()

        def fn(ctx: TaskContext):
            gate.wait(5)
            return {"ok": True}

        record = manager.submit("demo", fn)
        events = _collect(manager, record.task_id)
        gate.set()
        final = manager.wait(record.task_id, timeout=5)

        assert final.status == TaskStatus.SUCCEEDED
        assert final.detail["result"] == {"ok": True}
        assert final.started_at and final.finished_at
        assert final.created_at <= final.started_at <= final.finished_at
        assert [e.status for e in events][-1] == TaskStatus.SUCCEEDED

    def test_failure_is_captured(self, manager):
        def boom(ctx):
            raise ValueError("bad input")

        record = manager.submit("demo", boom)
        final = manager.wait(record.task_id, timeout=5)
        assert final.status == TaskStatus.FAILED
        assert final.detail["error"] == {"type": "ValueError", "message": "bad input"}
        assert final.finished_at

    def test_task_failure_keeps_partial_result(self, manager):
        def fn(ctx):
            raise TaskFailure("all failed", result={"failed": 2})

        final = manager.wait(manager.submit("demo", fn).task_id, timeout=5)
        assert final.status == TaskStatus.FAILED
        assert final.detail["error"]["message"] == "all failed"
        assert final.detail["result"] == {"failed": 2}

    def test_terminal_state_is_final(self, db_path):
        store = TaskStore(db_path)
        record = store.create("demo")
        store.update(record.task_id, status=TaskStatus.RUNNING)
        done = store.update(record.task_id, status=TaskStatus.SUCCEEDED, detail_patch={"result": 1})
        again = store.update(record.task_id, status=TaskStatus.FAILED, detail_patch={"error": "x"})
        assert again.status == TaskStatus.SUCCEEDED
        assert again.version == done.version
        assert "error" not in again.detail

    def test_dataclass_result_is_serialisable(self, manager):
        @dataclass
        class Summary:
            files: int = 1

        final = manager.wait(manager.submit("demo", lambda ctx: Summary()).task_id, timeout=5)
        assert final.detail["result"] == {"files": 1}

    def test_unknown_task(self, manager):
        assert manager.get("nope") is None
        assert manager.store.update("nope", status=TaskStatus.RUNNING) is None


class TestProgressAndEvents:
    def test_progress_is_persisted_and_published(self, manager):
        gate = threading.Event()

        def fn(ctx: TaskContext):
            gate.wait(5)
            ctx.progress("load", 1, 2)
            ctx.progress("embed", 2, 2)
            return "done"

        record = manager.submit("demo", fn)
        events = _collect(manager, record.task_id)
        gate.set()
        final = manager.wait(record.task_id, timeout=5)

        assert final.detail["progress"] == {"stage": "embed", "current": 2, "total": 2}
        versions = [e.version for e in events]
        assert versions == sorted(versions) and len(set(versions)) == len(versions)
        stages = [e.detail["progress"]["stage"] for e in events if "progress" in e.detail]
        assert stages[:2] == ["load", "embed"]
        assert events[-1].status == TaskStatus.SUCCEEDED

    def test_listener_exception_does_not_break_task(self, manager):
        record = manager.submit("demo", lambda ctx: 1)

        def bad(_):
            raise RuntimeError("listener bug")

        manager.add_listener(record.task_id, bad)
        assert manager.wait(record.task_id, timeout=5).status == TaskStatus.SUCCEEDED

    def test_remove_listener(self, manager):
        gate = threading.Event()
        record = manager.submit("demo", lambda ctx: gate.wait(5))
        events: list = []
        manager.add_listener(record.task_id, events.append)
        manager.remove_listener(record.task_id, events.append)
        gate.set()
        manager.wait(record.task_id, timeout=5)
        assert events == []

    def test_async_subscription_bridge(self, manager):
        gate = threading.Event()

        def fn(ctx: TaskContext):
            gate.wait(5)
            ctx.progress("stage", 1, 1)
            return {"r": 1}

        async def scenario():
            record = manager.submit("demo", fn)
            sub = manager.subscribe(record.task_id, asyncio.get_running_loop())
            try:
                gate.set()
                seen = []
                while True:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=5)
                    seen.append(event)
                    if event.terminal:
                        return seen
            finally:
                sub.close()

        seen = asyncio.run(scenario())
        assert seen[-1].status == TaskStatus.SUCCEEDED
        assert [e.version for e in seen] == sorted(e.version for e in seen)


class TestPersistence:
    def test_survives_reopen(self, db_path):
        first = TaskManager(db_path)
        final = first.wait(first.submit("demo", lambda ctx: {"v": 7}).task_id, timeout=5)
        first.shutdown(wait=True)

        second = TaskManager(db_path)
        try:
            reloaded = second.get(final.task_id)
            assert reloaded == final
        finally:
            second.shutdown(wait=True)

    def test_stale_active_tasks_marked_interrupted_on_reopen(self, db_path):
        store = TaskStore(db_path)
        pending = store.create("demo")
        running = store.create("demo")
        store.update(running.task_id, status=TaskStatus.RUNNING, detail_patch={"progress": {"stage": "x"}})
        done = store.create("demo")
        store.update(done.task_id, status=TaskStatus.RUNNING)
        store.update(done.task_id, status=TaskStatus.SUCCEEDED, detail_patch={"result": 1})
        store.close()

        reopened = TaskManager(db_path)
        try:
            for task_id in (pending.task_id, running.task_id):
                record = reopened.get(task_id)
                assert record.status == TaskStatus.FAILED
                assert record.detail["error"]["type"] == "interrupted"
                assert record.finished_at
            assert reopened.get(running.task_id).detail["progress"] == {"stage": "x"}
            assert reopened.get(done.task_id).status == TaskStatus.SUCCEEDED
        finally:
            reopened.shutdown(wait=True)


class TestListing:
    def test_newest_first_filters_and_pagination(self, manager):
        ids = []
        for kind in ("a", "b", "a"):
            record = manager.submit(kind, lambda ctx: 1)
            manager.wait(record.task_id, timeout=5)
            ids.append(record.task_id)

        records, total = manager.list()
        assert total == 3
        assert [r.task_id for r in records] == ids[::-1]

        only_a, total_a = manager.list(kind="a")
        assert total_a == 2 and {r.kind for r in only_a} == {"a"}

        page, total_all = manager.list(limit=1, offset=1)
        assert total_all == 3 and [r.task_id for r in page] == [ids[1]]

        failed, total_failed = manager.list(statuses=[TaskStatus.FAILED])
        assert (failed, total_failed) == ([], 0)


class TestConcurrency:
    def test_pool_is_bounded(self, db_path):
        mgr = TaskManager(db_path, max_workers=1)
        try:
            gate = threading.Event()
            started = threading.Event()

            def first(ctx):
                started.set()
                gate.wait(5)

            a = mgr.submit("demo", first)
            b = mgr.submit("demo", lambda ctx: "b")
            assert started.wait(5)
            assert mgr.get(a.task_id).status == TaskStatus.RUNNING
            assert mgr.get(b.task_id).status == TaskStatus.PENDING
            gate.set()
            assert mgr.wait(b.task_id, timeout=5).status == TaskStatus.SUCCEEDED
        finally:
            mgr.shutdown(wait=True)

    def test_concurrent_submissions(self, manager):
        records: list = []
        lock = threading.Lock()

        def submit(i: int) -> None:
            record = manager.submit("demo", lambda ctx, i=i: i)
            with lock:
                records.append(record)

        threads = [threading.Thread(target=submit, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        results = {manager.wait(r.task_id, timeout=10).detail["result"] for r in records}
        assert results == set(range(20))
        assert len({r.task_id for r in records}) == 20

    def test_rejects_invalid_worker_count(self, db_path):
        with pytest.raises(ValueError):
            TaskManager(db_path, max_workers=0)
