#!/usr/bin/env python
"""PI0 四路对照 · 主 venv 路由 runner（PAGEINDEX_TREE_RETRIEVAL_SPEC 3.6 第 4/5 条）。

由 ``poc_compare.py``（sidecar venv）以主项目 venv 的 python 子进程调用，
不 import pageindex / litellm（红线）。负责三条不需要 PageIndex 的路由：

- ``hybrid``：主项目 HybridSearch + Rerank（生产口径，query.py 同构）。
- ``agentic_deep``：Sirchmunk 第三路 DEEP（需 sidecar 已启动 + 配置
  ``agentic_search.mode=DEEP`` / ``allow_deep=true``）。
- ``plan_a``：方案 (a) 离线近似 —— 在 hybrid 结果上把 chunk 文本回定位到
  源文件行号并推导标题路径（不改摄取代码，PI0 专用口径）。

输入：``tests/fixtures/pageindex/poc_questions.jsonl``（PI0.2 定稿格式）。
输出：JSONL（每行一个题目记录），供 poc_compare.py 汇总评分。

用法::

    .venv/bin/python scripts/pageindex/poc_main_routes.py \\
        --route hybrid --source md --out /tmp/hybrid_md.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.query_engine import HybridSearch, Reranker  # noqa: E402
from core.settings import load_settings  # noqa: E402
from libs.agentic_search.adapter import build_agentic_retriever  # noqa: E402

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class HeadingIndex:
    """单篇 Markdown 的标题索引：行号 → 标题路径与节行范围（离线近似）。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.headings: list[tuple[int, int, str]] = []  # (line, level, title)
        self._line_probe: dict[str, int] = {}
        self._total_lines = 0
        with open(path, encoding="utf-8") as fh:
            for no, line in enumerate(fh, 1):
                self._total_lines = no
                m = HEADING_RE.match(line.rstrip("\n"))
                if m:
                    self.headings.append((no, len(m.group(1)), m.group(2).strip()))
                probe = _norm(line)
                if len(probe) >= 12 and probe not in self._line_probe:
                    self._line_probe[probe] = no

    def _path_at(self, line: int) -> list[str]:
        """该行所处的标题路径（ deepest heading 链，行号 ≤ line 的最深链）。"""
        stack: list[tuple[int, str]] = []
        for no, level, title in self.headings:
            if no > line:
                break
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        return [t for _, t in stack]

    def section_range(self, line: int) -> tuple[list[str], int, int] | None:
        """返回 (标题路径, 节起始行, 节结束行)。``line`` 无标题覆盖时返回 None。"""
        if not self.headings:
            return None
        path = self._path_at(line)
        if not path:
            return None
        start = max(no for no, _, _ in self.headings if no <= line)
        later = [no for no, _, _ in self.headings if no > start]
        # 节结束 = 下一个不深于起始标题级别的标题行 - 1；近似取下一标题行 - 1
        end = (min(later) - 1) if later else self._total_lines
        return path, start, end

    def locate(self, chunk_text: str) -> tuple[list[str], int, int] | None:
        """把 chunk 文本回定位到源文件行（离线近似：取首行/最长行/末行试探）。"""
        cands = [_norm(ln) for ln in chunk_text.splitlines() if len(_norm(ln)) >= 12]
        if not cands:
            return None
        for probe in (cands[0], max(cands, key=len), cands[-1]):
            no = self._line_probe.get(probe)
            if no is not None:
                return self.section_range(no)
        # 次级试探：文件行包含于 chunk 探针（截断块首尾不齐时）
        for probe in (cands[0], max(cands, key=len)):
            for probe_line in probe.split("。"):
                if len(probe_line) < 12:
                    continue
                for norm, no in self._line_probe.items():
                    if norm in probe_line:
                        return self.section_range(no)
        return None

    def __enter__(self) -> "HeadingIndex":
        with open(self.path, encoding="utf-8") as fh:
            self._total_lines = sum(1 for _ in fh)
        return self

    def __exit__(self, *exc) -> None:
        pass


def load_questions(path: str, source: str) -> list[dict]:
    qs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                q = json.loads(line)
                if q.get("source") == source:
                    qs.append(q)
    return qs


def run_route(route: str, settings, query: str, collection: str | None) -> tuple[list, dict]:
    """执行一条路由，返回 (results, meta)。results 为 RetrievalResult.to_dict() 列表。"""
    started = time.perf_counter()
    meta: dict = {}
    if route in ("hybrid", "plan_a"):
        search = HybridSearch(settings)
        results = search.search(
            query, filters={"collection": collection} if collection else None
        )
        try:
            results = Reranker(settings).rerank(results, query)
        except Exception as exc:  # rerank 失败不阻断 POC，记录后用融合排名
            meta["rerank_error"] = str(exc)
    elif route == "agentic_deep":
        adapter = build_agentic_retriever(settings)
        results = adapter.retrieve(query, collection=collection)
        resp = adapter.last_response
        meta = {
            "llm_call_count": getattr(resp, "llm_call_count", None),
            "total_llm_tokens": getattr(resp, "total_llm_tokens", None),
            "elapsed_ms": getattr(resp, "elapsed_ms", None),
            "telemetry": getattr(resp, "telemetry", None),
            "mode": getattr(resp, "mode", None),
        }
    else:
        raise ValueError(f"unknown route: {route}")
    meta.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000, 3))
    meta["llm_call_count"] = meta.get("llm_call_count", 0)
    meta["total_llm_tokens"] = meta.get("total_llm_tokens", 0)
    return [r.to_dict() for r in results], meta


def annotate_md(results: list[dict], heading_cache: dict) -> list[dict]:
    """给 md 检索结果追加离线定位（line_start/line_end/section）供评分用。

    按 chunk 自身的 ``metadata.source_path`` 选标题索引（上一版错拿目标文件
    的索引定位全语料检回的 chunk，span 几乎全错）；各 chunk 在自己文件内
    定位，评分侧再按「证据文件 == 题目目标文件」过滤。
    """
    for r in results:
        src = (r.get("metadata") or {}).get("source_path") or ""
        loc = None
        if src and src.lower().endswith((".md", ".markdown")) and Path(src).exists():
            if src not in heading_cache:
                try:
                    heading_cache[src] = HeadingIndex(src)
                except Exception:
                    heading_cache[src] = None
            index = heading_cache.get(src)
            if index is not None:
                found = index.locate(r["text"])
                if found:
                    loc = {"source": src, "section": " > ".join(found[0]),
                           "line_start": found[1], "line_end": found[2]}
        r["loc"] = loc
    return results


class PageLocator:
    """单篇 PDF 的页码离线近似定位（方案 (a) 口径，规格 3.6 第 4 条）。

    pypdf 逐页抽文本建行探针（归一化后 ≥12 字符的行 → 首个出现页码），
    chunk 文本的探针行命中即得页码范围。摄取侧 markitdown 引擎无页边界
    （整块 Markdown），chunk 也不带 page metadata（chunker 无页追踪），
    故页码只能离线回定位。pypdf 与 markitdown 的换行/空格差异使部分行
    匹配不上——locatable 率如实反映该近似的质量。
    """

    def __init__(self, path: str) -> None:
        from pypdf import PdfReader

        reader = PdfReader(path)
        # 去全部空白后整页保存：pypdf 与 markitdown 的换行/空格差异大，
        # 行级全等几乎必失配，子串包含才是可用近似。
        self._pages: list[str] = [
            re.sub(r"\s+", "", page.extract_text() or "") for page in reader.pages
        ]

    def locate(self, chunk_text: str) -> tuple[int, int] | None:
        joined = re.sub(r"\s+", "", chunk_text)
        if len(joined) < 30:
            return None
        mid = len(joined) // 2
        probes = [seg for seg in (joined[:100], joined[mid:mid + 100], joined[-100:])
                  if len(seg) >= 30]
        pages_by_probe = [
            [no for no, ptext in enumerate(self._pages, 1) if probe in ptext]
            for probe in probes
        ]
        # 目录行 / 页眉会在多页重复出现：优先只统计低歧义探针（≤5 页）
        usable = [ps for ps in pages_by_probe if ps and len(ps) <= 5]
        pages = sorted({p for ps in (usable or [ps for ps in pages_by_probe if ps]) for p in ps})
        if not pages:
            return None
        return pages[0], pages[-1]


def annotate_pdf(results: list[dict], locator_cache: dict, pdf_path: str) -> list[dict]:
    """给 PDF 检索结果追加离线页码定位（page_start/page_end）供评分用。"""
    if pdf_path not in locator_cache:
        try:
            locator_cache[pdf_path] = PageLocator(pdf_path)
        except Exception:
            locator_cache[pdf_path] = None
    locator = locator_cache.get(pdf_path)
    for r in results:
        found = locator.locate(r["text"]) if locator is not None else None
        r["loc"] = (
            {"source": pdf_path, "page_start": found[0], "page_end": found[1]}
            if found
            else None
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", required=True, choices=["hybrid", "agentic_deep", "plan_a"])
    parser.add_argument("--questions", default="tests/fixtures/pageindex/poc_questions.jsonl")
    parser.add_argument("--source", required=True, choices=["md", "pdf"])
    parser.add_argument("--config", default=None, help="配置文件（PDF 对照用临时隔离配置）")
    parser.add_argument("--collection", default=None)
    parser.add_argument("--pdf-path", default=None,
                        help="PDF 源的原始文件路径（离线页码定位用）")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    settings = load_settings(args.config)
    questions = load_questions(args.questions, args.source)
    heading_cache: dict[str, HeadingIndex] = {}
    locator_cache: dict[str, PageLocator | None] = {}

    with open(args.out, "w", encoding="utf-8") as out:
        for q in questions:
            record: dict = {"id": q["id"], "route": args.route}
            try:
                collection = args.collection
                results, meta = run_route(args.route, settings, q["question"], collection)
                if args.source == "md":
                    results = annotate_md(results, heading_cache)
                elif args.source == "pdf" and args.pdf_path:
                    results = annotate_pdf(results, locator_cache, args.pdf_path)
                record.update({"ok": True, "results": results, "meta": meta})
            except Exception as exc:  # 单题失败不中断整批
                record.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            print(f"[{args.route}] {q['id']}: {'ok' if record['ok'] else record['error']}",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
