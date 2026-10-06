"""``scripts/ingest.py`` 同步 / 清理 / 重试模式单元测试（IR3 + IR4.2 CLI 部分）。

纯离线：pipeline / 历史表 / DocumentManager 全部注入记录调用的 fake
（重试选择另有一条用 tmp 目录真 ``SQLiteIntegrityChecker`` 的用例），
零网络、零子进程、零真实向量库。

覆盖：
- ``collect_and_diff`` 分类：new / modified（哈希变化、上次失败、存储缺失）/
  unchanged / deleted / stale；范围限定 ``--path`` 前缀 + collection。
- ``--dry-run``：只打印计划，``pipeline.run`` 与 ``delete_document`` 零调用。
- ``--sync``：摄取 new + modified，删除磁盘上消失的文档；unchanged / stale 不动。
- ``--prune``：只删除不摄取；其他 collection / 范围外路径不受影响；
  整个目录已删除时仍可清理。
- ``--retry-failed``：只选该 collection 的 failed 记录；文件已消失则跳过。
- argparse 互斥 / 组合校验；退出码 0 / 1 / 2。
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from ingestion.pipeline import IngestionPipelineError, IngestionResult

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest.py"
_MODULE_NAME = "scripts_ingest"


def _load_cli():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


def _sha(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FakeIntegrity:
    """只读历史表 fake：``list_processed`` 返回给定记录，记录任何写调用。"""

    def __init__(self, records: list[dict] | None = None) -> None:
        self.records = list(records or [])
        self.writes: list[str] = []

    def list_processed(self) -> list[dict]:
        return [dict(r) for r in self.records]

    def compute_sha256(self, path: str) -> str:
        return _sha(path)

    def mark_success(self, *a, **kw):  # pragma: no cover - must never be called
        self.writes.append("mark_success")

    def mark_failed(self, *a, **kw):  # pragma: no cover - must never be called
        self.writes.append("mark_failed")

    def remove_record(self, *a, **kw):  # pragma: no cover - must never be called
        self.writes.append("remove_record")


@dataclass
class _Doc:
    source_path: str
    collection: str


@dataclass
class _DeleteResult:
    chroma_deleted: int = 2
    bm25_deleted: int = 2
    images_deleted: int = 0
    integrity_removed: bool = True


class FakeManager:
    def __init__(
        self,
        docs: list[_Doc] | None = None,
        *,
        fail_substrings: tuple[str, ...] = (),
        list_error: Exception | None = None,
    ) -> None:
        self.docs = list(docs or [])
        self.deleted: list[tuple[str, str]] = []
        self._fail = fail_substrings
        self._list_error = list_error

    def list_documents(self, collection=None):
        if self._list_error is not None:
            raise self._list_error
        return [d for d in self.docs if collection is None or d.collection == collection]

    def delete_document(self, source_path, collection):
        if any(sub in source_path for sub in self._fail):
            raise RuntimeError(f"boom deleting {source_path}")
        self.deleted.append((source_path, collection))
        return _DeleteResult()


class FakePipeline:
    def __init__(self, *, fail_substrings: tuple[str, ...] = ()) -> None:
        self.calls: list[dict] = []
        self._fail = fail_substrings

    def run(self, source_path, collection="default", on_progress=None, *, force=False, trace=None):
        self.calls.append({"path": str(source_path), "collection": collection, "force": force})
        if any(sub in str(source_path) for sub in self._fail):
            raise IngestionPipelineError(f"fake failure for {source_path}", stage="embed")
        return IngestionResult(
            source_path=str(source_path),
            collection=collection,
            doc_hash="a" * 64,
            status="success",
            chunks=1,
            images=0,
            written=1,
            trace_id="t",
            elapsed_ms=1.0,
        )


def _rec(path, *, collection="default", file_hash="x", status="success", **extra) -> dict:
    record = {
        "source_path": str(path),
        "collection": collection,
        "file_hash": file_hash,
        "status": status,
        "failed_stage": None,
        "error_msg": None,
        "embedding_version": None,
    }
    record.update(extra)
    return record


@pytest.fixture
def corpus(tmp_path):
    """docs/ 下 new.pdf / changed.pdf / same.pdf 三个现存文件 + 外部 other/。"""
    docs = tmp_path / "docs"
    docs.mkdir()
    files = {}
    for name in ("new.pdf", "changed.pdf", "same.pdf"):
        p = docs / name
        p.write_bytes(f"content of {name}".encode())
        files[name] = p
    other = tmp_path / "other"
    other.mkdir()
    (other / "outside.pdf").write_bytes(b"outside")
    files["outside"] = other / "outside.pdf"
    files["gone"] = docs / "gone.pdf"  # 不存在于磁盘
    files["docs"] = docs
    return files


def _standard_history(corpus) -> list[dict]:
    return [
        _rec(corpus["changed.pdf"], file_hash="stale-hash"),
        _rec(corpus["same.pdf"], file_hash=_sha(corpus["same.pdf"])),
        _rec(corpus["gone"], file_hash="h-gone"),
        # 不同 collection 的同目录记录（已消失）：不得被本 collection 处置
        _rec(corpus["docs"] / "gone_other.pdf", collection="other", file_hash="h-o"),
        # 范围外（已消失）：不得被 --path docs 处置
        _rec(corpus["outside"].parent / "gone_outside.pdf", file_hash="h-out"),
    ]


def _standard_store(corpus) -> list[_Doc]:
    return [
        _Doc(str(corpus["changed.pdf"]), "default"),
        _Doc(str(corpus["same.pdf"]), "default"),
        _Doc(str(corpus["gone"]), "default"),
        _Doc(str(corpus["docs"] / "gone_other.pdf"), "other"),
        _Doc(str(corpus["outside"].parent / "gone_outside.pdf"), "default"),
    ]


def _actions(plan) -> dict[str, str]:
    return {Path(e.path).name: e.action for e in plan.entries}


# ---------------------------------------------------------------------------
# collect_and_diff 分类
# ---------------------------------------------------------------------------


def test_plan_classifies_new_modified_unchanged_deleted(corpus):
    plan = cli.collect_and_diff(
        corpus["docs"],
        "default",
        integrity=FakeIntegrity(_standard_history(corpus)),
        store_sources=[d.source_path for d in _standard_store(corpus) if d.collection == "default"],
    )
    assert _actions(plan) == {
        "new.pdf": "new",
        "changed.pdf": "modified",
        "same.pdf": "unchanged",
        "gone.pdf": "deleted",
    }


def test_plan_failed_record_with_same_hash_is_modified(corpus):
    history = [
        _rec(
            corpus["same.pdf"],
            file_hash=_sha(corpus["same.pdf"]),
            status="failed",
            failed_stage="embed",
        )
    ]
    plan = cli.collect_and_diff(corpus["same.pdf"], "default", integrity=FakeIntegrity(history))
    [entry] = plan.entries
    assert entry.action == cli.ACTION_MODIFIED
    assert "failed" in entry.reason and "embed" in entry.reason
    assert entry.force is False


def test_plan_success_but_missing_in_store_is_forced_modified(corpus):
    history = [_rec(corpus["same.pdf"], file_hash=_sha(corpus["same.pdf"]))]
    plan = cli.collect_and_diff(
        corpus["same.pdf"], "default", integrity=FakeIntegrity(history), store_sources=[]
    )
    [entry] = plan.entries
    assert entry.action == cli.ACTION_MODIFIED
    assert entry.force is True
    assert plan.warnings


def test_plan_without_store_lookup_trusts_history(corpus):
    history = [_rec(corpus["same.pdf"], file_hash=_sha(corpus["same.pdf"]))]
    plan = cli.collect_and_diff(
        corpus["same.pdf"], "default", integrity=FakeIntegrity(history), store_sources=None
    )
    assert [e.action for e in plan.entries] == [cli.ACTION_UNCHANGED]


def test_plan_store_only_orphan_is_deleted(corpus):
    orphan = corpus["docs"] / "orphan.pdf"
    plan = cli.collect_and_diff(
        corpus["docs"], "default", integrity=FakeIntegrity([]), store_sources=[str(orphan)]
    )
    assert _actions(plan)["orphan.pdf"] == cli.ACTION_DELETED


@pytest.mark.parametrize(
    "recorded, current, expected",
    [
        ("emb-old", "emb-new", "stale"),
        ("emb-new", "emb-new", "unchanged"),
        ("legacy", "emb-new", "unchanged"),
        (None, "emb-new", "unchanged"),
        ("emb-old", None, "unchanged"),
    ],
)
def test_plan_embedding_version_stale(corpus, recorded, current, expected):
    history = [
        _rec(corpus["same.pdf"], file_hash=_sha(corpus["same.pdf"]), embedding_version=recorded)
    ]
    plan = cli.collect_and_diff(
        corpus["same.pdf"],
        "default",
        integrity=FakeIntegrity(history),
        current_embedding_version=current,
    )
    assert [e.action for e in plan.entries] == [expected]


@pytest.mark.parametrize("policy, needle", [("stale", "skipped"), ("rebuild", "full rebuild")])
def test_render_plan_stale_section_follows_policy(corpus, policy, needle):
    history = [_rec(corpus["same.pdf"], file_hash="h", embedding_version="old")]
    plan = cli.collect_and_diff(
        corpus["same.pdf"],
        "default",
        integrity=FakeIntegrity(history),
        current_embedding_version="new",
        on_embedding_mismatch=policy,
    )
    text = cli.render_plan(plan, mode="sync", dry_run=True)
    assert "dry-run" in text
    assert f"policy={policy}" in text
    assert needle in text


def test_plan_scope_excludes_other_collection_and_outside_path(corpus):
    plan = cli.collect_and_diff(
        corpus["docs"],
        "default",
        integrity=FakeIntegrity(_standard_history(corpus)),
        include_ingest=False,
    )
    assert [Path(e.path).name for e in plan.by_action(cli.ACTION_DELETED)] == ["gone.pdf"]
    assert plan.by_action(cli.ACTION_NEW) == []


def test_plan_dir_prefix_does_not_match_sibling_with_same_prefix(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    sibling = tmp_path / "docs2" / "x.pdf"  # "docs2" startswith "docs" 但不在范围内
    plan = cli.collect_and_diff(
        docs, "default", integrity=FakeIntegrity([_rec(sibling)]), include_ingest=False
    )
    assert plan.entries == []


def test_plan_existing_but_unscanned_file_is_left_untouched(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    note = docs / "note.md"  # 不受支持扩展名：不被扫描，但仍在磁盘上
    note.write_text("x")
    plan = cli.collect_and_diff(docs, "default", integrity=FakeIntegrity([_rec(note)]))
    assert plan.entries == []
    assert any("not scanned" in w for w in plan.warnings)


# ---------------------------------------------------------------------------
# main：--dry-run
# ---------------------------------------------------------------------------


def _main(argv, *, pipeline=None, integrity=None, manager=None):
    out: list[str] = []
    code = cli.main(
        argv,
        pipeline=pipeline or FakePipeline(),
        out=out.append,
        integrity_checker=integrity,
        document_manager=manager,
    )
    return code, "\n".join(out)


@pytest.mark.parametrize("mode", ["--sync", "--prune"])
def test_dry_run_writes_nothing(corpus, mode):
    pipeline = FakePipeline()
    integrity = FakeIntegrity(_standard_history(corpus))
    manager = FakeManager(_standard_store(corpus))
    code, text = _main(
        ["--path", str(corpus["docs"]), mode, "--dry-run"],
        pipeline=pipeline,
        integrity=integrity,
        manager=manager,
    )
    assert code == cli.EXIT_OK
    assert pipeline.calls == []
    assert manager.deleted == []
    assert integrity.writes == []
    assert "dry-run, nothing written" in text
    assert "gone.pdf" in text


# ---------------------------------------------------------------------------
# main：--sync / --prune
# ---------------------------------------------------------------------------


def test_sync_ingests_new_and_modified_and_deletes_missing(corpus):
    pipeline = FakePipeline()
    manager = FakeManager(_standard_store(corpus))
    code, text = _main(
        ["--path", str(corpus["docs"]), "--sync"],
        pipeline=pipeline,
        integrity=FakeIntegrity(_standard_history(corpus)),
        manager=manager,
    )
    assert code == cli.EXIT_OK
    ingested = sorted(Path(c["path"]).name for c in pipeline.calls)
    assert ingested == ["changed.pdf", "new.pdf"]
    assert all(c["collection"] == "default" for c in pipeline.calls)
    assert manager.deleted == [(str(corpus["gone"]), "default")]
    assert "ingest summary" in text and "prune summary" in text


def test_sync_forces_reingest_when_store_missing(corpus):
    pipeline = FakePipeline()
    history = [_rec(corpus["same.pdf"], file_hash=_sha(corpus["same.pdf"]))]
    code, _ = _main(
        ["--path", str(corpus["same.pdf"]), "--sync"],
        pipeline=pipeline,
        integrity=FakeIntegrity(history),
        manager=FakeManager([]),
    )
    assert code == cli.EXIT_OK
    assert pipeline.calls == [
        {"path": str(corpus["same.pdf"]), "collection": "default", "force": True}
    ]


def test_sync_uses_collection_flag(corpus):
    pipeline = FakePipeline()
    manager = FakeManager(_standard_store(corpus))
    code, _ = _main(
        ["--path", str(corpus["docs"]), "--collection", "other", "--sync"],
        pipeline=pipeline,
        integrity=FakeIntegrity(_standard_history(corpus)),
        manager=manager,
    )
    assert code == cli.EXIT_OK
    # collection=other 下没有任何历史：三个文件都是 new；只清理 other 的消失文档
    assert sorted(Path(c["path"]).name for c in pipeline.calls) == [
        "changed.pdf", "new.pdf", "same.pdf",
    ]
    assert {c["collection"] for c in pipeline.calls} == {"other"}
    assert manager.deleted == [(str(corpus["docs"] / "gone_other.pdf"), "other")]


def test_sync_store_lookup_failure_falls_back_to_history(corpus):
    manager = FakeManager(list_error=RuntimeError("chroma down"))
    code, text = _main(
        ["--path", str(corpus["docs"]), "--sync", "--dry-run"],
        integrity=FakeIntegrity(_standard_history(corpus)),
        manager=manager,
    )
    assert code == cli.EXIT_OK
    assert "store reverse lookup failed" in text


def test_prune_only_deletes_scoped_to_path_and_collection(corpus):
    pipeline = FakePipeline()
    manager = FakeManager(_standard_store(corpus))
    code, _ = _main(
        ["--path", str(corpus["docs"]), "--prune"],
        pipeline=pipeline,
        integrity=FakeIntegrity(_standard_history(corpus)),
        manager=manager,
    )
    assert code == cli.EXIT_OK
    assert pipeline.calls == []
    assert manager.deleted == [(str(corpus["gone"]), "default")]


def test_prune_vanished_directory_still_cleans_records(tmp_path):
    vanished = tmp_path / "removed_dir"
    history = [_rec(vanished / "a.pdf"), _rec(tmp_path / "keep" / "b.pdf")]
    manager = FakeManager()
    code, text = _main(
        ["--path", str(vanished), "--prune"],
        integrity=FakeIntegrity(history),
        manager=manager,
    )
    assert code == cli.EXIT_OK
    assert manager.deleted == [(str(vanished / "a.pdf"), "default")]
    assert "no longer exists" in text


def test_sync_missing_path_is_usage_error(tmp_path):
    code, _ = _main(
        ["--path", str(tmp_path / "nope"), "--sync"],
        integrity=FakeIntegrity(),
        manager=FakeManager(),
    )
    assert code == cli.EXIT_USAGE


def test_sync_exit_one_when_ingest_fails(corpus):
    pipeline = FakePipeline(fail_substrings=("new.pdf",))
    code, text = _main(
        ["--path", str(corpus["docs"]), "--sync"],
        pipeline=pipeline,
        integrity=FakeIntegrity(_standard_history(corpus)),
        manager=FakeManager(_standard_store(corpus)),
    )
    assert code == cli.EXIT_FAILURE
    assert len(pipeline.calls) == 2  # 单文件失败不中断批次
    assert "failed files" in text


def test_prune_exit_one_when_delete_fails_but_batch_continues(corpus):
    history = _standard_history(corpus) + [_rec(corpus["docs"] / "gone2.pdf")]
    manager = FakeManager(fail_substrings=("gone.pdf",))
    code, text = _main(
        ["--path", str(corpus["docs"]), "--prune"],
        integrity=FakeIntegrity(history),
        manager=manager,
    )
    assert code == cli.EXIT_FAILURE
    assert manager.deleted == [(str(corpus["docs"] / "gone2.pdf"), "default")]
    assert "1 failed" in text


def test_sync_injected_fake_pipeline_without_components_is_usage_error(corpus):
    # 只注入 integrity、未注入 manager 且 fake pipeline 无存储组件 → 无从装配删除器
    code, text = _main(
        ["--path", str(corpus["docs"]), "--sync"], integrity=FakeIntegrity()
    )
    assert code == cli.EXIT_USAGE
    assert "inject" in text


# ---------------------------------------------------------------------------
# main：--retry-failed
# ---------------------------------------------------------------------------


def _retry_history(corpus) -> list[dict]:
    return [
        _rec(corpus["changed.pdf"], status="failed", failed_stage="embed", error_msg="timeout"),
        _rec(corpus["same.pdf"], status="success"),
        _rec(corpus["new.pdf"], collection="other", status="failed", failed_stage="load"),
        _rec(corpus["outside"], status="failed", failed_stage="store"),
        _rec(corpus["gone"], status="failed", failed_stage="load"),
        {**_rec("", status="failed"), "source_path": None, "file_hash": "deadbeef" * 8},
    ]


def test_select_failed_records_filters_status_and_collection(corpus):
    records, warnings = cli.select_failed_records(
        FakeIntegrity(_retry_history(corpus)), "default"
    )
    names = [Path(r["source_path"]).name for r in records]
    assert sorted(names) == ["changed.pdf", "gone.pdf", "outside.pdf"]
    assert len(warnings) == 1 and "unknown source path" in warnings[0]


def test_select_failed_records_scoped_to_path(corpus):
    records, _ = cli.select_failed_records(
        FakeIntegrity(_retry_history(corpus)), "default", corpus["docs"]
    )
    assert sorted(Path(r["source_path"]).name for r in records) == ["changed.pdf", "gone.pdf"]


def test_retry_failed_reruns_only_failed_existing_files(corpus):
    pipeline = FakePipeline()
    code, text = _main(
        ["--retry-failed"],
        pipeline=pipeline,
        integrity=FakeIntegrity(_retry_history(corpus)),
    )
    assert code == cli.EXIT_OK
    assert sorted(Path(c["path"]).name for c in pipeline.calls) == ["changed.pdf", "outside.pdf"]
    assert {c["collection"] for c in pipeline.calls} == {"default"}
    assert "previously failed at stage 'embed'" in text
    assert "timeout" in text
    assert "no longer on disk" in text and "gone.pdf" in text


def test_retry_failed_with_path_and_collection(corpus):
    pipeline = FakePipeline()
    code, _ = _main(
        ["--retry-failed", "--collection", "other", "--path", str(corpus["docs"])],
        pipeline=pipeline,
        integrity=FakeIntegrity(_retry_history(corpus)),
    )
    assert code == cli.EXIT_OK
    assert pipeline.calls == [
        {"path": str(corpus["new.pdf"]), "collection": "other", "force": False}
    ]


def test_retry_failed_nothing_to_do_exit_zero(corpus):
    pipeline = FakePipeline()
    code, text = _main(
        ["--retry-failed", "--collection", "empty"],
        pipeline=pipeline,
        integrity=FakeIntegrity(_retry_history(corpus)),
    )
    assert code == cli.EXIT_OK
    assert pipeline.calls == []
    assert "no retryable failed records" in text


def test_retry_failed_exit_one_when_retry_fails(corpus):
    code, _ = _main(
        ["--retry-failed"],
        pipeline=FakePipeline(fail_substrings=("changed.pdf",)),
        integrity=FakeIntegrity(_retry_history(corpus)),
    )
    assert code == cli.EXIT_FAILURE


def test_retry_failed_missing_path_is_usage_error(tmp_path):
    code, _ = _main(
        ["--retry-failed", "--path", str(tmp_path / "nope")], integrity=FakeIntegrity()
    )
    assert code == cli.EXIT_USAGE


def test_retry_failed_with_real_sqlite_history(tmp_path):
    from libs.loader.file_integrity import SQLiteIntegrityChecker

    ok = tmp_path / "ok.pdf"
    bad = tmp_path / "bad.pdf"
    ok.write_bytes(b"ok")
    bad.write_bytes(b"bad")
    checker = SQLiteIntegrityChecker(db_path=str(tmp_path / "history.db"))
    try:
        checker.mark_success(_sha(ok), str(ok), collection="c")
        checker.mark_failed(
            _sha(bad), "boom", source_path=str(bad), collection="c", failed_stage="split"
        )
        checker.mark_failed(_sha(bad), "boom", source_path=str(bad), collection="d")
        pipeline = FakePipeline()
        code, text = _main(
            ["--retry-failed", "--collection", "c"], pipeline=pipeline, integrity=checker
        )
    finally:
        checker.close()
    assert code == cli.EXIT_OK
    assert pipeline.calls == [{"path": str(bad), "collection": "c", "force": False}]
    assert "'split'" in text


# ---------------------------------------------------------------------------
# argparse 校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--path", "x", "--sync", "--prune"],
        ["--path", "x", "--sync", "--retry-failed"],
        ["--path", "x", "--prune", "--retry-failed"],
        ["--path", "x", "--dry-run"],
        ["--retry-failed", "--dry-run"],
        ["--path", "x", "--sync", "--force"],
        ["--path", "x", "--prune", "--force"],
        ["--sync"],
        ["--prune"],
        [],
    ],
)
def test_parse_args_rejects_invalid_combinations(argv):
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(argv)
    assert excinfo.value.code == cli.EXIT_USAGE


@pytest.mark.parametrize(
    "argv, attrs",
    [
        (["--path", "x", "--sync"], {"sync": True, "dry_run": False}),
        (["--path", "x", "--prune", "--dry-run"], {"prune": True, "dry_run": True}),
        (["--retry-failed"], {"retry_failed": True, "path": None}),
        (["--retry-failed", "--force"], {"retry_failed": True, "force": True}),
    ],
)
def test_parse_args_accepts_valid_combinations(argv, attrs):
    args = cli.parse_args(argv)
    for name, value in attrs.items():
        assert getattr(args, name) == value
