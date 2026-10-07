"""``scripts/ingest.py`` CLI 入口单元测试（阶段 C15）。

纯离线：经 importlib 按路径加载 scripts/ingest.py（scripts 非 package），
全部断言基于可导入纯函数（build_parser/parse_args/collect_files/
run_ingest/aggregate/render_summary/main），pipeline 用可控 fake，
零网络、零子进程、零真实持久化。

覆盖：
- 参数解析：--path 必填、--collection/--force/--config 默认值。
- collect_files：单文件 / 目录递归排序 / 扩展名过滤（大小写不敏感）/
  空目录 / 不存在路径 / 单文件扩展名不支持。
- run_ingest：collection/force/on_progress 透传、单文件失败不中断批次。
- aggregate：混合 success/skipped/failed 的计数与总和（失败不计产物）。
- render_summary：计数行 + 失败文件明细。
- main 退出码：0 全成功或全跳过或空目录；1 有失败；2 用法错误。
- SUPPORTED_EXTENSIONS 与 pipeline 扩展名路由的 drift guard。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from ingestion.pipeline import IngestionResult, _SUPPORTED_EXTENSIONS
from ingestion.pipeline import IngestionPipelineError

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
# fake pipeline（记录调用、可配置结果/失败）
# ---------------------------------------------------------------------------


class FakePipeline:
    """具备 ``run(source_path, collection=..., on_progress=..., force=...)``
    签法的可控 fake；按路径前缀决定成功/跳过/失败。"""

    def __init__(self, *, fail_substrings: tuple[str, ...] = ("bad",)) -> None:
        self.calls: list[dict] = []
        self.progress_events: list[tuple] = []
        self._fail_substrings = fail_substrings

    def run(self, source_path, collection="default", on_progress=None, *, force=False, trace=None):
        self.calls.append(
            {"path": str(source_path), "collection": collection, "force": force}
        )
        if on_progress is not None:
            on_progress("load", 1, 1)
            self.progress_events.append((source_path, "load", 1, 1))
        if any(sub in str(source_path) for sub in self._fail_substrings):
            raise IngestionPipelineError(
                f"stage 'load' failed for {source_path}: fake explosion", stage="load"
            )
        unchanged = "skipme" in str(source_path) and not force
        status = "skipped" if unchanged else "success"
        chunks = 0 if unchanged else 3
        return IngestionResult(
            source_path=str(source_path),
            collection=collection,
            doc_hash="a" * 64,
            status=status,
            chunks=chunks,
            images=1 if chunks else 0,
            written=chunks,
            trace_id="trace-0001",
            elapsed_ms=1.5,
        )


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def test_parser_defaults():
    args = cli.parse_args(["--path", "docs"])
    assert args.path == "docs"
    assert args.collection == "default"
    assert args.force is False
    assert args.verbose is False
    assert args.config == str(cli.DEFAULT_CONFIG_PATH)


def test_parser_accepts_all_flags():
    args = cli.parse_args(
        ["--path", "docs", "--collection", "test", "--force", "--verbose"]
    )
    assert args.collection == "test"
    assert args.force is True
    assert args.verbose is True


def test_parser_requires_path():
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args([])
    assert excinfo.value.code == 2


def test_parser_rejects_unknown_flag():
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["--path", "docs", "--nope"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# collect_files
# ---------------------------------------------------------------------------


def test_collect_files_single_file(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert cli.collect_files(pdf) == [pdf]
    assert cli.collect_files(str(pdf)) == [pdf]


def test_collect_files_missing_path_raises():
    with pytest.raises(cli.IngestPathError, match="does not exist"):
        cli.collect_files(tmp_path := "/nonexistent/definitely/missing")  # noqa: F841


def test_collect_files_unsupported_single_file_raises(tmp_path):
    txt = tmp_path / "note.txt"
    txt.write_text("# hi")
    with pytest.raises(cli.IngestPathError, match="unsupported file extension"):
        cli.collect_files(txt)


def test_collect_files_single_markdown_file_accepted(tmp_path):
    """IR7.3：单文件 ``--path x.md`` 受理（此前 .md 未登记）。"""
    md = tmp_path / "note.md"
    md.write_text("# hi")
    assert cli.collect_files(md) == [md]
    markdown = tmp_path / "note.markdown"
    markdown.write_text("# hi")
    assert cli.collect_files(markdown) == [markdown]


def test_collect_files_directory_walk_sorted_and_filtered(tmp_path):
    (tmp_path / "b.pdf").write_bytes(b"b")
    (tmp_path / "a.PDF").write_bytes(b"a")  # 大小写不敏感
    (tmp_path / "notes.txt").write_text("ignore")
    (tmp_path / "readme.md").write_text("collect (IR7.3 登记)")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.pdf").write_bytes(b"c")
    (sub / "deep").mkdir()
    (sub / "deep" / "d.pdf").write_bytes(b"d")

    files = cli.collect_files(tmp_path)

    assert files == [
        tmp_path / "a.PDF",
        tmp_path / "b.pdf",
        tmp_path / "readme.md",
        sub / "c.pdf",
        sub / "deep" / "d.pdf",
    ]


def test_collect_files_empty_directory(tmp_path):
    assert cli.collect_files(tmp_path) == []


# Note: 断言翻转史与哨兵迁移（.md 转正）— 见
# .agents/notes/implemented/testing/2026-10-07-drift-guard-markdown-flip.md
def test_supported_extensions_matches_pipeline_routing():
    """drift guard：入口过滤集合必须与 pipeline Loader 路由一致（IR7.3 起
    含 Markdown 扩展名）。"""
    assert ".md" in _SUPPORTED_EXTENSIONS
    assert ".markdown" in _SUPPORTED_EXTENSIONS
    assert set(cli.SUPPORTED_EXTENSIONS) == set(_SUPPORTED_EXTENSIONS)
    assert set(cli.SUPPORTED_EXTENSIONS) >= {".md", ".markdown"}


# ---------------------------------------------------------------------------
# run_ingest / aggregate / render_summary
# ---------------------------------------------------------------------------


def test_run_ingest_relays_collection_force_and_progress(tmp_path):
    pdfs = [tmp_path / "one.pdf", tmp_path / "two.pdf"]
    for pdf in pdfs:
        pdf.write_bytes(b"x")
    pipeline = FakePipeline()

    summary = cli.run_ingest(
        pipeline, pdfs, "docs", force=True, verbose=True, out=lambda _s: None
    )

    assert [c["collection"] for c in pipeline.calls] == ["docs", "docs"]
    assert all(c["force"] is True for c in pipeline.calls)
    # on_progress 被真实回调过（verbose 只影响打印，不影响回调本身）
    assert len(pipeline.progress_events) == 2
    assert summary.ingested == 2 and summary.failed == 0


def test_run_ingest_isolates_per_file_failure(tmp_path):
    good = tmp_path / "good.pdf"
    good.write_bytes(b"g")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"b")
    pipeline = FakePipeline(fail_substrings=("bad",))

    summary = cli.run_ingest(pipeline, [good, bad], "docs")

    assert summary.total == 2
    assert summary.ingested == 1
    assert summary.failed == 1
    failed = summary.outcomes[1]
    assert failed.status == "failed"
    assert "fake explosion" in failed.error
    # 失败文件不计产物
    assert summary.chunks == 3 and summary.written == 3


def test_run_ingest_respects_skip_status(tmp_path):
    skip = tmp_path / "skipme.pdf"
    skip.write_bytes(b"s")
    pipeline = FakePipeline()

    summary = cli.run_ingest(pipeline, [skip], "docs")

    assert summary.skipped == 1 and summary.ingested == 0
    assert summary.chunks == 0 and summary.written == 0


def test_run_ingest_verbose_prints_progress_lines(tmp_path):
    pdf = tmp_path / "one.pdf"
    pdf.write_bytes(b"x")
    pipeline = FakePipeline()
    lines: list[str] = []

    cli.run_ingest(pipeline, [pdf], "docs", verbose=True, out=lines.append)

    assert any("load 1/1" in line for line in lines)


def test_run_ingest_quiet_by_default(tmp_path):
    pdf = tmp_path / "one.pdf"
    pdf.write_bytes(b"x")
    pipeline = FakePipeline()
    lines: list[str] = []

    cli.run_ingest(pipeline, [pdf], "docs", out=lines.append)

    assert lines == [
        f"[1/1] success {pdf}  chunks=3 images=1 written=3 trace=trace-0001 (1.5ms)"
    ]


def test_aggregate_mixed_math():
    outcomes = [
        cli.FileOutcome(path="a.pdf", status="success", chunks=4, images=1, written=4),
        cli.FileOutcome(path="b.pdf", status="success", chunks=2, written=2),
        cli.FileOutcome(path="c.pdf", status="skipped"),
        cli.FileOutcome(path="d.pdf", status="failed", error="boom"),
    ]

    summary = cli.aggregate("mixed", outcomes)

    assert summary.collection == "mixed"
    assert summary.total == 4
    assert summary.ingested == 2
    assert summary.skipped == 1
    assert summary.failed == 1
    assert summary.chunks == 6
    assert summary.images == 1
    assert summary.written == 6
    assert summary.to_dict()["outcomes"][3]["error"] == "boom"


def test_render_summary_counts_and_failed_detail():
    summary = cli.aggregate(
        "docs",
        [
            cli.FileOutcome(path="a.pdf", status="success", chunks=4, images=1, written=4),
            cli.FileOutcome(path="c.pdf", status="skipped"),
            cli.FileOutcome(path="bad.pdf", status="failed", error="ValueError: nope"),
        ],
    )

    text = cli.render_summary(summary)

    assert "collection='docs'" in text
    assert "3 total | 1 ingested | 1 skipped | 1 failed" in text
    assert "chunks : 4 | images: 1 | vectors written: 4" in text
    assert "bad.pdf: ValueError: nope" in text


def test_render_summary_no_failed_section_when_all_ok():
    summary = cli.aggregate("docs", [cli.FileOutcome(path="a.pdf", status="success", chunks=1, written=1)])
    assert "failed files" not in cli.render_summary(summary)


# ---------------------------------------------------------------------------
# main 退出码与输出
# ---------------------------------------------------------------------------


def _capture() -> tuple[list[str], object]:
    lines: list[str] = []
    return lines, lines.append


def test_main_exit_zero_all_success(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"a")
    pipeline = FakePipeline(fail_substrings=())
    lines, emit = _capture()

    code = cli.main(["--path", str(tmp_path)], pipeline=pipeline, out=emit)

    assert code == cli.EXIT_OK == 0
    assert "1 total | 1 ingested | 0 skipped | 0 failed" in "\n".join(lines)


def test_main_exit_zero_all_skipped(tmp_path):
    pdf = tmp_path / "skipme.pdf"
    pdf.write_bytes(b"s")
    pipeline = FakePipeline()
    lines, emit = _capture()

    code = cli.main(["--path", str(pdf)], pipeline=pipeline, out=emit)

    assert code == 0
    assert "1 skipped" in "\n".join(lines)
    assert "(unchanged; use --force to re-ingest)" in "\n".join(lines)


def test_main_force_reingests_skipped_file(tmp_path):
    pdf = tmp_path / "skipme.pdf"
    pdf.write_bytes(b"s")
    pipeline = FakePipeline()
    lines, emit = _capture()

    code = cli.main(["--path", str(pdf), "--force"], pipeline=pipeline, out=emit)

    assert code == 0
    assert pipeline.calls[0]["force"] is True
    assert "1 ingested" in "\n".join(lines)


def test_main_exit_one_when_any_file_fails(tmp_path):
    (tmp_path / "good.pdf").write_bytes(b"g")
    (tmp_path / "bad.pdf").write_bytes(b"b")
    pipeline = FakePipeline(fail_substrings=("bad",))
    lines, emit = _capture()

    code = cli.main(["--path", str(tmp_path), "--collection", "x"], pipeline=pipeline, out=emit)

    assert code == cli.EXIT_FAILURE == 1
    joined = "\n".join(lines)
    assert "1 failed" in joined
    assert "bad.pdf" in joined
    assert pipeline.calls[0]["collection"] == "x"


def test_main_exit_two_missing_path(tmp_path):
    lines, emit = _capture()
    code = cli.main(["--path", str(tmp_path / "nope.pdf")], pipeline=FakePipeline(), out=emit)
    assert code == cli.EXIT_USAGE == 2
    assert "does not exist" in "\n".join(lines)


def test_main_exit_two_unsupported_single_file(tmp_path):
    txt = tmp_path / "note.txt"
    txt.write_text("x")
    lines, emit = _capture()
    code = cli.main(["--path", str(txt)], pipeline=FakePipeline(), out=emit)
    assert code == 2
    assert "unsupported file extension" in "\n".join(lines)


def test_main_exit_two_on_config_error(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"a")
    lines, emit = _capture()
    code = cli.main(
        ["--path", str(pdf), "--config", str(tmp_path / "missing_settings.yaml")],
        out=emit,
    )
    assert code == 2
    assert "配置加载失败" in "\n".join(lines)


def test_main_empty_directory_exit_zero_with_info(tmp_path):
    empty = tmp_path / "empty_dir"
    empty.mkdir()
    lines, emit = _capture()

    code = cli.main(["--path", str(empty)], pipeline=FakePipeline(), out=emit)

    assert code == 0
    assert "no supported files found" in "\n".join(lines)
