"""PI4 ``scripts/pageindex_sync.py`` 对账同步单测（规格 §4 PI4 行）。

覆盖：四类动作（新增 / 重建 / 删除 / 跳过）、``--dry-run`` 无写入、单条失败
不中断、``render:`` 转换失败单列、双 collection 去重、hash 冲突实算优先、
文件缺失按「历史中已不存在」处理、``--collection`` 模式零删除、入口
``ensure_local_roots``、``tree_search.enabled=false`` 退出码 2、SidecarClient
错误分型（MockTransport）、``sys.modules`` 无 pageindex / litellm。

全部离线：fake history / fake checker / fake client，零网络零真实 sidecar。
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pageindex_sync.py"
_spec = importlib.util.spec_from_file_location("pageindex_sync_script", _SCRIPT)
sync_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync_mod  # dataclass 注解解析要求先注册模块
_spec.loader.exec_module(sync_mod)

from libs.agentic_search.path_guard import PathGuard  # noqa: E402


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


HASH_A = _sha("alpha content")
HASH_B = _sha("beta content")


class FakeChecker:
    """``compute_sha256`` 的 fake：命中映射返回哈希，否则按文件缺失抛错。"""

    def __init__(self, hashes: dict[str, str]):
        self.hashes = hashes

    def compute_sha256(self, path: str) -> str:
        if path not in self.hashes:
            raise FileNotFoundError(f"file not found or not a regular file: {path}")
        return self.hashes[path]


class FakeHistory:
    def __init__(self, records: list[dict]):
        self.records = records

    def list_processed(self) -> list[dict]:
        return list(self.records)


class FakeClient:
    def __init__(
        self,
        docs: list[dict] | None = None,
        index_errors: dict[str, Exception] | None = None,
        delete_errors: dict[str, Exception] | None = None,
    ):
        self.docs = docs or []
        self.index_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []
        self.index_errors = index_errors or {}
        self.delete_errors = delete_errors or {}

    def documents(self) -> list[dict]:
        return list(self.docs)

    def index(self, source_path: str, file_hash: str) -> dict:
        self.index_calls.append((source_path, file_hash))
        error = self.index_errors.get(source_path)
        if error is not None:
            raise error
        return {"doc_id": "d1", "format": "md", "node_count": 3, "llm_tokens": 10}

    def delete(self, source_path: str) -> bool:
        self.delete_calls.append(source_path)
        error = self.delete_errors.get(source_path)
        if error is not None:
            raise error
        return True


@pytest.fixture
def corpus(tmp_path):
    """两个真实 MD 文件 + 一个白名单根；返回 (root, files, hashes)。"""
    root = tmp_path / "documents"
    root.mkdir()
    files = {}
    hashes = {}
    for name, content in (("a.md", "alpha content"), ("b.md", "beta content")):
        path = root / name
        path.write_text(content, encoding="utf-8")
        files[name] = path
        hashes[str(path)] = _sha(content)
    return root, files, hashes


def _guard(corpus_root) -> PathGuard:
    return PathGuard([str(corpus_root)])


def _rec(path, file_hash=HASH_A, collection="default", status="success"):
    return {
        "source_path": str(path),
        "collection": collection,
        "status": status,
        "file_hash": file_hash,
    }


# ---------------------------------------------------------------------------
# plan_sync：四类动作
# ---------------------------------------------------------------------------


class TestPlanActions:
    def test_new_file_planned_as_add(self, corpus):
        root, files, hashes = corpus
        entries = sync_mod.collect_history([_rec(files["a.md"], HASH_A)])
        plan = sync_mod.plan_sync(
            entries, [], checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        assert [(a.kind, a.source_path, a.file_hash) for a in plan.upserts] == [
            ("upsert", str(files["a.md"]), HASH_A)
        ]
        assert plan.counts.added == 1 and plan.counts.rebuilt == 0

    def test_unchanged_hash_is_skipped(self, corpus):
        root, files, hashes = corpus
        entries = sync_mod.collect_history([_rec(files["a.md"], HASH_A)])
        docs = [{"source_path": str(files["a.md"]), "file_hash": HASH_A}]
        plan = sync_mod.plan_sync(
            entries, docs, checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        assert plan.upserts == [] and plan.deletes == []
        assert plan.counts.skipped == 1

    def test_hash_change_rebuilds_with_computed_hash(self, corpus):
        root, files, hashes = corpus
        entries = sync_mod.collect_history([_rec(files["a.md"], "old" * 16)])
        docs = [{"source_path": str(files["a.md"]), "file_hash": "old" * 16}]
        plan = sync_mod.plan_sync(
            entries, docs, checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        assert [(a.outcome, a.file_hash) for a in plan.upserts] == [("rebuilt", HASH_A)]
        assert plan.counts.rebuilt == 1 and plan.counts.added == 0

    def test_full_mode_deletes_orphan_sidecar_docs(self, corpus):
        root, files, hashes = corpus
        orphan = str(root / "gone.md")
        entries = sync_mod.collect_history([_rec(files["a.md"], HASH_A)])
        docs = [
            {"source_path": str(files["a.md"]), "file_hash": HASH_A},
            {"source_path": orphan, "file_hash": HASH_B},
        ]
        plan = sync_mod.plan_sync(
            entries, docs, checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        assert [a.source_path for a in plan.deletes] == [orphan]
        assert plan.counts.deleted == 1

    def test_failed_and_placeholder_records_ignored(self, corpus):
        root, files, hashes = corpus
        records = [
            _rec(files["a.md"], status="failed"),
            {"source_path": None, "collection": "default", "status": "success",
             "file_hash": HASH_A},  # v1 迁移占位
            _rec(files["b.md"], HASH_B),
        ]
        entries = sync_mod.collect_history(records)
        assert list(entries) == [str(files["b.md"])]
        plan = sync_mod.plan_sync(
            entries, [], checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        assert plan.counts.added == 1 and plan.counts.failed == 0


# ---------------------------------------------------------------------------
# 去重 / hash 冲突 / 缺失 / 扩展名 / 白名单
# ---------------------------------------------------------------------------


class TestDedupAndResolution:
    def test_same_file_two_collections_single_index(self, corpus):
        root, files, hashes = corpus
        records = [_rec(files["a.md"], HASH_A), _rec(files["a.md"], HASH_A, collection="other")]
        entries = sync_mod.collect_history(records)
        assert len(entries) == 1
        plan = sync_mod.plan_sync(
            entries, [], checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        assert len(plan.upserts) == 1 and plan.counts.added == 1

    def test_hash_conflict_resolved_by_computed_sha256(self, corpus):
        root, files, hashes = corpus
        records = [_rec(files["a.md"], HASH_A), _rec(files["a.md"], HASH_B, collection="other")]
        entries = sync_mod.collect_history(records)
        plan = sync_mod.plan_sync(
            entries, [], checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        # 实算 SHA256（HASH_A）优先于任一记录哈希。
        assert plan.upserts[0].file_hash == HASH_A

    def test_missing_file_treated_as_absent_from_history(self, corpus):
        root, files, hashes = corpus
        gone = str(root / "gone.md")
        entries = sync_mod.collect_history([_rec(gone, HASH_A)])
        docs = [{"source_path": gone, "file_hash": HASH_A}]
        plan = sync_mod.plan_sync(
            entries, docs, checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        assert plan.upserts == []
        # 「历史中已不存在」→ 不进删除保护集 → sidecar 记录被删。
        assert [a.source_path for a in plan.deletes] == [gone]

    def test_unsupported_extension_skipped_and_counted(self, corpus):
        root, files, hashes = corpus
        docx = root / "note.docx"
        docx.write_bytes(b"docx")
        hashes[str(docx)] = _sha("docx")
        entries = sync_mod.collect_history([_rec(docx, hashes[str(docx)])])
        plan = sync_mod.plan_sync(
            entries, [], checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        assert plan.upserts == []
        assert plan.counts.skipped_unsupported == 1

    def test_outside_whitelist_skipped_but_protected_from_deletion(self, tmp_path, corpus):
        root, files, hashes = corpus
        outsider = tmp_path / "elsewhere.md"
        outsider.write_text("outside", encoding="utf-8")
        hashes[str(outsider)] = _sha("outside")
        entries = sync_mod.collect_history([_rec(outsider, hashes[str(outsider)])])
        docs = [{"source_path": str(outsider), "file_hash": _sha("stale")}]
        plan = sync_mod.plan_sync(
            entries, docs, checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        assert plan.upserts == [] and plan.counts.skipped == 1
        # 仍在历史 → 不删 sidecar 索引（宁可保守）。
        assert plan.deletes == []

    def test_sidecar_docs_without_source_path_ignored(self, corpus):
        root, files, hashes = corpus
        entries = sync_mod.collect_history([_rec(files["a.md"], HASH_A)])
        plan = sync_mod.plan_sync(
            entries, [{"file_hash": HASH_A}, "junk"],
            checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True,
        )
        assert plan.counts.added == 1 and plan.deletes == []


# ---------------------------------------------------------------------------
# execute_sync：失败不中断 / render 前缀 / dry-run
# ---------------------------------------------------------------------------


class TestExecute:
    def test_single_failure_does_not_abort(self, corpus):
        root, files, hashes = corpus
        entries = sync_mod.collect_history(
            [_rec(files["a.md"], HASH_A), _rec(files["b.md"], HASH_B)]
        )
        plan = sync_mod.plan_sync(
            entries, [], checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        client = FakeClient(index_errors={
            str(files["a.md"]): sync_mod.SidecarBackendError("HTTP 500: boom"),
        })
        counts = sync_mod.execute_sync(plan, client, dry_run=False)
        assert len(client.index_calls) == 2  # 失败不中断，第二篇仍被处理
        assert counts.failed == 1 and counts.added == 1 and counts.convert_failed == 0

    def test_render_prefix_counted_as_convert_failed(self, corpus):
        root, files, hashes = corpus
        entries = sync_mod.collect_history([_rec(files["a.md"], HASH_A)])
        plan = sync_mod.plan_sync(
            entries, [], checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        client = FakeClient(index_errors={
            str(files["a.md"]): sync_mod.SidecarBackendError("render: pandoc not found"),
        })
        counts = sync_mod.execute_sync(plan, client, dry_run=False)
        assert counts.convert_failed == 1 and counts.failed == 0

    def test_delete_failure_counted_as_failed(self, corpus):
        root, files, hashes = corpus
        gone = str(root / "gone.md")
        plan = sync_mod.SyncPlan(
            deletes=[sync_mod.SyncAction("delete", gone)], counts=sync_mod.SyncCounts(deleted=1)
        )
        client = FakeClient(delete_errors={gone: sync_mod.SidecarTimeoutError("timeout")})
        counts = sync_mod.execute_sync(plan, client, dry_run=False)
        assert counts.deleted == 0 and counts.failed == 1

    def test_dry_run_makes_no_write_calls(self, corpus):
        root, files, hashes = corpus
        gone = str(root / "gone.md")
        entries = sync_mod.collect_history([_rec(files["a.md"], HASH_A)])
        docs = [{"source_path": gone, "file_hash": HASH_A}]
        plan = sync_mod.plan_sync(
            entries, docs, checker=FakeChecker(hashes), path_guard=_guard(root), full_mode=True
        )
        client = FakeClient()
        counts = sync_mod.execute_sync(plan, client, dry_run=True)
        assert client.index_calls == [] and client.delete_calls == []
        assert counts.added == 1 and counts.deleted == 1


# ---------------------------------------------------------------------------
# main()：入口契约（退出码 / ensure_local_roots / collection 模式提示）
# ---------------------------------------------------------------------------


def _settings(tmp_path, corpus_root):
    from core.settings import load_settings

    base = load_settings()
    tree = replace(
        base.tree_search,
        enabled=True,
        api_token="tok",
        base_url="http://127.0.0.1:8585",
        allowed_roots=[str(corpus_root)],
    )
    return replace(base, tree_search=tree)


class TestMain:
    def test_exit_zero_and_summary_when_all_skipped(self, corpus, capsys):
        root, files, hashes = corpus
        settings = _settings(corpus[0], root)
        history = FakeHistory([_rec(files["a.md"], HASH_A)])
        docs = [{"source_path": str(files["a.md"]), "file_hash": HASH_A}]
        code = sync_mod.main(
            [], settings=settings, checker=FakeChecker(hashes), history=history,
            client_factory=lambda cfg: FakeClient(docs=docs),
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "added=0" in out and "skipped=1" in out and "collection=all" in out

    def test_exit_one_when_failures(self, corpus):
        root, files, hashes = corpus
        settings = _settings(corpus[0], root)
        history = FakeHistory([_rec(files["a.md"], HASH_A)])
        code = sync_mod.main(
            [], settings=settings, checker=FakeChecker(hashes), history=history,
            client_factory=lambda cfg: FakeClient(
                index_errors={str(files["a.md"]): sync_mod.SidecarBackendError("boom")}
            ),
        )
        assert code == 1

    def test_disabled_tree_search_exits_2(self, corpus):
        from core.settings import load_settings

        root, files, hashes = corpus
        code = sync_mod.main(
            [], settings=load_settings(), checker=FakeChecker(hashes),
            history=FakeHistory([]), client_factory=lambda cfg: FakeClient(),
        )
        assert code == 2

    def test_unreachable_allowed_root_exits_2(self, tmp_path, corpus):
        root, files, hashes = corpus
        settings = _settings(corpus[0], root)
        settings = replace(
            settings,
            tree_search=replace(settings.tree_search, allowed_roots=[str(tmp_path / "nope")]),
        )
        code = sync_mod.main(
            [], settings=settings, checker=FakeChecker(hashes), history=FakeHistory([]),
            client_factory=lambda cfg: FakeClient(),
        )
        assert code == 2

    def test_entry_creates_missing_allowed_root(self, tmp_path, corpus, monkeypatch):
        root, files, hashes = corpus
        # 白名单根放 tmp 项目的 data/ 下且预先不存在：入口 ensure_local_roots
        # 预建后 PathGuard 构造成功（否则退出码 2）。
        uploads = tmp_path / "data" / "uploads"
        settings = _settings(corpus[0], uploads)
        monkeypatch.setenv("RAG_PROJECT_ROOT", str(tmp_path))
        assert not uploads.exists()
        code = sync_mod.main(
            [], settings=settings, checker=FakeChecker({}), history=FakeHistory([]),
            client_factory=lambda cfg: FakeClient(),
        )
        assert uploads.exists()
        assert code == 0

    def test_collection_mode_never_deletes_and_hints(self, corpus, capsys):
        root, files, hashes = corpus
        settings = _settings(corpus[0], root)
        history = FakeHistory([_rec(files["a.md"], HASH_A)])
        orphan = {"source_path": str(root / "gone.md"), "file_hash": HASH_B}
        client = FakeClient(docs=[orphan])
        code = sync_mod.main(
            ["--collection", "default"], settings=settings, checker=FakeChecker(hashes),
            history=history, client_factory=lambda cfg: client,
        )
        assert code == 0
        assert client.delete_calls == []
        out = capsys.readouterr().out
        assert "deleted=0" in out and "full sync" in out

    def test_dry_run_flag_makes_no_write_calls(self, corpus, capsys):
        root, files, hashes = corpus
        settings = _settings(corpus[0], root)
        history = FakeHistory([_rec(files["a.md"], HASH_A), _rec(files["b.md"], HASH_B)])
        client = FakeClient()
        code = sync_mod.main(
            ["--dry-run"], settings=settings, checker=FakeChecker(hashes),
            history=history, client_factory=lambda cfg: client,
        )
        assert code == 0
        assert client.index_calls == [] and client.delete_calls == []
        assert "dry-run" in capsys.readouterr().out

    def test_render_failure_exit_one_with_separate_count(self, corpus):
        root, files, hashes = corpus
        settings = _settings(corpus[0], root)
        history = FakeHistory([_rec(files["a.md"], HASH_A)])
        client = FakeClient(index_errors={
            str(files["a.md"]): sync_mod.SidecarBackendError("render: chrome missing"),
        })
        code = sync_mod.main(
            [], settings=settings, checker=FakeChecker(hashes), history=history,
            client_factory=lambda cfg: client,
        )
        assert code == 1


# ---------------------------------------------------------------------------
# SidecarClient：HTTP 错误分型（MockTransport）
# ---------------------------------------------------------------------------


def _client(handler):
    import httpx

    return sync_mod.SidecarClient(
        "http://127.0.0.1:8585", "tok", timeout_s=5,
        transport=httpx.MockTransport(handler),
    )


class TestSidecarClient:
    def test_documents_parses_envelope(self):
        def handler(request):
            assert request.headers["authorization"] == "Bearer tok"
            assert str(request.url) == "http://127.0.0.1:8585/api/v1/documents"
            import httpx
            return httpx.Response(200, json={"success": True, "data": [], "error": None})

        assert _client(handler).documents() == []

    def test_index_posts_payload(self):
        seen = {}

        def handler(request):
            import json as _json
            import httpx
            seen["path"] = request.url.path
            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json={
                "success": True, "data": {"doc_id": "d1"}, "error": None})

        data = _client(handler).index("/abs/a.md", HASH_A)
        assert data == {"doc_id": "d1"}
        assert seen["path"] == "/api/v1/index"
        assert seen["body"] == {"source_path": "/abs/a.md", "file_hash": HASH_A}

    def test_http_500_maps_to_backend_error(self):
        import httpx

        def handler(request):
            return httpx.Response(500, text="boom")

        with pytest.raises(sync_mod.SidecarBackendError):
            _client(handler).documents()

    def test_success_false_maps_to_backend_error(self):
        import httpx

        def handler(request):
            return httpx.Response(200, json={"success": False, "data": None,
                                             "error": "render: no chrome"})

        with pytest.raises(sync_mod.SidecarBackendError, match="render:"):
            _client(handler).index("/abs/a.md", HASH_A)

    def test_non_json_maps_to_response_error(self):
        import httpx

        def handler(request):
            return httpx.Response(200, text="<html>")

        with pytest.raises(sync_mod.SidecarResponseError):
            _client(handler).documents()

    def test_timeout_maps_to_timeout_error(self):
        import httpx

        def handler(request):
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(sync_mod.SidecarTimeoutError):
            _client(handler).documents()

    def test_delete_returns_removed_flag(self):
        def handler(request):
            import json as _json
            import httpx
            assert request.url.path == "/api/v1/index/delete"
            assert _json.loads(request.content) == {"source_path": "/abs/gone.md"}
            return httpx.Response(200, json={"success": True, "data": {"removed": True},
                                             "error": None})

        assert _client(handler).delete("/abs/gone.md") is True


# ---------------------------------------------------------------------------
# 红线 / 纪律
# ---------------------------------------------------------------------------


def test_no_pageindex_litellm_import():
    loaded = set(sys.modules)
    assert "pageindex" not in loaded and "litellm" not in loaded
    assert not any(m.startswith(("pageindex.", "litellm.")) for m in loaded)


def test_render_summary_collection_hint_and_dry_run_tag():
    counts = sync_mod.SyncCounts(added=1, convert_failed=2)
    text = sync_mod.render_summary(counts, collection="c", dry_run=True)
    assert "added=1" in text and "convert_failed=2" in text
    assert "collection=c" in text and "(dry-run)" in text
    # collection 模式的删除提示只在非 dry-run 给出。
    assert "full sync" not in text
    text_full = sync_mod.render_summary(sync_mod.SyncCounts(), collection=None, dry_run=False)
    assert "full sync" not in text_full
