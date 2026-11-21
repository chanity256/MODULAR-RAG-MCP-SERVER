"""AC1: collection 定界路径集推导单元测试（AGENTIC_COLLECTION_ISOLATION_SPEC，离线）。

覆盖 spec §4 AC1 五组：记录筛选、相对路径按项目根解析、逐条剔除互不
影响、空定界集、realpath 去重与排序确定性；另含 warning 日志纪律与
真实 ``SQLiteIntegrityChecker`` 句柄的鸭子类型契约验证。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from libs.agentic_search.collection_paths import CollectionPathResolver
from libs.agentic_search.path_guard import PathGuard
from libs.loader.file_integrity import SQLiteIntegrityChecker


class FakeHistory:
    """fake 历史表句柄：按 ``list_processed()`` 契约返回记录列表。"""

    def __init__(self, records: list[dict]) -> None:
        self._records = records

    def list_processed(self) -> list[dict]:
        return list(self._records)


def _rec(source_path: str | None, collection: str = "A", status: str = "success") -> dict:
    return {"source_path": source_path, "collection": collection, "status": status}


@pytest.fixture()
def tree(tmp_path: Path) -> dict[str, Path]:
    """项目根布局：白名单根 ``data/documents``（含两个文件）+ 白名单外目录。"""
    docs = tmp_path / "data" / "documents"
    docs.mkdir(parents=True)
    (docs / "a.md").write_text("alpha", encoding="utf-8")
    (docs / "b.md").write_text("beta", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "c.md").write_text("gamma", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return {"root": tmp_path, "docs": docs, "outside": outside, "elsewhere": elsewhere}


class TestRecordSelection:
    def test_only_success_and_matching_collection(self, tree):
        history = FakeHistory(
            [
                _rec(str(tree["docs"] / "a.md"), collection="A"),
                _rec(str(tree["docs"] / "b.md"), collection="B"),  # 其他 collection
                _rec(str(tree["docs"] / "b.md"), collection="A", status="failed"),  # failed
                _rec(None, collection="A"),  # v1 迁移占位
            ]
        )
        resolver = CollectionPathResolver(history, PathGuard([tree["docs"]]), tree["root"])
        assert resolver.resolve("A") == [str((tree["docs"] / "a.md").resolve())]

    def test_no_records_for_collection_returns_empty(self, tree):
        history = FakeHistory([_rec(str(tree["docs"] / "a.md"), collection="B")])
        resolver = CollectionPathResolver(history, PathGuard([tree["docs"]]), tree["root"])
        assert resolver.resolve("A") == []

    def test_empty_history_returns_empty(self, tree):
        resolver = CollectionPathResolver(FakeHistory([]), PathGuard([tree["docs"]]), tree["root"])
        assert resolver.resolve("A") == []

    def test_blank_collection_rejected(self, tree):
        resolver = CollectionPathResolver(FakeHistory([]), PathGuard([tree["docs"]]), tree["root"])
        for bad in ("", "   ", None, 42):
            with pytest.raises(ValueError, match="non-empty string"):
                resolver.resolve(bad)  # type: ignore[arg-type]


class TestProjectRootResolution:
    def test_relative_source_path_resolved_against_project_root(self, tree, monkeypatch):
        """相对 ``source_path`` 按注入的项目根解析，与启动目录无关。"""
        history = FakeHistory([_rec("data/documents/a.md", collection="A")])
        resolver = CollectionPathResolver(history, PathGuard([tree["docs"]]), tree["root"])
        monkeypatch.chdir(tree["elsewhere"])
        assert resolver.resolve("A") == [str((tree["docs"] / "a.md").resolve())]
        monkeypatch.chdir(tree["root"])  # 换目录重跑，结果一致
        assert resolver.resolve("A") == [str((tree["docs"] / "a.md").resolve())]

    def test_default_project_root_from_env(self, tree, monkeypatch):
        """未注入 project_root 时经 ``resolve_project_root()``（RAG_PROJECT_ROOT 优先）。"""
        history = FakeHistory([_rec("data/documents/b.md", collection="A")])
        monkeypatch.setenv("RAG_PROJECT_ROOT", str(tree["root"]))
        monkeypatch.chdir(tree["elsewhere"])
        resolver = CollectionPathResolver(history, PathGuard([tree["docs"]]))
        assert resolver.resolve("A") == [str((tree["docs"] / "b.md").resolve())]


class TestPerRecordFiltering:
    def test_unqualified_records_dropped_independently(self, tree, monkeypatch, caplog):
        """白名单外 / 不存在 / denied / 符号链接逃逸 / URL 逐条剔除，只剩合格项。"""
        link = tree["docs"] / "link.md"
        link.symlink_to(tree["outside"] / "c.md")  # 符号链接逃逸
        (tree["docs"] / ".env").write_text("KEY=1", encoding="utf-8")  # denied（默认表）
        history = FakeHistory(
            [
                _rec(str(tree["docs"] / "a.md")),  # 合格
                _rec(str(tree["outside"] / "c.md")),  # 白名单外
                _rec(str(tree["docs"] / "missing.md")),  # 白名单内但不存在
                _rec(str(tree["docs"] / ".env")),  # 命中 denied_substrings
                _rec(str(link)),  # 符号链接逃逸
                _rec("https://example.com/doc.pdf"),  # URL 形态
            ]
        )
        resolver = CollectionPathResolver(history, PathGuard([tree["docs"]]), tree["root"])
        monkeypatch.chdir(tree["elsewhere"])
        with caplog.at_level(logging.WARNING, logger="libs.agentic_search.collection_paths"):
            assert resolver.resolve("A") == [str((tree["docs"] / "a.md").resolve())]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "A" in message and "5" in message  # collection 名 + 剔除条数
        assert "c.md" not in message and "missing" not in message  # 不含路径内容


class TestDedupAndOrder:
    def test_realpath_dedup_sorted_deterministic(self, tree):
        """同一文件的相对与绝对记录去重为一条；输出升序；重复调用一致。"""
        history = FakeHistory(
            [
                _rec("data/documents/b.md"),  # 相对形态
                _rec(str(tree["docs"] / "b.md")),  # 同文件的绝对形态
                _rec(str(tree["docs"] / "a.md")),
            ]
        )
        resolver = CollectionPathResolver(history, PathGuard([tree["docs"]]), tree["root"])
        expected = sorted(
            {str((tree["docs"] / "a.md").resolve()), str((tree["docs"] / "b.md").resolve())}
        )
        first = resolver.resolve("A")
        assert first == expected
        assert resolver.resolve("A") == first


class TestRealSqliteHistoryHandle:
    def test_sqlite_checker_satisfies_handle_contract(self, tree, tmp_path):
        """真实 ``SQLiteIntegrityChecker`` 即满足句柄协议（记录形状契约）。"""
        checker = SQLiteIntegrityChecker(db_path=tmp_path / "history.db")
        try:
            checker.mark_success("hash-a", str(tree["docs"] / "a.md"), collection="A")
            checker.mark_success("hash-b", str(tree["docs"] / "b.md"), collection="B")
            checker.mark_failed(
                "hash-c", "boom", source_path=str(tree["docs"] / "missing.md"), collection="A"
            )
            resolver = CollectionPathResolver(checker, PathGuard([tree["docs"]]), tree["root"])
            assert resolver.resolve("A") == [str((tree["docs"] / "a.md").resolve())]
        finally:
            checker.close()
