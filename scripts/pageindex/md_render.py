"""MD → 派生 PDF 渲染（D11 终裁 v3 方案，PI1）。

pandoc（GFM → HTML5）→ headless Chromium ``--print-to-pdf`` → 原 MD 标题树
经页映射后由 pypdf 写入 PDF Embedded Bookmarks（Outline）。子进程执行由
注入的 ``runner`` 完成（生产为带超时的 ``subprocess.run``，单测注入 fake，
不调用真实 pandoc / Chromium）；页文本提取默认用 pypdfium2（懒加载），
可经 ``page_texts_fn`` 注入。产物先写临时文件再原子改名为目标路径
（``<storage>/derived/<md_sha256>.pdf``，由调用方命名）。不 import
pageindex / litellm。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:  # 同目录导入（测试经 sys.path.insert 导入本包目录）
    from heading_index import build_heading_index, normalize_title
except ImportError:  # pragma: no cover - 包化导入回退
    from .heading_index import build_heading_index, normalize_title  # type: ignore

PRINT_CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
       font-size: 10.5pt; line-height: 1.55; color: #111; }
h1, h2, h3, h4, h5, h6 { break-after: avoid; line-height: 1.3; }
h1 { font-size: 1.9em; } h2 { font-size: 1.55em; } h3 { font-size: 1.3em; }
h4 { font-size: 1.15em; } h5 { font-size: 1.05em; } h6 { font-size: 1.0em; }
pre, code { white-space: pre-wrap; word-break: break-word; font-size: 0.88em; }
pre { background: #f6f8fa; padding: 8px; border-radius: 4px; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #999; padding: 3px 6px; font-size: 0.95em; }
img { max-width: 100%; }
blockquote { color: #555; border-left: 3px solid #ccc; margin-left: 0; padding-left: 10px; }
"""

# 浏览器探测顺序（D11）：PAGEINDEX_CHROMIUM > 系统 Chrome/Chromium > 可选
# sidecar venv playwright 托管浏览器（避免强制安装 ~300 MB 浏览器）。
_SYSTEM_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)


class RenderError(Exception):
    """渲染失败基类（sidecar 据此对 /index 返回 ``render:`` 前缀错误，R9）。"""


class RenderToolMissing(RenderError):
    """pandoc / Chromium 缺失。"""


class RenderTimeout(RenderError):
    """子进程超时。"""


class RenderFailed(RenderError):
    """非零退出或产物缺失。"""


@dataclass
class RenderResult:
    pages: int
    elapsed_ms: float
    bookmarks: int
    map_misses: int


def _norm_page_text(s: str) -> str:
    # 与标题键（normalize_title）同一归一化：部件形映射 + 强调符剔除 +
    # 去空白，保证 map_headings_to_pages 的子串匹配两侧一致（10.9 #3）
    return normalize_title(s)


def resolve_chromium(env: dict | None = None) -> str | None:
    """探测 Chromium 可执行文件，找不到返回 ``None``。"""
    env = os.environ if env is None else env
    explicit = env.get("PAGEINDEX_CHROMIUM", "").strip()
    if explicit:
        return explicit
    for cand in _SYSTEM_CHROME_CANDIDATES:
        if Path(cand).exists():
            return cand
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return found
    return _playwright_chromium()


def _playwright_chromium() -> str | None:
    """可选回退：sidecar venv playwright 托管浏览器（缺失即静默跳过）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
            return path if path and Path(path).exists() else None
    except Exception:
        return None


def chromium_command(out_pdf: Path, html_path: Path, user_data_dir: Path,
                     chrome_bin: str) -> list[str]:
    """Chromium 命令行：按列表传参不经 shell；强制独立临时 profile 与
    ``--headless=new``（防与桌面 Chrome 抢 profile 锁，R9）。"""
    return [chrome_bin, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--hide-scrollbars",
            f"--user-data-dir={user_data_dir}",
            f"--print-to-pdf={out_pdf}", "--no-pdf-header-footer",
            html_path.resolve().as_uri()]  # 百分号编码（空格 / # / Windows，10.9 #25）


def _default_runner(cmd: list[str], timeout_s: float):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)


def _default_page_texts(pdf_path: Path) -> list[str]:
    """pypdfium2 全文提取（懒加载，零 LLM；可注入替换）。"""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # sidecar venv 未装（10.9 #11）
        raise RenderToolMissing(
            f"pypdfium2 not installed for page-text extraction: {pdf_path}") from exc

    doc = pdfium.PdfDocument(str(pdf_path))
    texts: list[str] = []
    try:
        for page in doc:
            tp = page.get_textpage()
            texts.append(_norm_page_text(tp.get_text_range()))
            tp.close()
            page.close()
    finally:
        doc.close()
    return texts


def _run_step(runner: Callable, cmd: list[str], timeout_s: float, what: str):
    """执行一步子进程并做三类错误分型；返回 ``runner`` 的结果供调用方取输出。"""
    try:
        proc = runner(cmd, timeout_s)
    except FileNotFoundError as exc:
        raise RenderToolMissing(f"{what} not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderTimeout(f"{what} timed out after {timeout_s:g}s") from exc
    if proc.returncode != 0:
        raise RenderFailed(f"{what} failed (rc={proc.returncode}): "
                           f"{(proc.stderr or '')[-2000:]}")
    return proc


def map_headings_to_pages(headings: list[dict], page_texts: list[str]
                          ) -> tuple[list[int | None], int]:
    """标题 → 物理页（0 起计）单调顺序匹配：标题 i 必须落在指针页之后；
    同页允许同级标题连排（指针不回退）。逐页先查全页精确匹配，未命中再查
    该页与下页的拼接窗口（标题文本被分页截断的情形）：窗口命中且键不在
    下页全页出现时记起始页 ``p``——若键完整落在下页，留给下一轮精确匹配，
    避免截断标题被吸到后文同名标题（10.9 #4）。返回 (mapping, misses)。"""
    joined = [page_texts[i] + (page_texts[i + 1] if i + 1 < len(page_texts) else "")
              for i in range(len(page_texts))]
    mapping: list[int | None] = []
    misses = 0
    ptr = 0
    for h in headings:
        key = normalize_title(h["title"])
        found = None
        if key:
            for p in range(ptr, len(page_texts)):
                if key in page_texts[p]:
                    found = p
                    break
                if p + 1 < len(page_texts) and key in joined[p] \
                        and key not in page_texts[p + 1]:
                    found = p  # 跨页截断标题：记其起始页
                    break
        if found is None:
            misses += 1
            mapping.append(None)
        else:
            mapping.append(found)
            ptr = found
    return mapping, misses


def write_bookmarks(src_pdf: Path, out_pdf: Path, headings: list[dict],
                    mapping: list[int | None]) -> int:
    """按标题层级写入 PDF Bookmarks（Outline）；页映射 miss 的标题跳过。
    返回写入条数。``out_pdf`` 与 ``src_pdf`` 可同路径（先写临时再替换）。"""
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # sidecar venv 未装（10.9 #11）
        raise RenderToolMissing("pypdf not installed for outline injection") from exc

    reader = PdfReader(str(src_pdf))
    writer = PdfWriter()
    writer.append(reader)
    parent_stack: dict[int, object] = {}
    n_written = 0
    for h, page_no in zip(headings, mapping):
        level = h["level"]
        if page_no is None:
            # miss 的标题不写入，但要清除栈中该层级及更深的条目，防止其
            # 子标题挂到前一个同级兄弟上（10.9 #5）
            for lv in [stale for stale in parent_stack if stale >= level]:
                del parent_stack[lv]
            continue
        # 父节点取栈中最近的更浅层级（层级跳跃时子标题不升为顶层）
        parent = None
        for lv in range(level - 1, 0, -1):
            if lv in parent_stack:
                parent = parent_stack[lv]
                break
        item = writer.add_outline_item(title=h["title"], page_number=page_no,
                                       parent=parent)
        parent_stack[level] = item
        for lv in [stale for stale in parent_stack if stale > level]:
            del parent_stack[lv]
        n_written += 1
    tmp = out_pdf.parent / (out_pdf.name + ".tmp")
    with open(tmp, "wb") as fh:
        writer.write(fh)
    os.replace(tmp, out_pdf)
    return n_written


def render(md_path: str | os.PathLike[str], out_pdf: str | os.PathLike[str], *,
           runner: Callable | None = None,
           page_texts_fn: Callable[[Path], list[str]] | None = None,
           pandoc_bin: str | None = None,
           chrome_bin: str | None = None,
           timeout_s: float | None = None) -> RenderResult:
    """MD → 派生 PDF（v3：注入原 MD 标题树 Bookmarks）。

    ``runner(cmd, timeout_s) -> CompletedProcess``；``page_texts_fn(pdf_path)
    -> [页归一化文本]``。Chrome 无法直接读 macOS TCC 保护目录（~/Desktop）：
    HTML 与 PDF 均经临时目录中转，产物原子落到 ``out_pdf``。不可信 MD 的
    防护（10.9 #1）：pandoc 关闭 ``raw_html`` 扩展（HTML 块 / 内联脚本不入
    文档），暂存 HTML 注入严格 CSP，Chromium 仅打开该暂存页。
    """
    md_path = Path(md_path)
    out_pdf = Path(out_pdf)
    runner = runner or _default_runner
    page_texts_fn = page_texts_fn or _default_page_texts
    pandoc_bin = pandoc_bin or os.environ.get("PAGEINDEX_PANDOC") or "pandoc"
    timeout_s = timeout_s or float(os.environ.get("PAGEINDEX_RENDER_TIMEOUT_S", "600"))
    started = time.perf_counter()
    try:  # Outline 注入依赖 pypdf：进入转换前先 fail-fast（10.9 #11）
        import pypdf  # noqa: F401
    except ImportError as exc:
        raise RenderToolMissing("pypdf not installed for outline injection") from exc

    # 1) pandoc：GFM − raw_html → HTML5（列表传参，不经 shell；standalone +
    #    embed-resources 让相对路径图片内嵌为 data URI，10.9 #24）
    html_proc = _run_step(runner, [
        pandoc_bin, "-f", "gfm-raw_html", "-t", "html5", "--wrap=none",
        "--standalone", "--embed-resources", f"--resource-path={md_path.parent}",
        str(md_path)], timeout_s, "pandoc")

    with tempfile.TemporaryDirectory(prefix="pi_render_") as stage:
        stage_path = Path(stage)
        staged_html = stage_path / "doc.html"
        body = html_proc.stdout
        # CSP：禁脚本 / 外链 / frame；样式只允许内联（打印 CSS），图片仅 data:
        csp_meta = ('<meta http-equiv="Content-Security-Policy" '
                    'content="default-src \'none\'; style-src \'unsafe-inline\'; '
                    'img-src data:">')
        inject = csp_meta + "<style>" + PRINT_CSS + "</style>"
        head = re.search(r"<head\b[^>]*>", body, re.IGNORECASE)
        if head:
            html = body[:head.end()] + inject + body[head.end():]
        else:  # 非 standalone 输出的兜底
            html = ("<!doctype html><html><head>" + inject +
                    "</head><body>" + body + "</body></html>")
        staged_html.write_text(html, encoding="utf-8")

        # 2) Chromium 渲染（独立临时 user-data-dir）
        chrome = chrome_bin or resolve_chromium()
        if not chrome:
            raise RenderToolMissing(
                "no Chromium found: set PAGEINDEX_CHROMIUM or install system Chrome")
        staged_pdf = stage_path / "out.pdf"
        _run_step(runner,
                  chromium_command(staged_pdf, staged_html, stage_path / "profile",
                                   chrome),
                  timeout_s, "chromium")
        if not staged_pdf.exists():
            raise RenderFailed("chromium produced no PDF")

        # 3) 页映射 + Bookmarks 注入（v3 终裁方案，PI0.4a 实测回定位 ~95%）
        page_texts = page_texts_fn(staged_pdf)
        headings = build_heading_index(
            md_path.read_text(encoding="utf-8-sig"))  # utf-8-sig 剥 BOM（10.9 #21）
        mapping, misses = map_headings_to_pages(headings, page_texts)
        buf = stage_path / "with_outline.pdf"
        n_bookmarks = write_bookmarks(staged_pdf, buf, headings, mapping)

        # 4) 原子落位（tmp + replace）
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = out_pdf.parent / (out_pdf.name + ".tmp")
        tmp_target.write_bytes(buf.read_bytes())
        tmp_target.replace(out_pdf)

    return RenderResult(pages=len(page_texts),
                        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                        bookmarks=n_bookmarks, map_misses=misses)


if __name__ == "__main__":  # pragma: no cover - 手动渲染入口（sidecar venv 用）
    print(render(sys.argv[1], sys.argv[2]))
