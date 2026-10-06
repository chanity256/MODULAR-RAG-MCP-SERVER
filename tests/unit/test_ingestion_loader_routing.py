"""IR5.2：扩展名双登记 + pipeline Loader 路由 + CLI 扫描（全离线）。

markitdown 以 fake 模块经 ``monkeypatch.setitem(sys.modules, ...)`` 注入，
不依赖真实安装；依赖缺失场景用 ``sys.modules["markitdown"] = None`` 模拟。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from ingestion import IngestionPipeline, IngestionPipelineError
from ingestion.pipeline import _SUPPORTED_EXTENSIONS
from ingestion.storage import BM25Indexer, ImageStorage, VectorUpserter
from libs.loader.file_integrity import SQLiteIntegrityChecker
from libs.loader.markitdown_loader import (
    INSTALL_HINT,
    SUPPORTED_EXTENSIONS as MARKITDOWN_EXTENSIONS,
    MarkitdownLoader,
)
from libs.loader.pdf_loader import PdfLoader
from tests.unit.test_ingestion_pipeline import (
    _PARAGRAPHS,
    FakeLoader,
    FakeVectorStore,
    _build_pipeline,
)

pytestmark = pytest.mark.unit

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest.py"
_CLI_MODULE_NAME = "scripts_ingest"
_NEW_EXTENSIONS = (".docx", ".pptx", ".xlsx", ".html")
_TEXT = "\n\n".join(_PARAGRAPHS)


def _load_cli():
    if _CLI_MODULE_NAME in sys.modules:
        return sys.modules[_CLI_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_CLI_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_CLI_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


class _FakeResult:
    def __init__(self, text_content: str) -> None:
        self.text_content = text_content


def _install_fake_markitdown(monkeypatch, text: str = _TEXT) -> list[str]:
    calls: list[str] = []

    class FakeMarkItDown:
        def convert(self, source):
            calls.append(source)
            return _FakeResult(text)

    module = types.ModuleType("markitdown")
    module.MarkItDown = FakeMarkItDown
    monkeypatch.setitem(sys.modules, "markitdown", module)
    return calls


def _build_routing_pipeline(tmp_path: Path, store: FakeVectorStore):
    """不注入 loader 的 pipeline（走扩展名路由），其余持久化落在 tmp_path。"""
    pipeline, parts = _build_pipeline(tmp_path, FakeLoader(tmp_path), store)
    pipeline._injected["loader"] = None
    return pipeline, parts


def _history_rows(integrity) -> list[dict]:
    return list(integrity.list_processed())


# ---------------------------------------------------------------------------
# 双登记
# ---------------------------------------------------------------------------


def test_dual_registration_includes_markitdown_extensions_and_excludes_markdown():
    cli = _load_cli()
    expected = {".pdf", *_NEW_EXTENSIONS}
    assert set(_SUPPORTED_EXTENSIONS) == expected
    assert set(cli.SUPPORTED_EXTENSIONS) == expected
    assert set(MARKITDOWN_EXTENSIONS) <= set(_SUPPORTED_EXTENSIONS)
    for ext in (".md", ".markdown"):
        assert ext not in _SUPPORTED_EXTENSIONS
        assert ext not in cli.SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# pipeline 路由
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ext", _NEW_EXTENSIONS)
def test_markitdown_extension_routes_to_markitdown_loader(tmp_path, monkeypatch, ext):
    calls = _install_fake_markitdown(monkeypatch)
    store = FakeVectorStore()
    pipeline, _ = _build_routing_pipeline(tmp_path, store)
    source = tmp_path / f"report{ext}"
    source.write_bytes(f"payload for {ext}".encode())

    result = pipeline.run(str(source), collection="docs")

    assert result.status == "success"
    assert result.chunks > 0
    assert calls == [str(source)]
    assert isinstance(pipeline._resolve_loader(str(source)), MarkitdownLoader)
    records = list(store.records.values())
    assert records
    for rec in records:
        assert rec.metadata["source_path"] == str(source)
        assert rec.metadata["collection"] == "docs"
        assert rec.metadata["doc_type"] == ext.lstrip(".")


def test_uppercase_extension_is_routed(tmp_path, monkeypatch):
    _install_fake_markitdown(monkeypatch)
    pipeline, _ = _build_routing_pipeline(tmp_path, FakeVectorStore())
    source = tmp_path / "SLIDES.PPTX"
    source.write_bytes(b"pptx payload")

    assert pipeline.run(str(source)).status == "success"


def test_routing_is_per_extension_within_one_pipeline(tmp_path):
    pipeline, _ = _build_routing_pipeline(tmp_path, FakeVectorStore())
    pipeline._ensure_components()

    pdf_loader = pipeline._resolve_loader("a.pdf")
    docx_loader = pipeline._resolve_loader("b.docx")
    html_loader = pipeline._resolve_loader("c.html")

    assert isinstance(pdf_loader, PdfLoader)
    assert isinstance(docx_loader, MarkitdownLoader)
    assert isinstance(html_loader, MarkitdownLoader)
    assert pipeline._resolve_loader("d.pdf") is pdf_loader
    assert pipeline._resolve_loader("e.docx") is docx_loader


def test_injected_loader_overrides_extension_routing(tmp_path, monkeypatch):
    calls = _install_fake_markitdown(monkeypatch)
    loader = FakeLoader(tmp_path, with_image=False)
    pipeline, _ = _build_pipeline(tmp_path, loader, FakeVectorStore())
    source = tmp_path / "notes.docx"
    source.write_bytes(b"docx payload")

    assert pipeline.run(str(source)).status == "success"
    assert loader.load_calls == [str(source)]
    assert calls == []


def test_parser_version_reflects_routed_loader(tmp_path, monkeypatch):
    _install_fake_markitdown(monkeypatch)
    pipeline, _ = _build_routing_pipeline(tmp_path, FakeVectorStore())
    pipeline._ensure_components()

    pipeline._resolve_loader("x.docx")
    assert type(pipeline._loader) is MarkitdownLoader
    pipeline._resolve_loader("y.pdf")
    assert type(pipeline._loader) is PdfLoader


def test_missing_markitdown_fails_at_load_stage_with_install_hint(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "markitdown", None)
    pipeline, parts = _build_routing_pipeline(tmp_path, FakeVectorStore())
    source = tmp_path / "sheet.xlsx"
    source.write_bytes(b"xlsx payload")

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source))

    assert exc_info.value.stage == "load"
    assert INSTALL_HINT in str(exc_info.value)
    failed = [r for r in _history_rows(parts["integrity"]) if r.get("status") == "failed"]
    assert failed
    assert failed[0].get("failed_stage") == "load"


def test_markitdown_chunks_are_isomorphic_with_pdf_chunks(tmp_path, monkeypatch):
    """同一文本经 markitdown 路由与 PDF（fake loader）路径产出的 chunk 同构。"""
    _install_fake_markitdown(monkeypatch)

    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    pdf_store = FakeVectorStore()
    pdf_pipeline, _ = _build_pipeline(pdf_dir, FakeLoader(pdf_dir, with_image=False), pdf_store)
    pdf_source = pdf_dir / "same.pdf"
    pdf_source.write_bytes(b"identical-bytes")
    pdf_result = pdf_pipeline.run(str(pdf_source), collection="c")

    md_dir = tmp_path / "docx"
    md_dir.mkdir()
    md_store = FakeVectorStore()
    md_pipeline, _ = _build_routing_pipeline(md_dir, md_store)
    md_source = md_dir / "same.docx"
    md_source.write_bytes(b"identical-bytes")
    md_result = md_pipeline.run(str(md_source), collection="c")

    assert md_result.chunks == pdf_result.chunks
    pdf_records = sorted(pdf_store.records.values(), key=lambda r: r.metadata.get("chunk_index", 0))
    md_records = sorted(md_store.records.values(), key=lambda r: r.metadata.get("chunk_index", 0))
    assert [r.text for r in md_records] == [r.text for r in pdf_records]
    for pdf_rec, md_rec in zip(pdf_records, md_records):
        assert set(md_rec.metadata) == set(pdf_rec.metadata)
        assert md_rec.metadata["sha256"] == pdf_rec.metadata["sha256"]
        assert len(md_rec.vector) == len(pdf_rec.vector)


# ---------------------------------------------------------------------------
# CLI 扫描
# ---------------------------------------------------------------------------


def test_cli_collect_files_picks_new_extensions(tmp_path):
    cli = _load_cli()
    sub = tmp_path / "sub"
    sub.mkdir()
    for name in ("a.pdf", "b.docx", "c.PPTX", "notes.md", "readme.txt"):
        (tmp_path / name).write_bytes(b"x")
    for name in ("d.xlsx", "e.html", "f.markdown"):
        (sub / name).write_bytes(b"x")

    files = cli.collect_files(tmp_path)

    assert files == [
        tmp_path / "a.pdf",
        tmp_path / "b.docx",
        tmp_path / "c.PPTX",
        sub / "d.xlsx",
        sub / "e.html",
    ]


@pytest.mark.parametrize("ext", _NEW_EXTENSIONS)
def test_cli_accepts_single_new_extension_file(tmp_path, ext):
    cli = _load_cli()
    target = tmp_path / f"doc{ext}"
    target.write_bytes(b"x")

    assert cli.collect_files(target) == [target]


def test_cli_rejects_single_markdown_file(tmp_path):
    cli = _load_cli()
    target = tmp_path / "note.md"
    target.write_text("# hi", encoding="utf-8")

    with pytest.raises(cli.IngestPathError):
        cli.collect_files(target)
