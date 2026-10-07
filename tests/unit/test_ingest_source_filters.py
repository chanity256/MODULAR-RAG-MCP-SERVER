"""IR7.4：目录扫描 include/exclude 过滤（``collect_files`` / CLI 汇总 / 同步）。

纯离线：glob 匹配用参数化 pattern×path 矩阵；CLI/同步模式注入 fake
pipeline 与配置；vault 夹具摄取走真实 pipeline + fake embedding（存储全落
``tmp_path``）。零网络、零子进程、零真实持久化。

覆盖（对齐 INGESTION_ROBUSTNESS_SPEC 3.8.2）：
- glob 匹配语义：目录段参与匹配（``.obsidian/attachments/x.png`` 命中
  ``.obsidian/**``）、include 空 = 全部受理、exclude 优先于 include。
- 默认排除 ``.obsidian``/``.trash``/``templates``（经默认 Settings）。
- 过滤先于扩展名筛选：被过滤文件无论扩展名均计入 ``filtered``。
- 显式单文件 ``--path`` 不过滤。
- ``IngestSummary.filtered`` 计数、``to_dict`` 与 ``render_summary`` 行。
- ``--sync`` 遵守同一套过滤（不摄取被过滤路径；已入库的被过滤路径按
  deleted 语义清理）；``--dry-run`` 计划只体现过滤后清单且零写入。
- 集成：默认配置摄取 ``tests/fixtures/vault/`` → filtered==2，
  ``.obsidian/`` 与 ``templates/`` 未被摄取，6 篇笔记正常入库。
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.settings import load_settings
from ingestion.pipeline import IngestionResult
from tests.unit.test_ingestion_loader_routing import _build_routing_pipeline
from tests.unit.test_ingestion_pipeline import _YAML, FakeVectorStore

pytestmark = pytest.mark.unit

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest.py"
_MODULE_NAME = "scripts_ingest_source_filters"
_VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "vault"

#: 与 settings.py 缺省 ``exclude_patterns`` 一致（DR 校验：改动须同步）。
_DEFAULT_EXCLUDES = [".obsidian/**", ".trash/**", "templates/**"]


def _load_cli():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _default_settings(tmp_path: Path):
    """无 ``source_filters`` 节的最小配置 → 走默认排除清单。"""
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(_YAML, encoding="utf-8")
    return load_settings(cfg)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class RecordingPipeline:
    """记录 ``run`` 调用并返回成功结果的最小 fake。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, source_path, collection="default", on_progress=None, *, force=False, trace=None):
        self.calls.append(str(source_path))
        return IngestionResult(
            source_path=str(source_path),
            collection=collection,
            doc_hash="a" * 64,
            status="success",
            chunks=1,
            written=1,
            trace_id="t",
            elapsed_ms=0.1,
        )


class FakeIntegrity:
    """只读历史表 fake（对齐 test_ingest_sync 的同名 fake 语义）。"""

    def __init__(self, records=None):
        self.records = list(records or [])

    def list_processed(self):
        return [dict(r) for r in self.records]

    def compute_sha256(self, path: str) -> str:
        return _sha(Path(path))


@dataclass
class _Doc:
    source_path: str
    collection: str = "c"


class FakeManager:
    def __init__(self, docs=()):
        self.docs = list(docs)
        self.deleted: list[tuple[str, str]] = []

    def list_documents(self, collection=None):
        return [d for d in self.docs if collection is None or d.collection == collection]

    def delete_document(self, source_path, collection):
        self.deleted.append((source_path, collection))
        return SimpleNamespace(
            chroma_deleted=1, bm25_deleted=1, images_deleted=0, integrity_removed=True
        )


def _rec(path, *, collection="c", **extra) -> dict:
    record = {
        "source_path": str(path),
        "collection": collection,
        "file_hash": "x",
        "status": "success",
        "failed_stage": None,
        "error_msg": None,
        "embedding_version": None,
    }
    record.update(extra)
    return record


# ---------------------------------------------------------------------------
# glob 匹配语义（pattern×path 参数化矩阵）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "include", "exclude", "expect_filtered"),
    [
        # 目录段参与匹配：深层路径命中目录 glob
        (".obsidian/attachments/x.png", [], [".obsidian/**"], True),
        (".obsidian/app.json", [], [".obsidian/**"], True),
        (".trash/nested/deep/a.md", [], [".trash/**"], True),
        ("templates/sub/daily.md", [], ["templates/**"], True),
        # 默认排除清单逐一命中；范围外不误伤
        ("notes/a.md", [], _DEFAULT_EXCLUDES, False),
        (".trash/old.md", [], _DEFAULT_EXCLUDES, True),
        ("templates/t.md", [], _DEFAULT_EXCLUDES, True),
        # include 空 = 全部受理
        ("notes/a.md", [], [], False),
        ("anything/b.md", [], [], False),
        # include 非空：命中受理 / 未命中排除
        ("notes/a.md", ["notes/**"], [], False),
        ("other/b.md", ["notes/**"], [], True),
        # include 与 exclude 同时命中：exclude 优先
        ("notes/a.md", ["notes/**"], ["notes/a.md"], True),
        ("notes/a.md", ["**"], [".obsidian/**", "notes/a.md"], True),
        # 精确相对路径 pattern
        ("notes/skip.md", [], ["notes/skip.md"], True),
        ("notes/keep.md", [], ["notes/skip.md"], False),
        # * 跨目录段（fnmatch 语义）：单 * 同样命中子目录内路径
        ("a/b/c.md", [], ["a/*"], True),
    ],
)
def test_filter_matrix(tmp_path, rel, include, exclude, expect_filtered):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")

    scan = cli.collect_files_detailed(
        tmp_path, include_patterns=include, exclude_patterns=exclude
    )

    assert (target not in scan.files) is expect_filtered
    assert scan.filtered == (1 if expect_filtered else 0)


def test_filtered_counted_regardless_of_extension(tmp_path):
    """过滤先于扩展名筛选：被过滤路径视为不存在，非支持扩展名同样计数
    （vault 场景 filtered==2 的依据：.obsidian/app.json + templates/daily.md）。"""
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "daily.md").write_text("# t", encoding="utf-8")

    scan = cli.collect_files_detailed(tmp_path, exclude_patterns=_DEFAULT_EXCLUDES)

    assert scan.files == []
    assert scan.filtered == 2


def test_default_settings_excludes_applied(tmp_path):
    """无 source_filters 节的配置走默认排除清单（.obsidian/.trash/templates）。"""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("# a", encoding="utf-8")
    for rel in (".obsidian/x.md", ".trash/y.md", "templates/z.md"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")

    settings = _default_settings(tmp_path)
    filters = settings.ingestion.source_filters
    assert filters.include_patterns == []
    assert filters.exclude_patterns == _DEFAULT_EXCLUDES

    scan = cli.collect_files_detailed(
        tmp_path,
        include_patterns=filters.include_patterns,
        exclude_patterns=filters.exclude_patterns,
    )

    assert scan.files == [tmp_path / "notes" / "a.md"]
    assert scan.filtered == 3


def test_explicit_single_file_bypasses_filters(tmp_path):
    """``--path`` 显式指向单文件时不过滤（即使在排除目录内）。"""
    d = tmp_path / "templates"
    d.mkdir()
    target = d / "a.md"
    target.write_text("# a", encoding="utf-8")

    scan = cli.collect_files_detailed(target, exclude_patterns=["templates/**"])

    assert scan.files == [target]
    assert scan.filtered == 0


def test_collect_files_thin_wrapper_keeps_list_contract(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"a")
    (tmp_path / ".obsidian" / "b.pdf").parent.mkdir()
    (tmp_path / ".obsidian" / "b.pdf").write_bytes(b"b")

    assert cli.collect_files(tmp_path, exclude_patterns=[".obsidian/**"]) == [
        tmp_path / "a.pdf"
    ]
    # 不过滤时与既有行为一致（Path 排序：隐藏目录段在前）
    assert cli.collect_files(tmp_path) == [
        tmp_path / ".obsidian" / "b.pdf",
        tmp_path / "a.pdf",
    ]


# ---------------------------------------------------------------------------
# CLI 汇总：filtered 计数
# ---------------------------------------------------------------------------


def test_summary_carries_filtered_count_and_renders_line():
    summary = cli.aggregate(
        "c",
        [cli.FileOutcome(path="a.pdf", status="success", chunks=1, written=1)],
        filtered=2,
    )

    assert summary.filtered == 2
    assert summary.total == 1  # 被过滤文件不计入 total/outcomes
    assert summary.to_dict()["filtered"] == 2

    text = cli.render_summary(summary)
    assert "filtered: 2 (excluded by source_filters)" in text


def test_run_ingest_passes_filtered_through():
    summary = cli.run_ingest(
        RecordingPipeline(), [Path("a.pdf")], "c", filtered=3, out=lambda _s: None
    )

    assert summary.filtered == 3
    assert summary.ingested == 1


def test_main_applies_settings_filters_and_reports_count(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"a")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "h.pdf").write_bytes(b"h")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "t.md").write_text("# t", encoding="utf-8")
    settings = _default_settings(tmp_path)
    pipeline = RecordingPipeline()
    lines: list[str] = []

    code = cli.main(
        ["--path", str(tmp_path), "--collection", "c"],
        pipeline=pipeline,
        settings=settings,
        out=lines.append,
    )

    assert code == cli.EXIT_OK == 0
    assert pipeline.calls == [str(tmp_path / "a.pdf")]
    joined = "\n".join(lines)
    assert "filtered: 2 (excluded by source_filters)" in joined
    assert ".obsidian" not in joined  # 被过滤文件不出现在逐文件行
    assert "templates" not in joined


def test_main_without_settings_keeps_unfiltered_baseline(tmp_path):
    """注入无配置的 fake pipeline（单测惯例）时保持 IR7.4 前的全受理基线。"""
    (tmp_path / "a.pdf").write_bytes(b"a")
    (tmp_path / ".obsidian" / "h.pdf").parent.mkdir()
    (tmp_path / ".obsidian" / "h.pdf").write_bytes(b"h")
    pipeline = RecordingPipeline()
    lines: list[str] = []

    code = cli.main(
        ["--path", str(tmp_path)], pipeline=pipeline, out=lines.append
    )

    assert code == 0
    assert sorted(pipeline.calls) == sorted(
        [str(tmp_path / "a.pdf"), str(tmp_path / ".obsidian" / "h.pdf")]
    )
    assert "filtered: 0 (excluded by source_filters)" in "\n".join(lines)


# ---------------------------------------------------------------------------
# 同步模式（--sync / --dry-run）遵守同一套过滤
# ---------------------------------------------------------------------------


def _sync_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """docs/new.pdf（受理）+ docs/.obsidian/h.pdf（被过滤，仍在磁盘）。"""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "new.pdf").write_bytes(b"n")
    hidden = docs / ".obsidian" / "h.pdf"
    hidden.parent.mkdir()
    hidden.write_bytes(b"h")
    return docs, docs / "new.pdf", hidden


def test_sync_skips_filtered_files(tmp_path):
    docs, new_pdf, hidden = _sync_tree(tmp_path)
    pipeline = RecordingPipeline()
    manager = FakeManager()
    lines: list[str] = []

    code = cli.main(
        ["--path", str(docs), "--collection", "c", "--sync"],
        pipeline=pipeline,
        out=lines.append,
        integrity_checker=FakeIntegrity(),
        document_manager=manager,
        settings=_default_settings(tmp_path),
    )

    assert code == 0
    assert pipeline.calls == [str(new_pdf)]  # 被过滤路径视为不存在，不摄取
    assert manager.deleted == []
    joined = "\n".join(lines)
    assert str(hidden) not in joined


def test_sync_prunes_ingested_filtered_files(tmp_path):
    """已入库但被过滤的路径视为不存在 → 按既有 deleted 语义清理。"""
    docs, new_pdf, hidden = _sync_tree(tmp_path)
    settings = _default_settings(tmp_path)
    pipeline = RecordingPipeline()
    manager = FakeManager(docs=[_Doc(str(hidden)), _Doc(str(new_pdf))])
    integrity = FakeIntegrity([_rec(hidden), _rec(new_pdf, file_hash=_sha(new_pdf))])
    lines: list[str] = []

    code = cli.main(
        ["--path", str(docs), "--collection", "c", "--sync"],
        pipeline=pipeline,
        out=lines.append,
        integrity_checker=integrity,
        document_manager=manager,
        settings=settings,
    )

    assert code == 0
    assert manager.deleted == [(str(hidden), "c")]
    assert pipeline.calls == []  # new.pdf 哈希未变 → unchanged；hidden 不摄取
    assert f"- {hidden}" in "\n".join(lines)


def test_sync_dry_run_plan_reflects_filters_and_writes_nothing(tmp_path):
    docs, new_pdf, hidden = _sync_tree(tmp_path)
    (docs / "templates").mkdir()
    template = docs / "templates" / "t.md"
    template.write_text("# t", encoding="utf-8")
    pipeline = RecordingPipeline()
    manager = FakeManager()
    lines: list[str] = []

    code = cli.main(
        ["--path", str(docs), "--collection", "c", "--sync", "--dry-run"],
        pipeline=pipeline,
        out=lines.append,
        integrity_checker=FakeIntegrity(),
        document_manager=manager,
        settings=_default_settings(tmp_path),
    )

    assert code == 0
    assert pipeline.calls == [] and manager.deleted == []  # dry-run 零写入
    joined = "\n".join(lines)
    assert f"+ {new_pdf}" in joined  # 计划只体现过滤后清单
    assert str(hidden) not in joined
    assert str(template) not in joined


# ---------------------------------------------------------------------------
# 集成：默认配置摄取 tests/fixtures/vault/
# ---------------------------------------------------------------------------


def test_vault_ingestion_with_default_filters(tmp_path):
    """fixtures/vault 全量摄取：filtered==2，.obsidian/ 与 templates/ 未入库，
    6 篇笔记正常摄取（真实 MarkdownLoader + fake embedding，存储落 tmp_path）。"""
    store = FakeVectorStore()
    pipeline, _ = _build_routing_pipeline(tmp_path, store)
    lines: list[str] = []

    code = cli.main(
        ["--path", str(_VAULT), "--collection", "vault"],
        pipeline=pipeline,
        out=lines.append,
    )

    assert code == cli.EXIT_OK == 0
    joined = "\n".join(lines)
    assert "filtered: 2 (excluded by source_filters)" in joined
    assert "6 total | 6 ingested | 0 skipped | 0 failed" in joined

    sources = {rec.metadata["source_path"] for rec in store.records.values()}
    assert len(sources) == 6
    assert all(source.endswith(".md") for source in sources)
    assert all("/.obsidian/" not in source for source in sources)
    assert all("/templates/" not in source for source in sources)
    assert all("/notes/" in source for source in sources)
    # 被过滤文件不产生逐文件行
    assert "app.json" not in joined
    assert "daily.md" not in joined
