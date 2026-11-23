"""PI1 md_render 单测（fake runner 注入，不调用真实 pandoc / Chromium）。

覆盖规格 §4：命令行构造（列表传参、不经 shell、独立临时 user-data-dir）、
浏览器探测顺序、PDF Outline 注入、三类错误分型、产物原子改名；模块导入
不得触发 pageindex / litellm（断言 sys.modules）。
"""

import subprocess
import sys
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter

PAGEINDEX_DIR = Path(__file__).resolve().parents[2] / "scripts" / "pageindex"
sys.path.insert(0, str(PAGEINDEX_DIR))

import md_render  # noqa: E402
from md_render import (RenderFailed, RenderToolMissing, RenderTimeout,  # noqa: E402
                       _norm_page_text, map_headings_to_pages, render,
                       resolve_chromium, write_bookmarks)


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
        self.staged_html: str | None = None  # chromium 实际打开的暂存 HTML

    def __call__(self, cmd, timeout_s):
        self.cmds.append(list(cmd))
        if any(a.startswith("--print-to-pdf=") for a in cmd):
            url = next(a for a in cmd if a.startswith("file://"))
            html_path = Path(urllib.parse.unquote(url[len("file://"):]))
            self.staged_html = html_path.read_text(encoding="utf-8")
            out = next(a[len("--print-to-pdf="):] for a in cmd
                       if a.startswith("--print-to-pdf="))
            Path(out).write_bytes(self.pdf_bytes)
            return SimpleNamespace(returncode=self.chrome_rc, stdout="",
                                   stderr=self.chrome_stderr)
        # pandoc 步骤（自定义 pandoc_bin 也路由到这里）
        return SimpleNamespace(returncode=self.pandoc_rc,
                               stdout=self.pandoc_stdout, stderr="")


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
    # pandoc：列表传参、GFM − raw_html（10.9 #1）、standalone + 资源内嵌、
    # 资源路径 = MD 所在目录（相对图片可解析，10.9 #24）、不经 shell
    assert pandoc_cmd == ["pandoc", "-f", "gfm-raw_html", "-t", "html5",
                          "--wrap=none", "--standalone", "--embed-resources",
                          f"--resource-path={md_file.parent}", str(md_file)]
    # chromium：独立临时 user-data-dir（stage 目录内）、headless、print-to-pdf
    assert chrome_cmd[0] == "/fake/chrome"
    assert "--headless=new" in chrome_cmd and "--disable-gpu" in chrome_cmd
    udd = next(a for a in chrome_cmd if a.startswith("--user-data-dir="))
    assert "pi_render_" in udd  # 独立临时目录，非共享默认 profile
    assert any(a.startswith("--print-to-pdf=") for a in chrome_cmd)
    assert any(a.startswith("file://") for a in chrome_cmd)
    # 暂存 HTML 含严格 CSP（10.9 #1）：禁脚本 / 外链 / frame
    assert runner.staged_html is not None
    assert "Content-Security-Policy" in runner.staged_html
    assert "default-src 'none'" in runner.staged_html
    assert "<style>" in runner.staged_html  # 打印 CSS 已注入
    assert result.pages == 2
    assert result.bookmarks == 1
    assert result.map_misses == 0
    assert result.elapsed_ms >= 0


def test_csp_injected_into_pandoc_head(md_file, tmp_path, base_pdf_bytes):
    # pandoc standalone 输出带 <head>：CSP 与打印 CSS 注入 head 内
    pandoc_out = ('<!doctype html><html><head><meta charset="utf-8">'
                  "<title>t</title></head><body><h1>标题一</h1></body></html>")
    runner = FakeRunner(base_pdf_bytes, pandoc_stdout=pandoc_out)
    render(md_file, tmp_path / "out.pdf", runner=runner,
           page_texts_fn=lambda p: ["标题一内容"],
           chrome_bin="/fake/chrome", timeout_s=60)
    html = runner.staged_html
    head_end = html.index("</head>")
    assert "Content-Security-Policy" in html[:head_end]
    assert html.index("<style>") < head_end  # CSS 也在 head 内


def test_pandoc_bin_from_env(monkeypatch, md_file, tmp_path, base_pdf_bytes):
    # 10.9 #13④：PAGEINDEX_PANDOC 生效（env.example / 3.3 既有约定）
    monkeypatch.setenv("PAGEINDEX_PANDOC", "/custom/pandoc")
    runner = FakeRunner(base_pdf_bytes)
    render(md_file, tmp_path / "out.pdf", runner=runner,
           page_texts_fn=lambda p: ["标题一内容"],
           chrome_bin="/fake/chrome", timeout_s=60)
    assert runner.cmds[0][0] == "/custom/pandoc"


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
    monkeypatch.setenv("PATH", str(tmp_path))  # 不依赖宿主 PATH（10.9 #2）
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


def test_browser_detection_precedence(monkeypatch, tmp_path):
    # 10.9 #13⑥：探测顺序 PAGEINDEX_CHROMIUM > 系统 Chrome > PATH > playwright
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    exe = fakebin / "chromium"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    sys_chrome = tmp_path / "syschrome"
    sys_chrome.write_text("#!/bin/sh\n")
    sys_chrome.chmod(0o755)

    monkeypatch.setenv("PAGEINDEX_CHROMIUM", "")
    monkeypatch.setattr(md_render, "_playwright_chromium", lambda: "/pw/chrome")
    monkeypatch.setenv("PATH", str(fakebin))
    # 系统 Chrome 候选优先于 PATH
    monkeypatch.setattr(md_render, "_SYSTEM_CHROME_CANDIDATES", (str(sys_chrome),))
    assert resolve_chromium() == str(sys_chrome)
    # 无系统候选：PATH 优先于 playwright
    monkeypatch.setattr(md_render, "_SYSTEM_CHROME_CANDIDATES", ())
    assert resolve_chromium() == str(exe)
    # 无系统候选、PATH 无可执行：playwright 兜底
    monkeypatch.setenv("PATH", str(tmp_path / "nonexistent"))
    assert resolve_chromium() == "/pw/chrome"


def test_map_headings_uses_same_normalization_as_keys():
    # 10.9 #3：页文本与标题键同一归一化——下划线代码标题与部件形字符不丢书签
    # （render 传入的页文本已经 _norm_page_text 归一化，测试保持同口径）
    heads = [{"level": 2, "title": "`max_nodes` 参数"},
             {"level": 1, "title": "如何见门"}]
    pages = [_norm_page_text("intro max_nodes 参数 body"),
             _norm_page_text("如何⻅⻔章节")]
    mapping, misses = map_headings_to_pages(heads, pages)
    assert mapping == [0, 1]
    assert misses == 0


def test_map_split_title_not_attracted_to_later_repeat():
    # 10.9 #4：跨页截断的「小结」记起始页，不落到后文同名标题
    heads = [{"level": 1, "title": "第一章"}, {"level": 2, "title": "小结"},
             {"level": 1, "title": "第二章"}, {"level": 2, "title": "小结"}]
    pages = ["第一章正文小", "结内容", "第二章", "小结末尾"]
    mapping, misses = map_headings_to_pages(heads, pages)
    assert mapping == [0, 0, 2, 3]
    assert misses == 0


def _flatten_outline(items, acc, parent=None):
    """pypdf outline → [(title, parent_title | None)]；跟在条目后的 list 是其子层。"""
    last = parent  # 紧随其后的 list 属于哪个条目
    for it in items:
        if isinstance(it, list):
            _flatten_outline(it, acc, last)
        else:
            acc.append((it.title, parent))
            last = it.title
    return acc


def test_bookmark_parents_on_miss_and_level_skip(tmp_path):
    # 10.9 #5：miss 的标题不入栈（其子标题不挂到前一个兄弟，落顶层兜底）；
    # 层级跳跃的子标题挂到最近的更浅层级而非顶层
    src = tmp_path / "s.pdf"
    make_blank_pdf(src, 4)
    heads = [{"title": "Z", "level": 1}, {"title": "A", "level": 1},
             {"title": "B", "level": 2}, {"title": "C", "level": 1},
             {"title": "D", "level": 3}]
    mapping = [0, None, 1, 2, 3]  # A miss
    out = tmp_path / "o.pdf"
    n = write_bookmarks(src, out, heads, mapping)
    assert n == 4
    pairs = _flatten_outline(PdfReader(str(out)).outline, [])
    by_title = {t: p for t, p in pairs}
    assert by_title == {"Z": None, "B": None, "C": None, "D": "C"}


def test_render_md_with_bom_keeps_first_heading(tmp_path, base_pdf_bytes):
    # 10.9 #21：BOM 文件的首个标题参与 Outline
    md = tmp_path / "bom.md"
    md.write_bytes("# 标题一\n\n正文\n## 副题\n".encode("utf-8-sig"))
    runner = FakeRunner(base_pdf_bytes,
                        pandoc_stdout="<h1>标题一</h1><h2>副题</h2>")
    result = render(md, tmp_path / "out.pdf", runner=runner,
                    page_texts_fn=lambda p: ["标题一内容副题", "x"],
                    chrome_bin="/fake/chrome", timeout_s=60)
    assert result.bookmarks == 2


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
