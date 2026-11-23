"""PI1 md_render 单测（fake runner 注入，不调用真实 pandoc / Chromium）。

覆盖规格 §4：命令行构造（列表传参、不经 shell、独立临时 user-data-dir）、
浏览器探测顺序、PDF Outline 注入、三类错误分型、产物原子改名；模块导入
不得触发 pageindex / litellm（断言 sys.modules）。
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter

PAGEINDEX_DIR = Path(__file__).resolve().parents[2] / "scripts" / "pageindex"
sys.path.insert(0, str(PAGEINDEX_DIR))

import md_render  # noqa: E402
from md_render import (RenderFailed, RenderToolMissing, RenderTimeout,  # noqa: E402
                       map_headings_to_pages, render, resolve_chromium)


def make_blank_pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        writer.write(fh)


class FakeRunner:
    """记录命令并按 --print-to-pdf= 参数产出预置 PDF 字节。"""

    def __init__(self, pdf_bytes: bytes, *, pandoc_rc: int = 0,
                 chrome_rc: int = 0, pandoc_stdout: str = "<h1>标题一</h1>",
                 chrome_stderr: str = "") -> None:
        self.pdf_bytes = pdf_bytes
        self.cmds: list[list[str]] = []
        self.pandoc_rc = pandoc_rc
        self.chrome_rc = chrome_rc
        self.pandoc_stdout = pandoc_stdout
        self.chrome_stderr = chrome_stderr

    def __call__(self, cmd, timeout_s):
        self.cmds.append(list(cmd))
        if cmd[0] == "pandoc":
            return SimpleNamespace(returncode=self.pandoc_rc,
                                   stdout=self.pandoc_stdout, stderr="")
        out = next(a[len("--print-to-pdf="):] for a in cmd
                   if a.startswith("--print-to-pdf="))
        Path(out).write_bytes(self.pdf_bytes)
        return SimpleNamespace(returncode=self.chrome_rc, stdout="",
                               stderr=self.chrome_stderr)


@pytest.fixture
def md_file(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("# 标题一\n\n正文\n", encoding="utf-8")
    return p


@pytest.fixture
def base_pdf_bytes(tmp_path):
    p = tmp_path / "base.pdf"
    make_blank_pdf(p, pages=2)
    return p.read_bytes()


def test_command_construction(md_file, tmp_path, base_pdf_bytes):
    runner = FakeRunner(base_pdf_bytes)

    def page_texts(pdf_path):
        return ["标题一内容", "第二页"]

    out = tmp_path / "derived" / "abc.pdf"
    result = render(md_file, out, runner=runner, page_texts_fn=page_texts,
                    pandoc_bin="pandoc", chrome_bin="/fake/chrome", timeout_s=60)
    pandoc_cmd, chrome_cmd = runner.cmds[0], runner.cmds[1]
    # pandoc：列表传参、GFM、不经 shell
    assert pandoc_cmd == ["pandoc", "-f", "gfm", "-t", "html5", "--wrap=none",
                          str(md_file)]
    # chromium：独立临时 user-data-dir（stage 目录内）、headless、print-to-pdf
    assert chrome_cmd[0] == "/fake/chrome"
    assert "--headless=new" in chrome_cmd and "--disable-gpu" in chrome_cmd
    udd = next(a for a in chrome_cmd if a.startswith("--user-data-dir="))
    assert "pi_render_" in udd  # 独立临时目录，非共享默认 profile
    assert any(a.startswith("--print-to-pdf=") for a in chrome_cmd)
    assert any(a.startswith("file://") for a in chrome_cmd)
    assert result.pages == 2
    assert result.bookmarks == 1
    assert result.map_misses == 0
    assert result.elapsed_ms >= 0


def test_outline_written_and_atomic_rename(md_file, tmp_path, base_pdf_bytes):
    out = tmp_path / "derived" / "abc.pdf"
    render(md_file, out, runner=FakeRunner(base_pdf_bytes),
           page_texts_fn=lambda p: ["标题一内容", "x"],
           chrome_bin="/fake/chrome", timeout_s=60)
    assert out.exists()
    assert not (tmp_path / "derived" / "abc.pdf.tmp").exists()  # 原子改名无残留
    outline = PdfReader(str(out)).outline
    assert len(outline) == 1
    assert outline[0].title == "标题一"


def test_pandoc_failure_is_render_failed(md_file, tmp_path, base_pdf_bytes):
    runner = FakeRunner(base_pdf_bytes, pandoc_rc=2)
    with pytest.raises(RenderFailed, match="pandoc"):
        render(md_file, tmp_path / "out.pdf", runner=runner,
               page_texts_fn=lambda p: [], chrome_bin="/fake/chrome")


def test_chrome_failure_is_render_failed(md_file, tmp_path, base_pdf_bytes):
    runner = FakeRunner(base_pdf_bytes, chrome_rc=1,
                        chrome_stderr="boom")
    with pytest.raises(RenderFailed, match="chromium"):
        render(md_file, tmp_path / "out.pdf", runner=runner,
               page_texts_fn=lambda p: [], chrome_bin="/fake/chrome")


def test_tool_missing_and_timeout_typing(md_file, tmp_path, base_pdf_bytes):
    class MissingRunner(FakeRunner):
        def __call__(self, cmd, timeout_s):
            raise FileNotFoundError("pandoc")

    with pytest.raises(RenderToolMissing):
        render(md_file, tmp_path / "out1.pdf", runner=MissingRunner(b""),
               chrome_bin="/fake/chrome")

    class TimeoutRunner(FakeRunner):
        def __call__(self, cmd, timeout_s):
            self.cmds.append(list(cmd))
            if cmd[0] == "pandoc":
                return SimpleNamespace(returncode=0, stdout="<h1>x</h1>", stderr="")
            raise subprocess.TimeoutExpired(cmd, timeout_s)

    with pytest.raises(RenderTimeout):
        render(md_file, tmp_path / "out2.pdf", runner=TimeoutRunner(base_pdf_bytes),
               chrome_bin="/fake/chrome")


def test_chromium_missing_raises_tool_missing(md_file, tmp_path, base_pdf_bytes,
                                              monkeypatch):
    monkeypatch.setenv("PAGEINDEX_CHROMIUM", "")
    monkeypatch.setattr(md_render, "_SYSTEM_CHROME_CANDIDATES", ())
    monkeypatch.setattr(md_render, "_playwright_chromium", lambda: None)
    runner = FakeRunner(base_pdf_bytes)
    with pytest.raises(RenderToolMissing, match="Chromium"):
        render(md_file, tmp_path / "out.pdf", runner=runner,
               page_texts_fn=lambda p: [], timeout_s=60)
    assert len(runner.cmds) == 1  # pandoc 执行后即失败，未发 chromium 命令


def test_resolve_chromium_env_priority(monkeypatch):
    monkeypatch.setenv("PAGEINDEX_CHROMIUM", "/custom/chrome")
    assert resolve_chromium() == "/custom/chrome"


def test_resolve_chromium_falls_back_to_system_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PAGEINDEX_CHROMIUM", "")
    monkeypatch.setattr(md_render, "_SYSTEM_CHROME_CANDIDATES", ())
    monkeypatch.setattr(md_render, "_playwright_chromium", lambda: None)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    exe = fakebin / "chromium"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(fakebin))
    assert resolve_chromium() == str(exe)


def test_map_headings_monotonic_and_misses():
    heads = [{"level": 1, "title": "Alpha"}, {"level": 2, "title": "Beta"},
             {"level": 1, "title": "Gamma"}]
    mapping, misses = map_headings_to_pages(
        heads, ["Alpha内容", "Alpha内容Beta", "Gamma内容"])
    assert mapping == [0, 1, 2]
    assert misses == 0
    mapping, misses = map_headings_to_pages(
        heads, ["Alpha内容", "Gamma内容", ""])
    assert mapping[0] == 0 and mapping[1] is None and mapping[2] == 1
    assert misses == 1


def test_import_does_not_pull_pageindex_or_litellm():
    # 规格红线预演（PI2 正式规则落地前的单测锁定）：导入本模块不得连带
    # import pageindex / litellm
    import importlib
    importlib.reload(md_render)
    assert "pageindex" not in sys.modules
    assert "litellm" not in sys.modules
