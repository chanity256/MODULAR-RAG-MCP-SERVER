"""MarkitdownLoader 单测（IR5.1）：全离线，注入 fake markitdown 模块。"""

from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path

import pytest

from core.types import Document
from libs.loader.base_loader import BaseLoader
from libs.loader.markitdown_loader import (
    INSTALL_HINT,
    SUPPORTED_EXTENSIONS,
    MarkitdownLoader,
    MarkitdownLoaderError,
    MarkitdownNotInstalledError,
)

pytestmark = pytest.mark.unit


class _FakeResult:
    def __init__(self, text_content):
        self.text_content = text_content


def _install_fake_markitdown(monkeypatch, *, text="# Title\n\nbody text", error=None):
    calls: list[str] = []

    class FakeMarkItDown:
        def convert(self, source):
            calls.append(source)
            if error is not None:
                raise error
            return _FakeResult(text)

    module = types.ModuleType("markitdown")
    module.MarkItDown = FakeMarkItDown
    monkeypatch.setitem(sys.modules, "markitdown", module)
    return calls


def _write(tmp_path: Path, name: str, data: bytes = b"fake binary payload") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_is_base_loader():
    assert isinstance(MarkitdownLoader(), BaseLoader)


def test_supported_extensions_exclude_markdown():
    assert SUPPORTED_EXTENSIONS == {".docx", ".pptx", ".xlsx", ".html"}
    assert ".md" not in SUPPORTED_EXTENSIONS
    assert ".pdf" not in SUPPORTED_EXTENSIONS


@pytest.mark.parametrize(
    ("name", "doc_type"),
    [("a.docx", "docx"), ("b.pptx", "pptx"), ("c.xlsx", "xlsx"), ("d.html", "html"), ("E.DOCX", "docx")],
)
def test_load_produces_document_with_metadata_contract(tmp_path, monkeypatch, name, doc_type):
    calls = _install_fake_markitdown(monkeypatch, text="hello world")
    payload = f"payload-{name}".encode()
    path = _write(tmp_path, name, payload)

    doc = MarkitdownLoader().load(str(path))

    expected_hash = hashlib.sha256(payload).hexdigest()
    assert isinstance(doc, Document)
    assert doc.id == expected_hash
    assert doc.text == "hello world"
    assert doc.metadata == {
        "source_path": str(path),
        "doc_type": doc_type,
        "sha256": expected_hash,
    }
    assert "images" not in doc.metadata
    assert doc.image_refs == []
    assert calls == [str(path)]


def test_doc_id_depends_on_content_not_path(tmp_path, monkeypatch):
    _install_fake_markitdown(monkeypatch)
    a = _write(tmp_path, "a.docx", b"same bytes")
    b = _write(tmp_path, "b.docx", b"same bytes")
    c = _write(tmp_path, "c.docx", b"other bytes")
    loader = MarkitdownLoader()
    assert loader.load(str(a)).id == loader.load(str(b)).id
    assert loader.load(str(a)).id != loader.load(str(c)).id


@pytest.mark.parametrize("text", ["", "   \n\t ", None])
def test_empty_text_raises(tmp_path, monkeypatch, text):
    _install_fake_markitdown(monkeypatch, text=text)
    path = _write(tmp_path, "empty.pptx")
    with pytest.raises(MarkitdownLoaderError, match="no text"):
        MarkitdownLoader().load(str(path))


def test_convert_failure_wrapped(tmp_path, monkeypatch):
    _install_fake_markitdown(monkeypatch, error=ValueError("corrupt zip"))
    path = _write(tmp_path, "bad.xlsx")
    with pytest.raises(MarkitdownLoaderError, match="corrupt zip") as excinfo:
        MarkitdownLoader().load(str(path))
    assert isinstance(excinfo.value.__cause__, ValueError)


@pytest.mark.parametrize("name", ["note.md", "doc.pdf", "plain.txt", "legacy.doc", "noext"])
def test_unsupported_extension_rejected(tmp_path, monkeypatch, name):
    calls = _install_fake_markitdown(monkeypatch)
    path = _write(tmp_path, name)
    with pytest.raises(MarkitdownLoaderError, match="unsupported extension"):
        MarkitdownLoader().load(str(path))
    assert calls == []


def test_missing_file_raises_file_not_found(tmp_path, monkeypatch):
    _install_fake_markitdown(monkeypatch)
    with pytest.raises(FileNotFoundError):
        MarkitdownLoader().load(str(tmp_path / "missing.docx"))


def test_missing_dependency_raises_with_install_hint(tmp_path, monkeypatch):
    # sys.modules 中置 None 会让 import 抛 ImportError，模拟未安装。
    monkeypatch.setitem(sys.modules, "markitdown", None)
    path = _write(tmp_path, "a.docx")
    with pytest.raises(MarkitdownNotInstalledError) as excinfo:
        MarkitdownLoader().load(str(path))
    err = excinfo.value
    assert isinstance(err, ImportError)
    assert isinstance(err, MarkitdownLoaderError)
    assert "pip install" in str(err)
    assert "ingest-formats" in str(err)
    assert INSTALL_HINT in str(err)


def test_module_import_does_not_require_markitdown():
    import subprocess

    src_dir = Path(__file__).resolve().parents[2] / "src"
    code = (
        "import sys; sys.modules['markitdown'] = None; "
        f"sys.path.insert(0, {str(src_dir)!r}); "
        "import libs.loader.markitdown_loader as m; "
        "assert m.MarkitdownLoader is not None"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
