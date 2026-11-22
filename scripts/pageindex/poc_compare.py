#!/usr/bin/env python
"""PI0 四路对照 · 编排与树检索原型（PAGEINDEX_TREE_RETRIEVAL_SPEC 3.6 第 4/5 条）。

在 sidecar venv（``.venv-pageindex``）中运行，直接调用上游 PageIndex 与
litellm（PI0 阶段唯一例外，红线白名单成员）；不依赖 PI1 / PI2 的正式模块。

0.5 起的形态（D3 / D11 / D12）：
- MD 不再走上游 ``md_to_tree``（废弃）；先转派生 PDF（pandoc → HTML →
  headless Chrome ``--print-to-pdf``，原 MD 标题树经 ``pypdf`` 注入
  Bookmarks——PI0.4a 终裁的 v3 方案），再与原生 PDF 同走
  ``PageIndexClient.submit_document``（``--tree-source submit``）或零 LLM
  ``page_index_flash``（``--tree-source flash``，PI0.5 冒烟用）。
- MD 证据按 D12 回映射：树节点标题经 heading_index 原型回定位为原 MD 行
  区间，原文优先从原 MD 读取（读前校验文件哈希）；回定位失败或越界时
  回退派生 PDF 页文本并记 telemetry。

四路对照：
- ``tree``：本脚本内置的树检索原型（选文档 → 逐层选节点 → 读原文 →
  判是否足够）。
- ``hybrid`` / ``agentic_deep`` / ``plan_a``：经子进程调用主 venv 的
  ``poc_main_routes.py``（不 import pageindex，红线不受影响）。

0.5 度量口径（D9 只固定口径，数值门槛 PI0.7 与用户共同拍板）：
- 各路统一按前 k 条证据判 hit（k 与 hybrid ``top_k_final`` 对齐，默认 10）。
- hit 双口径：严格同文件（证据文件 == 题目目标文件）+ 内容等价（跨文件
  证据文本归一化后包含于目标文件全文，如纯子集拆分件）。
- ``locate`` / ``synthesize`` 分列汇总；synthesize 增报多目标章节覆盖率
  （section recall：命中目标小节数 / 目标小节总数）。
- 证据精度：top-k 中落在目标范围内的证据条数占比。
- locatable 按生产口径分路：``plan_a`` 计离线回定位；``hybrid`` /
  ``agentic_deep`` 只计产品自身输出的章节 / 行号 / 页码（现状均无，
  如实为 0）；``tree`` 计树检索返回的 section / line / page 字段。

用法::

    .venv-pageindex/bin/python scripts/pageindex/poc_compare.py --source all
    .venv-pageindex/bin/python scripts/pageindex/poc_compare.py --index-only
    .venv-pageindex/bin/python scripts/pageindex/poc_compare.py --smoke-fake
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "pageindex"))

# tree 路多文档候选集（PI0.5 修订：剔除纯子集 13_面试实战.md——Q21–Q25 已
# 按内容等价映回原大文件；保留独立修订版；增纳 1 篇次长独立 MD 真测多文档路由）
DEFAULT_MD_PATHS = [
    "data/documents/大模型应用学习路线拆解/大模型应用_算法学习路线+八股+面试实战4.md",
    "data/documents/大模型应用学习路线拆解/章节拆分/应用开发项目与开源架构解析.md",
    "data/documents/大模型应用学习路线拆解/章节拆分/agent八股/05_Agent.md",
]
MAX_EVIDENCE_CHARS = 8000
TOP_K = 10               # 与 hybrid top_k_final 对齐（config/settings.yaml retrieval）
PREFILTER_THRESHOLD = 25  # 单层候选节点超过该数先做压缩预筛
PREFILTER_KEEP = 15
PAGEINDEX_STORAGE = REPO_ROOT / "data/db/pageindex"

ATX_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*#*\s*$")
SETEXT_UNDERLINE_RE = re.compile(r"^(=+|-+)\s*$")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")

# PDF 文本抽取会把部分汉字映射为 CJK 部件 Supplement 码位（PI0.4a 实测：
# 见→⻅ 门→⻔ 风→⻛ 长→⻓ 等），heading_index 归一化需先映射回正字。
RADICAL_FORM_MAP = str.maketrans({
    "⻅": "见", "⻆": "角", "⻉": "贝", "⻋": "车", "⻑": "长", "⻓": "长",
    "⻔": "门", "⻗": "雨", "⻘": "青", "⻙": "韦", "⻚": "页", "⻛": "风",
    "⻜": "飞", "⻝": "食", "⻞": "食", "⻢": "马", "⻣": "骨", "⻤": "鬼",
    "⻥": "鱼", "⻦": "鸟", "⻨": "麦", "⻬": "齐", "⻭": "齿", "⻮": "齿",
})


# ---------------------------------------------------------------------------
# env 与小工具
# ---------------------------------------------------------------------------

def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def extract_json(text: str) -> dict | list | None:
    """从 LLM 输出中提取第一个 JSON 对象/数组（容忍代码围栏与前后废话）。"""
    text = re.sub(r"```(?:json)?", "", text or "")
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
    return None


def patch_pageindex_ratelimit(concurrency: int = 3) -> None:
    """给上游 ``pageindex.utils._acompletion`` 打限流补丁（POC 运行期 monkeypatch，
    不改上游源码）。

    上游对全部节点 ``asyncio.gather`` 并发发 LLM 请求，大文档会瞬时打爆
    供应商 RPM（实测智谱 429，重试 10 次耗尽后整树崩溃）。此补丁用全局
    信号量限制在飞请求数（``submit_document`` 的 ``summary_concurrency``
    之外的第二道保险，两条建树路径共用），并在上游重试耗尽后以指数退避
    整体重试；单节点 400 内容审核拒绝降级为截断原文当摘要（对齐上游
    fallback_summary 的 600 字符口径），计数后继续。
    """
    import litellm
    import pageindex.utils as pi_utils

    if getattr(pi_utils._acompletion, "_poc_ratelimit_wrapped", False):
        return  # 幂等：多文档建树重复调用时不再嵌套包装
    sem = asyncio.Semaphore(concurrency)
    original = pi_utils._acompletion
    retryable = (
        litellm.RateLimitError,
        litellm.InternalServerError,
        litellm.Timeout,
        litellm.APIConnectionError,
        pi_utils.LLMRetriesExhausted,
    )

    async def limited(model, messages, sent):
        delay = 5.0
        for attempt in range(10):
            async with sem:
                try:
                    return await original(model, messages, sent)
                except retryable:
                    if attempt == 9:
                        raise
                except litellm.BadRequestError as exc:
                    limited.moderation_skips += 1
                    prompt = ""
                    if messages and isinstance(messages[0], dict):
                        prompt = messages[0].get("content") or ""
                    text = prompt.split("Partial Document Text:", 1)[1] if \
                        "Partial Document Text:" in prompt else prompt
                    excerpt = " ".join(text.strip().split())[:600]
                    print(f"[poc] moderation fallback #{limited.moderation_skips}: "
                          f"{str(exc)[:80]}", file=sys.stderr)
                    return excerpt
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)
        raise RuntimeError("unreachable: retry loop exited")

    limited.moderation_skips = 0
    limited._poc_ratelimit_wrapped = True
    pi_utils._acompletion = limited


def moderation_skip_count() -> int:
    try:
        import pageindex.utils as pi_utils
        return getattr(pi_utils._acompletion, "moderation_skips", 0)
    except Exception:
        return 0


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pctl(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[idx]


def norm_key(s: str) -> str:
    """标题归一化：NFKC、去空白与强调符号、CJK 部件形映射回正字。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.translate(RADICAL_FORM_MAP)
    s = re.sub(r"[*_`~\[\]]", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def norm_text(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


# ---------------------------------------------------------------------------
# heading_index 原型（PI1 正式模块的预研版；D12 证据回定位）
# ---------------------------------------------------------------------------

def build_heading_index(md_text: str) -> list[dict]:
    """解析原 MD 标题（ATX + Setext，跳过围栏代码块与 front matter）。

    返回按行序的 ``{"level", "title", "line", "parents": [祖先标题...]}``。
    """
    lines = md_text.split("\n")
    # front matter：文件开头 --- 包围的 YAML 块，整体跳过
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    heads: list[dict] = []
    fence = None
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if fence:
            if re.match(r"^" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}\s*$", stripped):
                fence = None
            i += 1
            continue
        m = FENCE_RE.match(stripped)
        if m:
            fence = (m.group(1)[0], len(m.group(1)))
            i += 1
            continue
        m = ATX_RE.match(line)
        if m:
            heads.append({"level": len(m.group(1)), "title": m.group(2).strip(), "line": i + 1})
            i += 1
            continue
        # Setext：当前行是上一非空行的下划线（= 一级 / - 二级）
        if stripped and i > start and lines[i - 1].strip():
            prev = lines[i - 1].strip()
            level = None
            if re.fullmatch(r"=+", stripped):
                level = 1
            elif re.fullmatch(r"-{2,}", stripped) and not prev.startswith(("#", "-", "*", "+", ">")) \
                    and len(prev) <= 200 and SETEXT_UNDERLINE_RE.match(stripped):
                level = 2
            if level and not ATX_RE.match(prev):
                heads.append({"level": level, "title": prev, "line": i})
                i += 1
                continue
        i += 1
    # 祖先路径（同级或更高级标题截断栈）
    out: list[dict] = []
    stack: list[dict] = []
    for h in heads:
        while stack and stack[-1]["level"] >= h["level"]:
            stack.pop()
        h = {**h, "parents": [x["title"] for x in stack]}
        out.append(h)
        stack.append(h)
    return out


_MD_LINE_CACHE: dict[str, list[str]] = {}


def md_lines(md_path: Path) -> list[str]:
    """按路径缓存的原 MD 行（同一文档多节点读取避免反复全文件读盘）。"""
    key = str(md_path)
    if key not in _MD_LINE_CACHE:
        _MD_LINE_CACHE[key] = md_path.read_text(encoding="utf-8").split("\n")
    return _MD_LINE_CACHE[key]


class HeadingIndexProto:
    """树节点标题 → 原 MD 行区间回定位（含 Preface / (intro) 合成节点）。"""

    def __init__(self, md_text: str) -> None:
        self.heads = build_heading_index(md_text)
        self.first_heading_line = self.heads[0]["line"] if self.heads else None
        pre = md_text.split("\n")[: (self.first_heading_line - 1) if self.first_heading_line else None]
        self.pre_first_content = any(s.strip() for s in pre)
        self._lines = md_text.split("\n")

    def _span(self, idx: int) -> tuple[int, int]:
        h = self.heads[idx]
        for j in range(idx + 1, len(self.heads)):
            if self.heads[j]["level"] <= h["level"]:
                return (h["line"], self.heads[j]["line"] - 1)
        return (h["line"], len(self._lines))

    def locate(self, title: str, parent_path: list[str] | None = None) -> tuple[int, int] | None:
        """返回 (line_start, line_end)（1 起计、闭区间）；失败返回 None。"""
        key = norm_key(title)
        if not key:
            return None
        if title.strip() == "Preface":
            if self.pre_first_content and self.first_heading_line:
                return (1, self.first_heading_line - 1)
            return None
        is_intro = key.endswith("(intro)") or title.rstrip().endswith("(intro)")
        base_key = key[:-len("(intro)")] if is_intro else key
        cands = [i for i, h in enumerate(self.heads) if norm_key(h["title"]) == base_key]
        if not cands:
            return None
        if len(cands) > 1 and parent_path:
            pk = [norm_key(p) for p in parent_path if norm_key(p)]
            narrowed = []
            for i in cands:
                parents = [norm_key(p) for p in self.heads[i]["parents"]]
                # 父路径尾部对齐（Flash 树可能跳层）
                n = min(len(parents), len(pk))
                if n and parents[-n:] == pk[-n:]:
                    narrowed.append(i)
            if narrowed:
                cands = narrowed
        idx = cands[0]
        if is_intro:
            # <parent title> (intro)：父标题行到首个子标题前
            parent_level = self.heads[idx]["level"]
            for j in range(idx + 1, len(self.heads)):
                if self.heads[j]["level"] > parent_level:
                    return (self.heads[idx]["line"], self.heads[j]["line"] - 1)
                if self.heads[j]["level"] <= parent_level:
                    break
            return self._span(idx)
        return self._span(idx)


# ---------------------------------------------------------------------------
# MD → 派生 PDF（D11 终裁 v3 方案：pandoc → Chrome --print-to-pdf → pypdf Bookmarks）
# ---------------------------------------------------------------------------

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


def _chrome_cmd(out_pdf: Path, html_path: Path, user_data_dir: Path) -> list[str]:
    chrome = os.environ.get("PAGEINDEX_CHROMIUM") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--hide-scrollbars",
            f"--user-data-dir={user_data_dir}",
            f"--print-to-pdf={out_pdf}", "--no-pdf-header-footer",
            "file://" + str(html_path)]


def convert_md_to_pdf(md_path: Path, out_pdf: Path, timeout_s: int | None = None) -> dict:
    """MD → 派生 PDF（v3：注入原 MD 标题树 Bookmarks）。返回 {pages, elapsed_ms, bookmarks, map_misses}。

    Chrome 无法直接读 macOS TCC 保护目录（~/Desktop）：HTML 与 PDF 均经
    /tmp 暂存目录中转，产物原子落到 ``out_pdf``。
    """
    import pypdf
    import pypdfium2 as pdfium

    started = time.perf_counter()
    timeout_s = timeout_s or int(os.environ.get("PAGEINDEX_RENDER_TIMEOUT_S", "600"))
    with tempfile.TemporaryDirectory(prefix="pi_poc_render_") as stage:
        stage_path = Path(stage)
        html_proc = subprocess.run(
            ["pandoc", "-f", "gfm", "-t", "html5", "--wrap=none", str(md_path)],
            capture_output=True, text=True, timeout=timeout_s, check=True)
        staged_html = stage_path / "doc.html"
        staged_html.write_text(
            '<!doctype html><html><head><meta charset="utf-8"><style>'
            + PRINT_CSS + "</style></head><body>" + html_proc.stdout + "</body></html>",
            encoding="utf-8")
        staged_pdf = stage_path / "out.pdf"
        proc = subprocess.run(
            _chrome_cmd(staged_pdf, staged_html, stage_path / "profile"),
            capture_output=True, text=True, timeout=timeout_s)
        if proc.returncode != 0 or not staged_pdf.exists():
            raise RuntimeError(f"chrome render failed: {proc.stderr[-2000:]}")

        # 页映射：pypdfium2 全文提取 + 标题顺序匹配（单调推进）
        doc = pdfium.PdfDocument(str(staged_pdf))
        page_texts = []
        for page in doc:
            tp = page.get_textpage()
            page_texts.append(norm_text(tp.get_text_range()))
            tp.close()
            page.close()
        doc.close()
        joined = [page_texts[i] + (page_texts[i + 1] if i + 1 < len(page_texts) else "")
                  for i in range(len(page_texts))]
        headings = build_heading_index(md_path.read_text(encoding="utf-8"))
        mapping: list[int | None] = []
        misses = 0
        ptr = 0
        for h in headings:
            key = norm_key(h["title"])
            found = None
            for p in range(ptr, len(page_texts)):
                if key and (key in page_texts[p] or key in joined[p]):
                    found = p
                    break
            if found is None:
                misses += 1
                mapping.append(None)
            else:
                mapping.append(found)
                ptr = found
        # Bookmarks 注入（与派生 PDF 同一渲染，页码可直接复用）
        reader = pypdf.PdfReader(str(staged_pdf))
        writer = pypdf.PdfWriter()
        writer.append(reader)
        parent_stack: dict[int, object] = {}
        n_bookmarks = 0
        for h, page_no in zip(headings, mapping):
            if page_no is None:
                continue
            parent = parent_stack.get(h["level"] - 1)
            item = writer.add_outline_item(title=h["title"], page_number=page_no, parent=parent)
            parent_stack[h["level"]] = item
            for lv in [l for l in parent_stack if l > h["level"]]:
                del parent_stack[lv]
            n_bookmarks += 1
        buf = stage_path / "with_outline.pdf"
        with open(buf, "wb") as fh:
            writer.write(fh)
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = out_pdf.parent / (out_pdf.name + ".tmp")
        tmp_target.write_bytes(buf.read_bytes())
        tmp_target.replace(out_pdf)  # 原子落位
    return {"pages": len(page_texts), "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "bookmarks": n_bookmarks, "map_misses": misses}


# ---------------------------------------------------------------------------
# 文档树（MD：派生 PDF 建树；PDF：上游 DocStore）
# ---------------------------------------------------------------------------

class TreeDoc:
    def __init__(self, source_path: str, fmt: str, tree: dict, *,
                 doc_id: str | None = None, build_tokens: int = 0,
                 build_ms: float = 0.0, derived_pdf: str | None = None,
                 heading_index: HeadingIndexProto | None = None,
                 md_hash: str | None = None) -> None:
        self.source_path = source_path
        self.format = fmt
        self.tree = tree
        self.doc_id = doc_id
        self.build_tokens = build_tokens
        self.build_ms = build_ms
        self.derived_pdf = derived_pdf
        self.heading_index = heading_index
        self.md_hash = md_hash
        self._page_texts: list[str] | None = None

    def flat_nodes(self) -> list[dict]:
        out: list[dict] = []
        stack = list(self.tree.get("structure", []))
        while stack:
            node = stack.pop(0)
            out.append(node)
            stack = list(node.get("nodes", [])) + stack
        return sorted(out, key=lambda n: n.get("line_num") or n.get("start_index") or 0)

    def section_path(self, node: dict) -> str:
        target_id = node.get("node_id")

        def find(nodes: list[dict], prefix: list[str]) -> list[str] | None:
            for child in nodes:
                here = prefix + [child.get("title", "")]
                if child.get("node_id") == target_id or child is node:
                    return here
                found = find(child.get("nodes", []), here)
                if found is not None:
                    return found
            return None

        found = find(self.tree.get("structure", []), [])
        return " > ".join(found or [])

    def section_titles(self, node: dict) -> list[str]:
        return [t for t in self.section_path(node).split(" > ") if t]

    def page_text(self, page: int) -> str:
        """派生 PDF 页文本（回退读取用；pypdfium2 离线，零 LLM）。"""
        if self._page_texts is None:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(str(self.derived_pdf))
            texts = []
            for pg in doc:
                tp = pg.get_textpage()
                texts.append(tp.get_text_range())
                tp.close()
                pg.close()
            doc.close()
            self._page_texts = texts
        if 1 <= page <= len(self._page_texts):
            return self._page_texts[page - 1]
        return ""


def _flash_tree(pdf_path: Path) -> dict:
    from pageindex.flash import page_index_flash
    res = page_index_flash(pdf_path, summary=False, optimize=False)
    return {"structure": res.get("structure", []),
            "toc_source": res.get("toc_source")}


def _derived_cache_key(md_path: Path) -> str:
    return f"derived_{file_hash(md_path)[:16]}"


def build_md_tree_v2(path: Path, model: str, cache_dir: Path, *,
                     tree_source: str = "flash", concurrency: int = 3,
                     optimize: str | None = None) -> TreeDoc:
    """MD → 派生 PDF → 树（D3 / D11 / D12 原型）。

    tree_source:
    - ``flash``：零 LLM ``page_index_flash(summary=False, optimize=False)``，
      PI0.5 冒烟/口径修正用（无节点摘要，选节点 prompt 容忍空摘要）。
    - ``submit``：``PageIndexClient.submit_document``（LLM 写摘要，花真实
      token，PI0.4b / PI0.6 用）；显式 ``storage_path`` 与低
      ``summary_concurrency``，采集 ``usage["total_tokens"]``。
    """
    patch_pageindex_ratelimit(concurrency)
    md_hash = file_hash(path)
    cache = cache_dir / f"{_derived_cache_key(path)}_{tree_source}"
    cache = Path(str(cache) + (f"_{optimize}" if tree_source == "submit" else "") + ".json")
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("md_hash") == md_hash and data.get("tree_source") == tree_source:
            doc = TreeDoc(str(path), "md", data["tree"],
                          doc_id=data.get("doc_id"),
                          build_tokens=data.get("build_tokens", 0),
                          build_ms=data.get("build_ms", 0.0),
                          derived_pdf=data.get("derived_pdf"),
                          md_hash=md_hash)
            if data.get("derived_pdf") and Path(data["derived_pdf"]).exists():
                doc.heading_index = HeadingIndexProto(path.read_text(encoding="utf-8"))
                return doc
            # 派生 PDF 丢失：重走转换（树页码仍有效，Bookmarks 不必重写）
            render = convert_md_to_pdf(path, Path(data["derived_pdf"]))
            doc.heading_index = HeadingIndexProto(path.read_text(encoding="utf-8"))
            doc.build_ms = data.get("build_ms", 0.0) + render["elapsed_ms"]
            return doc

    derived_dir = PAGEINDEX_STORAGE / "derived"
    derived_pdf = derived_dir / f"{md_hash}.pdf"
    render = convert_md_to_pdf(path, derived_pdf)
    started = time.perf_counter()
    if tree_source == "flash":
        tree = _flash_tree(derived_pdf)
        doc_id = None
        tokens = 0
    else:
        from pageindex import PageIndexClient
        client = PageIndexClient(index_model=model, chat_model=model,
                                 storage_path=str(PAGEINDEX_STORAGE),
                                 summary_concurrency=concurrency,
                                 optimize=optimize)
        submitted = client.submit_document(str(derived_pdf))
        doc_id = submitted.get("doc_id") if isinstance(submitted, dict) else None
        usage = submitted.get("usage") or {} if isinstance(submitted, dict) else {}
        tokens = int(usage.get("total_tokens") or 0)
        structure = client.get_document_structure(doc_id)
        tree = structure if isinstance(structure, dict) else {"structure": structure}
    build_ms = round((time.perf_counter() - started) * 1000, 1) + render["elapsed_ms"]
    doc = TreeDoc(str(path), "md", tree, doc_id=doc_id, build_tokens=tokens,
                  build_ms=build_ms, derived_pdf=str(derived_pdf), md_hash=md_hash)
    doc.heading_index = HeadingIndexProto(path.read_text(encoding="utf-8"))
    cache.write_text(json.dumps({
        "tree": tree, "doc_id": doc_id, "build_tokens": tokens, "build_ms": build_ms,
        "derived_pdf": str(derived_pdf), "md_hash": md_hash, "tree_source": tree_source,
        "optimize": optimize, "render": render,
    }, ensure_ascii=False), encoding="utf-8")
    return doc


def build_pdf_tree(path: Path, model: str, cache_dir: Path, *,
                   concurrency: int = 3) -> TreeDoc:
    from pageindex import PageIndexClient

    patch_pageindex_ratelimit(concurrency)
    cache = cache_dir / f"pdf_{file_hash(path)[:16]}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        doc = TreeDoc(str(path), "pdf", data["tree"], doc_id=data["doc_id"],
                      build_tokens=data.get("build_tokens", 0),
                      build_ms=data.get("build_ms", 0.0))
        return doc

    client = PageIndexClient(index_model=model, chat_model=model,
                             storage_path=str(PAGEINDEX_STORAGE),
                             summary_concurrency=concurrency)
    started = time.perf_counter()
    submitted = client.submit_document(str(path))
    doc_id = submitted.get("doc_id") if isinstance(submitted, dict) else None
    usage = submitted.get("usage") or {} if isinstance(submitted, dict) else {}
    tokens = int(usage.get("total_tokens") or 0)
    structure = client.get_document_structure(doc_id)
    build_ms = round((time.perf_counter() - started) * 1000, 1)
    tree = structure if isinstance(structure, dict) else {"structure": structure}
    doc = TreeDoc(str(path), "pdf", tree, doc_id=doc_id, build_tokens=tokens,
                  build_ms=build_ms)
    cache.write_text(json.dumps({
        "tree": tree, "doc_id": doc_id, "build_tokens": tokens, "build_ms": build_ms,
    }, ensure_ascii=False), encoding="utf-8")
    return doc


# ---------------------------------------------------------------------------
# 树检索原型（选文档 → 逐层选节点 → 读原文 → 判是否足够）
# ---------------------------------------------------------------------------

class TreeSearcher:
    """逐层下钻检索（0.5 调优：调用前预算检查、单层预筛、max_nodes 硬截断、
    「读原文 → 判是否足够」闭环、多文档并行、D12 证据回映射）。"""

    def __init__(self, docs: list[TreeDoc], complete, *, max_nodes: int = 5,
                 max_loops: int = 3, max_token_budget: int = 32000,
                 get_pdf_page=None) -> None:
        self.docs = docs
        self.complete = complete
        self.max_nodes = max_nodes
        self.max_loops = max_loops
        self.max_token_budget = max_token_budget
        self.get_pdf_page = get_pdf_page
        self.llm_calls = 0
        self.tokens = 0
        self.last_tokens = 0   # 最近一次 search 的本题 token（每题独立预算口径）
        self.last_calls = 0
        self.telemetry: dict = {}

    def _telemetry_add(self, key: str, n: int = 1) -> None:
        self.telemetry[key] = self.telemetry.get(key, 0) + n

    @staticmethod
    def _estimate_tokens(prompt: str) -> int:
        return max(1, len(prompt) // 2)  # 中文粗估：~2 字符 / token

    def search(self, query: str, source_paths: list[str] | None) -> list[dict]:
        candidates = [d for d in self.docs
                      if source_paths is None or d.source_path in source_paths]
        if not candidates:
            return []
        self.last_tokens = 0
        self.last_calls = 0
        rank = 0
        lock = threading.Lock()
        telemetry_q: dict = {}

        def ask(prompt: str) -> str | None:
            """调用前预算检查：预估超剩余预算则不发起调用（返回 None）。"""
            estimate = self._estimate_tokens(prompt)
            with lock:
                if self.last_tokens + estimate > self.max_token_budget:
                    telemetry_q["budget_stopped"] = telemetry_q.get("budget_stopped", 0) + 1
                    return None
            result = self.complete(prompt)
            with lock:
                self.last_tokens += result["tokens"]
                self.last_calls += 1
                self.llm_calls += 1
                self.tokens += result["tokens"]
            return result["text"]

        # 1) 选文档（候选 > 1 时）
        picked = candidates
        if len(candidates) > 1:
            listing = "\n".join(f"- [{i}] {d.source_path}（{d.format}）"
                                for i, d in enumerate(candidates))
            text = ask(
                "以下是可选文档列表。判断哪些文档可能包含回答问题所需的内容，"
                "返回 JSON：{\"doc_ids\": [索引]}。\n\n"
                f"问题：{query}\n\n文档列表（索引从 0 开始）：\n{listing}\n\n"
                '只返回 JSON，例如 {"doc_ids": [0, 2]}'
            )
            data = extract_json(text) if text is not None else None
            ids = (data or {}).get("doc_ids") if isinstance(data, dict) else None
            if not isinstance(ids, list) or not ids:
                telemetry_q["invalid_output"] = telemetry_q.get("invalid_output", 0) + 1
                picked = candidates
            else:
                picked = [candidates[i] for i in ids
                          if isinstance(i, int) and 0 <= i < len(candidates)] or candidates

        evidence_by_node: dict[tuple, dict] = {}
        total_selected = 0
        truncated_any = False

        def read_one(doc: TreeDoc, node: dict) -> dict | None:
            nonlocal rank, total_selected, truncated_any
            key = (doc.source_path, node.get("node_id"))
            if key in evidence_by_node:
                return None
            if doc.format == "md":
                ls, le, text, truncated, how = self._read_md_node(doc, node)
                page_start = page_end = None
                if how == "unlocated":
                    telemetry_q["unlocated"] = telemetry_q.get("unlocated", 0) + 1
                if how == "page_fallback":
                    telemetry_q["page_fallback"] = telemetry_q.get("page_fallback", 0) + 1
            else:
                page_start, page_end, text, truncated = self._read_pdf_node(doc, node)
                ls = le = None
            with lock:
                if total_selected >= self.max_nodes:
                    return None  # 总输出节点硬截断：不再新增证据
                total_selected += 1
                rank += 1
                truncated_any = truncated_any or truncated
            return {
                "source_path": doc.source_path,
                "doc_id": doc.doc_id,
                "node_id": node.get("node_id"),
                "section": doc.section_path(node),
                "line_start": ls,
                "line_end": le,
                "page_start": page_start,
                "page_end": page_end,
                "text": text,
                "score": round(1 / rank, 4),
                "truncated": truncated,
            }

        def select_for_doc(doc: TreeDoc, frontier: list[dict]) -> tuple[list[dict], list[dict], bool]:
            """单文档一轮：返回 (reads, next_frontier, progressed)。"""
            if not frontier:
                return [], [], False
            nodes = frontier
            if len(nodes) > PREFILTER_THRESHOLD:
                listing = "\n".join(f"- [{n.get('node_id')}] {n.get('title')}"
                                    for n in nodes)
                text = ask(
                    "下面是文档某一层级的候选节点列表（过多，请先压缩筛选）。\n"
                    f"保留与问题可能相关的最多 {PREFILTER_KEEP} 个节点，返回 JSON："
                    "{\"keep\": [\"0001\"]}。只返回 JSON。\n\n"
                    f"问题：{query}\n\n候选节点：\n" + listing
                )
                data = extract_json(text) if text is not None else None
                keep = data.get("keep") if isinstance(data, dict) else None
                if isinstance(keep, list) and keep:
                    by_id = {str(n.get("node_id")): n for n in nodes}
                    nodes = [by_id[str(k)] for k in keep if str(k) in by_id] or nodes
                    with lock:
                        telemetry_q["prefilter_used"] = telemetry_q.get("prefilter_used", 0) + 1
            lines = []
            for n in nodes:
                summary = (n.get("summary") or n.get("prefix_summary") or "").strip()
                if len(summary) > 120:
                    summary = summary[:120] + "…"
                has_children = "＋" if n.get("nodes") else "·"
                lines.append(f"- [{n.get('node_id')}] {has_children} {n.get('title')}"
                             + (f" — {summary}" if summary else ""))
            text = ask(
                "下面是文档某一层级的节点列表（＋= 有子节点可展开，·= 叶节点）。\n"
                f"选出与回答问题相关的节点（最多 {self.max_nodes} 个）：要读原文的放进 "
                "\"read\"，要看其子节点的放进 \"expand\"（只能选带＋的）。\n"
                "返回 JSON：{\"read\": [\"0000\"], \"expand\": [\"0001\"]}。只返回 JSON。\n\n"
                f"问题：{query}\n文档：{doc.source_path}\n\n节点列表：\n" + "\n".join(lines)
            )
            data = extract_json(text) if text is not None else None
            read_ids = data.get("read") if isinstance(data, dict) else None
            expand_ids = data.get("expand") if isinstance(data, dict) else None
            if not isinstance(read_ids, list) and not isinstance(expand_ids, list):
                with lock:
                    telemetry_q["invalid_output"] = telemetry_q.get("invalid_output", 0) + 1
                return [], [], False
            by_id = {str(n.get("node_id")): n for n in doc.flat_nodes()}
            reads: list[dict] = []
            next_level: list[dict] = []
            for nid in (expand_ids or []):
                node = by_id.get(str(nid))
                if node is None:
                    continue
                children = node.get("nodes") or []
                if children:
                    next_level.extend(children)
                else:  # 叶节点无可展开：直接读
                    reads.append(node)
            for nid in (read_ids or []):
                node = by_id.get(str(nid))
                if node is not None:
                    reads.append(node)
            return reads, next_level, bool(next_level)

        frontier: dict[str, list[dict]] = {d.source_path: list(self.tree_nodes(d)) for d in picked}
        for _loop in range(self.max_loops):
            if all(not v for v in frontier.values()):
                break
            # 2) 逐层选节点（多文档并行）
            jobs = [(doc, frontier.get(doc.source_path) or []) for doc in picked
                    if frontier.get(doc.source_path)]
            results = []
            if len(jobs) > 1:
                with ThreadPoolExecutor(max_workers=min(3, len(jobs))) as pool:
                    futures = [pool.submit(select_for_doc, doc, nodes)
                               for doc, nodes in jobs]
                    results = [f.result() for f in futures]
            else:
                results = [select_for_doc(doc, nodes) for doc, nodes in jobs]
            new_reads: list[tuple[TreeDoc, dict]] = []
            progressed = False
            for doc, (reads, next_level, prog) in zip([j[0] for j in jobs], results):
                frontier[doc.source_path] = next_level
                progressed = progressed or prog
                for node in reads:
                    if total_selected >= self.max_nodes:
                        break
                    new_reads.append((doc, node))

            # 3) 读原文（max_nodes 硬截断 + 超长下钻）
            for doc, node in list(new_reads):
                text_len_hint = None
                if doc.format == "md":
                    # 超长节点优先下钻子节点，不急读
                    approx_span = doc.heading_index.locate(
                        node.get("title", ""), doc.section_titles(node)) \
                        if doc.heading_index else None
                    if approx_span and (approx_span[1] - approx_span[0] + 1) > 400 \
                            and node.get("nodes"):
                        frontier.setdefault(doc.source_path, []).extend(node.get("nodes") or [])
                        new_reads.remove((doc, node))
                        progressed = True
                        continue
                ev = read_one(doc, node)
                if ev is not None:
                    evidence_by_node[(doc.source_path, node.get("node_id"))] = ev

            if not new_reads and not progressed:
                break
            if total_selected >= self.max_nodes:
                telemetry_q["max_nodes_capped"] = telemetry_q.get("max_nodes_capped", 0) + 1
                break

            # 4) 判是否足够（有新证据时）：足够则提前收敛
            if evidence_by_node:
                snippets = "\n\n".join(
                    f"[{e['section']}] {e['text'][:500]}" for e in
                    sorted(evidence_by_node.values(), key=lambda x: x["score"], reverse=True))
                text = ask(
                    "基于已读到的证据，判断是否足够回答问题。返回 JSON："
                    "{\"sufficient\": true/false, \"missing\": \"还缺什么\"}。只返回 JSON。\n\n"
                    f"问题：{query}\n\n已有证据：\n{snippets[:6000]}"
                )
                data = extract_json(text) if text is not None else None
                if isinstance(data, dict) and data.get("sufficient") is True:
                    telemetry_q["sufficient_stop"] = telemetry_q.get("sufficient_stop", 0) + 1
                    break

        if truncated_any:
            telemetry_q["truncated_evidence"] = telemetry_q.get("truncated_evidence", 0) + 1
        evidences = sorted(evidence_by_node.values(),
                           key=lambda e: e["score"], reverse=True)
        # 总输出节点硬截断（防御：超预算截停时已选数可能超限）
        evidences = evidences[: self.max_nodes]
        for k, v in telemetry_q.items():
            self.telemetry[k] = self.telemetry.get(k, 0) + v
        return evidences

    def tree_nodes(self, doc: TreeDoc) -> list[dict]:
        return doc.tree.get("structure", [])

    def _read_md_node(self, doc: TreeDoc, node: dict) -> tuple[int | None, int | None, str, bool, str]:
        """D12：MD 证据优先按回定位行区间从原 MD 读取；失败回退派生 PDF 页文本。

        返回 (line_start, line_end, text, truncated, how)。
        how: relocated | unlocated | page_fallback
        """
        md_path = Path(doc.source_path)
        span = None
        if doc.heading_index:
            span = doc.heading_index.locate(node.get("title", ""), doc.section_titles(node))
        if span:
            start, end = span
            try:
                lines = md_lines(md_path)
                if 1 <= start <= end <= len(lines):
                    text = "\n".join(lines[start - 1:end])
                    truncated = False
                    if len(text) > MAX_EVIDENCE_CHARS:
                        text = text[:MAX_EVIDENCE_CHARS]
                        truncated = True
                    return start, end, text, truncated, "relocated"
            except OSError:
                pass
            return None, None, "", False, "page_fallback"
        # 回定位失败：退化为派生 PDF 页文本（页码对用户无意义，仅作证据内容）
        if doc.derived_pdf and Path(doc.derived_pdf).exists():
            pages = []
            start = node.get("start_index") or 1
            end = node.get("end_index") or start
            for page in range(int(start), min(int(end), int(start) + 5) + 1):
                pages.append(doc.page_text(page))
            text = "\n\n".join(t for t in pages if t)
            if len(text) > MAX_EVIDENCE_CHARS:
                text = text[:MAX_EVIDENCE_CHARS]
            return None, None, text, False, "unlocated"
        return None, None, "", False, "unlocated"

    def _read_pdf_node(self, doc: TreeDoc, node: dict) -> tuple[int, int, str, bool]:
        # PDF 树节点页码字段为 start_index / end_index（物理页，1 起计）
        start = node.get("start_index") or 1
        end = node.get("end_index") or start
        text = ""
        if self.get_pdf_page and doc.doc_id:
            pages = []
            for page in range(int(start), min(int(end), int(start) + 5) + 1):
                page_text = self.get_pdf_page(doc.doc_id, page)
                if page_text:
                    pages.append(page_text)
            text = "\n\n".join(pages)
        truncated = False
        if len(text) > MAX_EVIDENCE_CHARS:
            text = text[:MAX_EVIDENCE_CHARS]
            truncated = True
        return int(start), int(end), text, truncated


# ---------------------------------------------------------------------------
# 四路编排与评分（0.5 口径）
# ---------------------------------------------------------------------------

def run_main_route(main_python: str, route: str, source: str, questions: str,
                   out_dir: Path, config: str | None, collection: str | None,
                   extra_env: dict[str, str] | None = None,
                   pdf_path: str | None = None) -> list[dict]:
    out_path = out_dir / f"main_{route}_{source}.jsonl"
    cmd = [main_python, str(REPO_ROOT / "scripts" / "pageindex" / "poc_main_routes.py"),
           "--route", route, "--questions", questions, "--source", source,
           "--out", str(out_path)]
    if config:
        cmd += ["--config", config]
    if collection:
        cmd += ["--collection", collection]
    if pdf_path:
        cmd += ["--pdf-path", pdf_path]
    env = dict(os.environ)
    env.update(extra_env or {})
    print(f"[poc] main route {route}/{source} -> {out_path.name}", file=sys.stderr)
    subprocess.run(cmd, check=True, cwd=str(REPO_ROOT), env=env)
    return [json.loads(l) for l in out_path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _topk_evidences(record: dict, source: str, k: int) -> list[dict]:
    """统一前 k 条证据：tree 按score降序；主路由按返回序（融合排名）。"""
    if record.get("route") == "tree":
        evs = sorted(record.get("evidences", []),
                     key=lambda e: e.get("score") or 0, reverse=True)
        return evs[:k]
    return list(record.get("results", []))[:k]


def _evidence_spans(evs: list[dict], source: str, target: str | None
                    ) -> tuple[list[tuple[int, int]], list[str], int, list[str]]:
    """返回 (同文件 spans, sections, 跨文件数, [(src, text)...] 跨文件证据文本)。"""
    spans: list[tuple[int, int]] = []
    sections: list[str] = []
    crossfile = 0
    cross_texts: list[str] = []
    for e in evs:
        loc = e.get("loc") or {}
        meta = e.get("metadata") or {}
        if source == "md":
            src = e.get("source_path") or loc.get("source") or meta.get("source_path") or ""
            if target and src and str(Path(src).resolve()) != target:
                crossfile += 1
                cross_texts.append(e.get("text") or "")
                continue
            ls, le = e.get("line_start"), e.get("line_end")
            if not ls:
                ls, le = loc.get("line_start"), loc.get("line_end")
            if ls:
                spans.append((ls, le or ls))
                sections.append(e.get("section") or loc.get("section") or "")
        else:
            page = e.get("page_start") or loc.get("page_start") or meta.get("page")
            if page:
                page_end = e.get("page_end") or loc.get("page_end") or page
                spans.append((int(page), int(page_end)))
                sections.append(e.get("section") or loc.get("section") or "")
    return spans, sections, crossfile, cross_texts


def score_questions(records: list[dict], questions: dict[str, dict], source: str,
                    route: str, k: int = TOP_K,
                    target_text_norm: str | None = None) -> list[dict]:
    scored = []
    for rec in records:
        q = questions[rec["id"]]
        targets = [(t["target_lines"][0], t["target_lines"][1]) if "target_lines" in t
                   else (t["target_pages"][0], t["target_pages"][1])
                   for t in q["target_sections"]]
        target = str((REPO_ROOT / q["path"]).resolve()) if source == "md" else None
        evs = _topk_evidences(rec, source, k) if rec.get("ok") else []
        spans, sections, crossfile, cross_texts = _evidence_spans(evs, source, target)
        elapsed = rec.get("meta", {}).get("elapsed_ms") or 0
        tokens = rec.get("meta", {}).get("total_llm_tokens") or 0

        # hit 双口径（严格同文件 / 内容等价）
        hit_strict = overlap(spans, targets) if rec.get("ok") else False
        hit_equiv = hit_strict
        if not hit_strict and rec.get("ok") and target_text_norm and cross_texts:
            for t in cross_texts:
                snippet = norm_text(t)[:120]
                if snippet and snippet in target_text_norm:
                    hit_equiv = True
                    break

        # section recall：目标小节粒度覆盖率（严格同文件口径）
        if len(targets) > 1 and rec.get("ok"):
            covered = sum(1 for (ts, te) in targets
                          if any(s <= te and e2 >= ts for (s, e2) in spans))
            section_recall = covered / len(targets)
        else:
            section_recall = 1.0 if hit_strict else 0.0

        # 证据精度：top-k 中落在任一目标范围内的条数占比（严格同文件）
        precision = (sum(1 for (s, e2) in spans
                         if any(s <= te and e2 >= ts for (ts, te) in targets)) / len(evs)
                     ) if evs else 0.0

        # locatable 生产口径分路
        if not rec.get("ok"):
            locatable = False
        elif route == "plan_a":
            # 方案 (a) 口径：计离线回定位（md 行区间 / pdf 页码）
            locatable = any((e.get("loc") or {}).get("line_start")
                            or (e.get("loc") or {}).get("page_start") for e in evs)
        elif route == "tree":
            if source == "md":
                locatable = any(e.get("line_start") for e in evs)
            else:
                locatable = any(e.get("page_start") for e in evs)
        else:
            # hybrid / agentic_deep：只认产品自身输出（现状无 section/line/page；
            # loc 字段是离线回定位标注，不算产品输出）
            def _prod_loc(e: dict) -> bool:
                meta = e.get("metadata") or {}
                return bool(meta.get("section") or meta.get("line_start")
                            or meta.get("page"))
            locatable = any(_prod_loc(e) for e in evs)

        scored.append({
            "id": rec["id"], "type": q.get("type", "locate"),
            "ok": rec.get("ok", False),
            "hit": hit_strict, "hit_equiv": hit_equiv,
            "section_recall": round(section_recall, 3),
            "precision": round(precision, 3),
            "locatable": locatable,
            "elapsed_ms": elapsed, "tokens": tokens,
            "n_evidences": len(evs), "n_crossfile": crossfile,
            "error": rec.get("error"),
        })
    return scored


def overlap(spans: list[tuple[int, int]], targets: list[tuple[int, int]]) -> bool:
    return any(s <= te and ts <= e for (s, e) in spans for (ts, te) in targets)


def summarize(scored: list[dict]) -> dict:
    ok = [s for s in scored if s["ok"]]
    lat = [s["elapsed_ms"] for s in ok]

    def block(rows: list[dict]) -> dict:
        return {
            "n_ok": len(rows),
            "hit": round(sum(r["hit"] for r in rows) / len(rows), 3) if rows else None,
            "hit_equiv": round(sum(r["hit_equiv"] for r in rows) / len(rows), 3) if rows else None,
            "section_recall": round(statistics.mean(r["section_recall"] for r in rows), 3) if rows else None,
            "precision": round(statistics.mean(r["precision"] for r in rows), 3) if rows else None,
            "locatable": round(sum(r["locatable"] for r in rows) / len(rows), 3) if rows else None,
        }

    by_type = {t: block([s for s in ok if s.get("type") == t])
               for t in sorted({s.get("type", "locate") for s in ok})}
    return {
        "n": len(scored), "ok": len(ok), **block(ok),
        "p50_ms": round(pctl(lat, 50), 1), "p95_ms": round(pctl(lat, 95), 1),
        "mean_tokens": round(statistics.mean([s["tokens"] for s in ok]), 1) if ok else 0,
        "by_type": by_type,
    }


# ---------------------------------------------------------------------------
# fake LLM（--smoke-fake：零 token 冒烟，按 prompt 类型返回罐头 JSON）
# ---------------------------------------------------------------------------

def fake_complete(prompt: str) -> dict:
    """零 token 冒烟罐头：按 prompt 类型返回合法 JSON，驱动全部循环分支。"""
    if "可选文档列表" in prompt:
        ids = [int(m) for m in re.findall(r"\[(\d+)\]", prompt.split("文档列表")[-1])]
        return {"text": json.dumps({"doc_ids": ids[:1]}), "tokens": 0}
    if "请先压缩筛选" in prompt:
        ids = re.findall(r"^\s*-\s*\[([^\]]+)\]", prompt.split("候选节点：")[-1], re.M)
        return {"text": json.dumps({"keep": ids[:PREFILTER_KEEP]}), "tokens": 0}
    if "节点列表" in prompt:
        lines = re.findall(r"^\s*-\s*\[([^\]]+)\]\s+(＋|·)", prompt.split("节点列表：\n")[-1], re.M)
        read = [i for i, _mk in lines if _mk == "·"][:1] or [lines[0][0]] if lines else []
        expand = [i for i, mk in lines if mk == "＋"][:1]
        return {"text": json.dumps({"read": read, "expand": expand}), "tokens": 0}
    if "是否足够" in prompt:
        return {"text": json.dumps({"sufficient": True}), "tokens": 0}
    return {"text": "{}", "tokens": 0}


# ---------------------------------------------------------------------------
# 工作区（PDF 对照隔离）
# ---------------------------------------------------------------------------

def prepare_pdf_workspace(out_dir: Path, pdf_path: Path,
                          sirchmunk_env: dict[str, str]) -> tuple[Path, str, str]:
    """PDF 对照隔离工作区：RAG_PROJECT_ROOT 临时目录 + 临时配置（agentic DEEP）。

    工作区放系统临时目录而非 ``data/db/`` 下：PathGuard 的 denied
    substring 含 ``data/db``（防索引库进语料），工作区建在其下会让
    collection 定界把语料自身判为越权（实测踩坑：collection_paths 全部
    记录被剔除 → 空定界集 → 零证据）。
    """
    import yaml

    ws = Path(tempfile.gettempdir()) / "pi0_poc_ws"
    docs_dir = ws / "data" / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (ws / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    target = docs_dir / pdf_path.name
    if not target.exists():
        target.write_bytes(pdf_path.read_bytes())

    base = yaml.safe_load((REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    ag = base.setdefault("agentic_search", {})
    ag.update({
        "enabled": True, "mode": "DEEP", "allow_deep": True,
        "base_url": sirchmunk_env.get("SIRCHMUNK_BASE_URL", "http://127.0.0.1:8584"),
        "api_token": sirchmunk_env.get("SIRCHMUNK_API_TOKEN", "${SIRCHMUNK_API_TOKEN}"),
        "allowed_roots": ["./data/documents", "./data/uploads"],
    })
    base.setdefault("chat", {})["default_retrieval"] = "hybrid"
    config_path = ws / "settings_poc.yaml"
    config_path.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    return ws, str(config_path), "pi0_poc"


# ---------------------------------------------------------------------------
# 主编排
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", default="tests/fixtures/pageindex/poc_questions.jsonl")
    parser.add_argument("--out", default="data/db/pageindex/poc")
    parser.add_argument("--main-python", default=str(REPO_ROOT / ".venv" / "bin" / "python"))
    parser.add_argument("--routes", default="tree,hybrid,agentic_deep,plan_a")
    parser.add_argument("--source", default="all", choices=["md", "pdf", "all"])
    parser.add_argument("--md-paths", nargs="*", default=DEFAULT_MD_PATHS)
    parser.add_argument("--index-only", action="store_true", help="只测建树成本")
    parser.add_argument("--max-nodes", type=int, default=5)
    parser.add_argument("--max-loops", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3,
                        help="建树期 LLM 并发上限（限流补丁；智谱账户 RPM 低时调小）")
    parser.add_argument("--tree-source", default="flash", choices=["flash", "submit"],
                        help="MD 建树来源：flash=零 LLM 结构（冒烟/口径修正）；"
                             "submit=submit_document 真实建树（PI0.4b/PI0.6，花 token）")
    parser.add_argument("--submit-optimize", default="full", choices=["full", "merge"],
                        help="submit 建树的 optimize 档（PI0.4b 对比 merge vs full；"
                             "merge=确定性合并不改写标题、省 LLM expand）")
    parser.add_argument("--smoke-fake", action="store_true",
                        help="fake LLM 冒烟：零 token 跑通新管线与检索循环")
    parser.add_argument("--rebuild-pdf-tree", action="store_true",
                        help="忽略 PDF 树缓存重新建树（采集真实 usage token 时用）")
    parser.add_argument("--rescore-only", action="store_true",
                        help="只按 0.5 口径对落盘明细重算评分（零 LLM，不建树不跑路由）")
    args = parser.parse_args()
    poc_concurrency = args.concurrency

    out_dir = REPO_ROOT / args.out
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    questions_list = [json.loads(l) for l in (REPO_ROOT / args.questions)
                      .read_text(encoding="utf-8").splitlines() if l.strip()]
    questions = {q["id"]: q for q in questions_list}

    if args.rescore_only:
        # 0.5 口径重算：main_{route}_{source}.jsonl + tree_{source}.jsonl → 新汇总
        routes = [r.strip() for r in args.routes.split(",")]
        sources = ["md", "pdf"] if args.source == "all" else [args.source]
        md_norm = "\n".join(norm_text((REPO_ROOT / rel).read_text(encoding="utf-8"))
                            for rel in args.md_paths if (REPO_ROOT / rel).exists())
        all_scored: dict[str, dict] = {}
        for source in sources:
            target_text_norm = md_norm if source == "md" else None
            for route in routes:
                path = out_dir / (f"tree_{source}.jsonl" if route == "tree"
                                  else f"main_{route}_{source}.jsonl")
                if not path.exists():
                    print(f"[rescore] missing detail: {path.name}", file=sys.stderr)
                    continue
                recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
                        if l.strip()]
                all_scored[f"{route}/{source}"] = score_questions(
                    recs, questions, source, route, target_text_norm=target_text_norm)
        summary = {key: summarize(v) for key, v in all_scored.items()}
        report_path = out_dir / "poc_results_rescored.json"
        report_path.write_text(json.dumps(
            {"summary": summary, "detail": all_scored,
             "metrics_note": {"top_k": TOP_K,
                              "locatable": "plan_a=offline annotate; hybrid/agentic_deep=product output only; tree=tree fields"}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n=== PI0 重算汇总（0.5 口径，零 LLM） ===")
        print(f"{'route/source':<24}{'n':>4}{'hit':>7}{'hitEq':>7}{'sRec':>7}{'prec':>7}"
              f"{'locat':>7}{'p50(ms)':>9}{'p95(ms)':>9}{'tokens':>9}")
        for key, s in summary.items():
            print(f"{key:<24}{s['n']:>4}{str(s['hit']):>7}{str(s['hit_equiv']):>7}"
                  f"{str(s['section_recall']):>7}{str(s['precision']):>7}{str(s['locatable']):>7}"
                  f"{s['p50_ms']:>9}{s['p95_ms']:>9}{s['mean_tokens']:>9.1f}")
        print(f"\nreport -> {report_path}")
        return 0

    env = load_env_file(REPO_ROOT / ".env-pageindex")
    llm_model = env.get("LLM_MODEL", "openai/glm-5.3-flash")
    os.environ.setdefault("OPENAI_API_BASE", env.get("LLM_API_BASE", ""))
    os.environ.setdefault("OPENAI_API_KEY", env.get("LLM_API_KEY", ""))

    if args.smoke_fake:
        complete = fake_complete
        patch_pageindex_ratelimit(poc_concurrency)
    else:
        import litellm

        def complete(prompt: str) -> dict:
            started = time.perf_counter()
            resp = None
            for attempt in range(3):  # 连接类错误重试：3 次 + 指数退避
                try:
                    resp = litellm.completion(
                        model=llm_model,
                        messages=[{"role": "user", "content": prompt}],
                        api_base=env.get("LLM_API_BASE"),
                        api_key=env.get("LLM_API_KEY"),
                        temperature=0,
                    )
                    break
                except Exception as exc:
                    if attempt == 2 or "Connection error" not in str(exc):
                        raise
                    time.sleep(2 ** attempt)
            usage = getattr(resp, "usage", None)
            return {
                "text": resp.choices[0].message.content or "",
                "tokens": getattr(usage, "total_tokens", 0) or 0,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }

    # ---- 建树（成本单列）----
    pdf_rel = "data/db/pageindex/poc/2023-annual-report.pdf"
    pdf_path = REPO_ROOT / pdf_rel
    docs: list[TreeDoc] = []
    build_rows = []
    for rel in args.md_paths:
        doc = build_md_tree_v2(REPO_ROOT / rel, llm_model, cache_dir,
                               tree_source=args.tree_source,
                               concurrency=poc_concurrency,
                               optimize=args.submit_optimize)
        docs.append(doc)
        build_rows.append({"source_path": rel, "format": "md",
                           "pipeline": "derived-pdf/" + args.tree_source
                                       + (f"/{args.submit_optimize}" if args.tree_source == "submit" else ""),
                           "node_count": len(doc.flat_nodes()),
                           "build_ms": doc.build_ms, "build_tokens": doc.build_tokens})
    if pdf_path.exists():
        if args.rebuild_pdf_tree and (cache_dir / f"pdf_{file_hash(pdf_path)[:16]}.json").exists():
            (cache_dir / f"pdf_{file_hash(pdf_path)[:16]}.json").unlink()
        doc = build_pdf_tree(pdf_path, llm_model, cache_dir, concurrency=poc_concurrency)
        docs.append(doc)
        build_rows.append({"source_path": pdf_rel, "format": "pdf", "pipeline": "docstore",
                           "node_count": len(doc.flat_nodes()),
                           "build_ms": doc.build_ms, "build_tokens": doc.build_tokens})
    print(json.dumps({"build_cost": build_rows,
                      "moderation_skips": moderation_skip_count()},
                     ensure_ascii=False, indent=2))

    if args.index_only:
        return 0

    # ---- 树检索原型跑题 ----
    def get_pdf_page(doc_id: str, page: int) -> str:
        """上游 ``get_page_content(doc_id, pages: str) -> list[{page_index, markdown}]``。"""
        try:
            from pageindex import PageIndexClient
            client = PageIndexClient(index_model=llm_model, chat_model=llm_model,
                                     storage_path=str(PAGEINDEX_STORAGE))
            result = client.get_page_content(doc_id, str(page))
            if isinstance(result, list):
                return "\n\n".join(
                    item.get("markdown") or "" for item in result if isinstance(item, dict))
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            print(f"[poc] get_page_content failed: {type(exc).__name__}: {str(exc)[:120]}",
                  file=sys.stderr)
            return ""

    routes = [r.strip() for r in args.routes.split(",")]
    sources = ["md", "pdf"] if args.source == "all" else [args.source]

    if args.smoke_fake:
        # 冒烟：每 source 取 1 题，断言证据非空（含 get_pdf_page 证据非空断言）
        failures = []
        for source in sources:
            cand_paths = args.md_paths if source == "md" else [pdf_rel]
            abs_paths = [str(REPO_ROOT / p) for p in cand_paths]
            qs = [q for q in questions_list if q["source"] == source][:1]
            if not qs:
                continue
            searcher = TreeSearcher(docs, complete, max_nodes=args.max_nodes,
                                    max_loops=args.max_loops,
                                    get_pdf_page=get_pdf_page)
            evidences = searcher.search(qs[0]["question"], abs_paths)
            nonempty = [e for e in evidences if (e.get("text") or "").strip()]
            print(f"[smoke] {source}: q={qs[0]['id']} evidences={len(evidences)} "
                  f"nonempty={len(nonempty)} telemetry={searcher.telemetry}")
            if not nonempty:
                failures.append(f"{source}: evidence text empty")
        if failures:
            print(f"[smoke] FAIL: {failures}", file=sys.stderr)
            return 1
        print("[smoke] PASS（fake LLM，零 token）")
        return 0

    sirchmunk_env = load_env_file(REPO_ROOT / ".env-sirchmunk")
    searcher = TreeSearcher(docs, complete, max_nodes=args.max_nodes,
                            max_loops=args.max_loops, get_pdf_page=get_pdf_page)

    all_scored: dict[str, dict] = {}
    # tree 路不在本轮 routes 时，从落盘明细复跑评分（零 LLM）；无明细则
    # 退回上一轮 poc_results.json 的评分行。
    prior_tree_telemetry: dict | None = None
    if "tree" not in routes:
        for source in sources:
            tree_path = out_dir / f"tree_{source}.jsonl"
            if tree_path.exists():
                recs = [json.loads(l) for l in tree_path.read_text(encoding="utf-8").splitlines() if l.strip()]
                all_scored[f"tree/{source}"] = score_questions(recs, questions, source, "tree")
                print(f"[poc] rescored tree from {tree_path.name} ({len(recs)} 题)", file=sys.stderr)
            else:
                prev = out_dir / "poc_results.json"
                key = f"tree/{source}"
                if prev.exists() and key in json.loads(prev.read_text(encoding="utf-8")).get("detail", {}):
                    all_scored[key] = json.loads(prev.read_text(encoding="utf-8"))["detail"][key]
                    print(f"[poc] reuse tree detail: {key}", file=sys.stderr)
        prev = out_dir / "poc_results.json"
        if prev.exists():
            try:
                prior_tree_telemetry = json.loads(prev.read_text(encoding="utf-8")).get("tree_telemetry")
            except Exception:
                pass
    for source in sources:
        ws = config = collection = None
        extra_env = {"SIRCHMUNK_API_TOKEN": sirchmunk_env.get("SIRCHMUNK_API_TOKEN", ""),
                     "SIRCHMUNK_BASE_URL": sirchmunk_env.get("SIRCHMUNK_BASE_URL", "")}
        if source == "md":
            # tree 候选 MD 需在默认库可检索（hybrid / plan_a 基线口径）：
            # 按正常摄取路径补齐（SHA256 幂等，跳过已摄取）。
            for rel in args.md_paths:
                subprocess.run([args.main_python, str(REPO_ROOT / "scripts" / "ingest.py"),
                                "--path", str(REPO_ROOT / rel), "--collection", "default"],
                               check=False, cwd=str(REPO_ROOT))
        if source == "pdf" and pdf_path.exists():
            ws, config, collection = prepare_pdf_workspace(out_dir, pdf_path, sirchmunk_env)
            extra_env["RAG_PROJECT_ROOT"] = str(ws)
            subprocess.run([args.main_python, str(REPO_ROOT / "scripts" / "ingest.py"),
                            "--path", str(ws / "data" / "documents" / pdf_path.name),
                            "--collection", collection, "--config", config],
                           check=False, cwd=str(REPO_ROOT), env={**os.environ, **extra_env})

        for route in routes:
            if route == "tree":
                recs = []
                for q in questions_list:
                    if q["source"] != source:
                        continue
                    started = time.perf_counter()
                    try:
                        cand_paths = args.md_paths if source == "md" else [str(pdf_path)]
                        abs_paths = [str(REPO_ROOT / p) for p in cand_paths]
                        evidences = searcher.search(q["question"], abs_paths)
                        recs.append({"id": q["id"], "route": "tree", "ok": True,
                                     "evidences": evidences,
                                     "meta": {"elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                                              "total_llm_tokens": searcher.last_tokens,
                                              "llm_call_count": searcher.last_calls}})
                    except Exception as exc:
                        recs.append({"id": q["id"], "route": "tree", "ok": False,
                                     "error": f"{type(exc).__name__}: {exc}"})
                tree_records_path = out_dir / f"tree_{source}.jsonl"
                tree_records_path.write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                    encoding="utf-8")
            else:
                try:
                    recs = run_main_route(args.main_python, route, source, args.questions,
                                          out_dir, config, collection, extra_env,
                                          pdf_path=str(pdf_path) if source == "pdf" else None)
                except Exception as exc:
                    print(f"[poc] route {route}/{source} failed: {exc}", file=sys.stderr)
                    continue
            # 目标文件全文归一化（内容等价口径用；每 source 一份）
            target_text_norm = None
            if source == "md":
                parts = []
                for rel in args.md_paths:
                    f = REPO_ROOT / rel
                    if f.exists():
                        parts.append(norm_text(f.read_text(encoding="utf-8")))
                target_text_norm = "\n".join(parts)
            all_scored[f"{route}/{source}"] = score_questions(
                recs, questions, source, route, target_text_norm=target_text_norm)

    # ---- 汇总报告 ----
    expected = {f"{route}/{source}" for route in routes for source in sources}
    if "tree" not in routes:
        expected |= {f"tree/{source}" for source in sources}
    prev = out_dir / "poc_results.json"
    if prev.exists():
        try:
            prior = json.loads(prev.read_text(encoding="utf-8"))
            for key, rows in prior.get("detail", {}).items():
                if key not in expected and key not in all_scored:
                    all_scored[key] = rows
                    print(f"[poc] keep prior detail: {key}", file=sys.stderr)
        except Exception as exc:
            print(f"[poc] prior merge failed: {exc}", file=sys.stderr)
    summary = {key: summarize(v) for key, v in all_scored.items()}
    # tree_telemetry 按路由分键保存与合并（部分重跑不覆盖其他 key）
    new_tree_tel = {f"tree/{source}": {"llm_calls": searcher.llm_calls,
                                       "tokens": searcher.tokens,
                                       **searcher.telemetry}
                    for source in sources if f"tree/{source}" in all_scored}
    tree_telemetry: dict = {}
    if isinstance(prior_tree_telemetry, dict) and prior_tree_telemetry:
        if all(k.startswith("tree/") for k in prior_tree_telemetry):
            tree_telemetry.update(prior_tree_telemetry)
        else:
            tree_telemetry["tree/legacy"] = prior_tree_telemetry
    tree_telemetry.update(new_tree_tel)
    report = {"summary": summary, "build_cost": build_rows, "detail": all_scored,
              "moderation_skips": moderation_skip_count(),
              "metrics_note": {"top_k": TOP_K,
                               "hit": "strict-same-file overlap on top-k",
                               "hit_equiv": "cross-file evidence text contained in target file (normalized)",
                               "locatable": "plan_a=offline annotate; hybrid/agentic_deep=product output only; tree=tree fields",
                               "section_recall": "covered targets / total targets (synthesize multi-target)",
                               "precision": "target-overlapping evidences / top-k evidences"},
              "tree_telemetry": tree_telemetry}
    (out_dir / "poc_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    print("\n=== PI0 四路对照汇总（0.5 口径） ===")
    print(f"{'route/source':<24}{'n':>4}{'hit':>7}{'hitEq':>7}{'sRec':>7}{'prec':>7}"
          f"{'locat':>7}{'p50(ms)':>9}{'p95(ms)':>9}{'tokens':>9}")
    for key, s in summary.items():
        print(f"{key:<24}{s['n']:>4}{str(s['hit']):>7}{str(s['hit_equiv']):>7}"
              f"{str(s['section_recall']):>7}{str(s['precision']):>7}{str(s['locatable']):>7}"
              f"{s['p50_ms']:>9}{s['p95_ms']:>9}{s['mean_tokens']:>9.1f}")
    print(f"\nreport -> {out_dir / 'poc_results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
