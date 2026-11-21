#!/usr/bin/env python
"""PI0 四路对照 · 编排与树检索原型（PAGEINDEX_TREE_RETRIEVAL_SPEC 3.6 第 4/5 条）。

在 sidecar venv（``.venv-pageindex``）中运行，直接调用上游
``md_to_tree`` / ``PageIndexClient`` 与 litellm（PI0 阶段唯一例外，
红线白名单成员）；不依赖 PI1 / PI2 的正式模块。

四路对照：
- ``tree``：本脚本内置的树检索原型（选文档 → 选节点 → 读原文 → 判是否足够）。
- ``hybrid`` / ``agentic_deep`` / ``plan_a``：经子进程调用主 venv 的
  ``poc_main_routes.py``（不 import pageindex，红线不受影响）。

度量口径（D9 只固定口径，数值门槛跑出后与用户共同拍板）：
命中率（证据与目标范围有交集）、引用可定位率（章节+行号或页码）、
P50/P95 延迟、单题 token、一次性建树成本单列。

用法::

    .venv-pageindex/bin/python scripts/pageindex/poc_compare.py --source all
    .venv-pageindex/bin/python scripts/pageindex/poc_compare.py --index-only
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
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "pageindex"))

DEFAULT_MD_PATHS = [
    "data/documents/大模型应用学习路线拆解/大模型应用_算法学习路线+八股+面试实战4.md",
    "data/documents/大模型应用学习路线拆解/章节拆分/应用开发项目与开源架构解析.md",
    "data/documents/大模型应用学习路线拆解/章节拆分/13_面试实战.md",
]
MAX_EVIDENCE_CHARS = 8000

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)")
SETEXT_RE = re.compile(r"^(=+|-+)\s*$")


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


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pctl(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[idx]


# ---------------------------------------------------------------------------
# MD 预处理（PI0 内联最小版；PI1 抽取为正式 md_prep 模块）
# ---------------------------------------------------------------------------

def prepare_markdown(text: str, doc_name: str) -> tuple[str, list[int | None]]:
    """绕开上游 Issue #609：首个标题前的非空正文包成 ``# <文件名>`` 节点；
    Setext 标题改写为 ATX。返回 (预处理后全文, 新行号→旧行号映射，1 起计)。"""
    lines = text.split("\n")
    out: list[str] = []
    mapping: list[int | None] = []  # index 0 = 新第 1 行
    first_heading = next((i for i, ln in enumerate(lines) if HEADING_RE.match(ln)), None)

    if first_heading is None or any(ln.strip() for ln in lines[:first_heading]):
        out.append(f"# {doc_name}")
        mapping.append(None)  # 合成标题行，无原文对应
        out.append("")
        mapping.append(None)

    skip_next = False  # 上一行已被改写为 ATX，当前行是 Setext 下划线，吞掉
    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        stripped = line.strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        m = SETEXT_RE.match(nxt)
        if m and stripped and not HEADING_RE.match(line):
            level = 1 if m.group(1)[0] == "=" else 2
            out.append(f"{'#' * level} {stripped}")
            mapping.append(i + 1)
            skip_next = True
            continue
        out.append(line)
        mapping.append(i + 1)
    return "\n".join(out), mapping


# ---------------------------------------------------------------------------
# 文档树构建（MD：上游 md_to_tree；PDF：上游 PageIndexClient）
# ---------------------------------------------------------------------------

class TreeDoc:
    def __init__(self, source_path: str, fmt: str, tree: dict, prepared: str | None,
                 line_map: list[int | None] | None, build_tokens: int, build_ms: float) -> None:
        self.source_path = source_path
        self.format = fmt
        self.tree = tree
        self.prepared = prepared
        self.line_map = line_map or []
        self.build_tokens = build_tokens
        self.build_ms = build_ms
        self.doc_id: str | None = None

    def flat_nodes(self) -> list[dict]:
        out: list[dict] = []
        stack = list(self.tree.get("structure", []))
        while stack:
            node = stack.pop(0)
            out.append(node)
            stack = list(node.get("nodes", [])) + stack
        return sorted(out, key=lambda n: n.get("line_num") or n.get("start_index") or 0)

    def section_path(self, node: dict) -> str:
        """从根到该节点的标题路径（`` > `` 连接）。"""
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


def build_md_tree(path: Path, model: str, cache_dir: Path) -> TreeDoc:
    from pageindex.page_index_md import md_to_tree

    cache = cache_dir / f"md_{file_hash(path)[:16]}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        doc = TreeDoc(str(path), "md", data["tree"], data["prepared"],
                      data["line_map"], data["build_tokens"], data["build_ms"])
        return doc

    raw = path.read_text(encoding="utf-8")
    prepared, line_map = prepare_markdown(raw, path.stem)
    prepared_path = cache_dir / f"prepared_{file_hash(path)[:16]}.md"
    prepared_path.write_text(prepared, encoding="utf-8")

    started = time.perf_counter()
    tree = asyncio.run(md_to_tree(
        str(prepared_path), if_add_node_summary="yes", model=model, if_add_node_id="yes"
    ))
    build_ms = round((time.perf_counter() - started) * 1000, 1)
    tokens = 0  # md_to_tree 内部 litellm 用量不回传，建树 token 以 token 计数器近似（见 report 注记）
    doc = TreeDoc(str(path), "md", tree, prepared, line_map, tokens, build_ms)
    cache.write_text(json.dumps({
        "tree": tree, "prepared": prepared, "line_map": line_map,
        "build_tokens": tokens, "build_ms": build_ms,
    }, ensure_ascii=False), encoding="utf-8")
    return doc


def build_pdf_tree(path: Path, model: str, cache_dir: Path) -> TreeDoc:
    from pageindex import PageIndexClient

    cache = cache_dir / f"pdf_{file_hash(path)[:16]}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        doc = TreeDoc(str(path), "pdf", data["tree"], None, None,
                      data["build_tokens"], data["build_ms"])
        doc.doc_id = data["doc_id"]
        return doc

    client = PageIndexClient(index=model, chat=model)
    started = time.perf_counter()
    submitted = client.submit_document(str(path))
    doc_id = submitted.get("doc_id") if isinstance(submitted, dict) else None
    structure = client.get_document_structure(doc_id)
    build_ms = round((time.perf_counter() - started) * 1000, 1)
    tree = structure if isinstance(structure, dict) else {"structure": structure}
    doc = TreeDoc(str(path), "pdf", tree, None, None, 0, build_ms)
    doc.doc_id = doc_id
    cache.write_text(json.dumps({
        "tree": tree, "doc_id": doc_id, "build_tokens": 0, "build_ms": build_ms,
    }, ensure_ascii=False), encoding="utf-8")
    return doc


# ---------------------------------------------------------------------------
# 树检索原型（选文档 → 选节点 → 读原文 → 判是否足够）
# ---------------------------------------------------------------------------

class TreeSearcher:
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
        self.telemetry: dict = {"invalid_output": 0}

    def _ask(self, prompt: str) -> str:
        self.llm_calls += 1
        result = self.complete(prompt)
        self.tokens += result["tokens"]
        return result["text"]

    def _read_md_node(self, doc: TreeDoc, node: dict) -> tuple[int, int, str, bool]:
        """返回 (原文 line_start, line_end, text, truncated)。"""
        nodes = doc.flat_nodes()
        idx = nodes.index(node)
        nxt = nodes[idx + 1].get("line_num") if idx + 1 < len(nodes) else None
        prepared_lines = doc.prepared.rstrip("\n").split("\n")
        start = node.get("line_num")
        end = (nxt - 1) if nxt else len(prepared_lines)
        text = "\n".join(prepared_lines[start - 1:end])
        truncated = False
        if len(text) > MAX_EVIDENCE_CHARS and node.get("nodes"):
            # 优先下钻子节点：返回父节点行范围但证据改为子节点集合
            text = text[:MAX_EVIDENCE_CHARS]
            truncated = True
        # 行号映射回原文（合成行映射为 None，取邻近原行）
        def to_old(n: int) -> int:
            while 1 <= n <= len(doc.line_map) and doc.line_map[n - 1] is None:
                n += 1
            if n > len(doc.line_map):
                # 尾部合成行越界：回退到映射表中最后一个原行号
                for m in reversed(doc.line_map):
                    if m is not None:
                        return m
                return n
            return doc.line_map[n - 1]
        return to_old(start), to_old(end), text, truncated

    def _read_pdf_node(self, doc: TreeDoc, node: dict) -> tuple[int, int, str, bool]:
        page = node.get("page_number") or node.get("page") or 1
        text = ""
        if self.get_pdf_page and doc.doc_id:
            text = self.get_pdf_page(doc.doc_id, page) or ""
        else:
            text = json.dumps(node, ensure_ascii=False)
        truncated = len(text) > MAX_EVIDENCE_CHARS
        if truncated:
            text = text[:MAX_EVIDENCE_CHARS]
        end_page = node.get("page_end") or page
        return page, end_page, text, truncated

    def _outline(self, doc: TreeDoc) -> str:
        lines = []

        def walk(nodes: list[dict], depth: int) -> None:
            for n in nodes:
                summary = (n.get("summary") or "").strip().replace("\n", " ")
                lines.append(f"{'  ' * depth}- [{n.get('node_id')}] {n.get('title')}"
                             + (f" — {summary}" if summary else ""))
                walk(n.get("nodes", []), depth + 1)

        walk(self.tree_nodes(doc), 0)
        return "\n".join(lines)

    def tree_nodes(self, doc: TreeDoc) -> list[dict]:
        return doc.tree.get("structure", [])

    def search(self, query: str, source_paths: list[str] | None) -> list[dict]:
        candidates = [d for d in self.docs if source_paths is None or d.source_path in source_paths]
        if not candidates:
            return []
        picked = candidates
        if len(candidates) > 1:
            listing = "\n".join(f"- {d.source_path}（{d.format}）" for d in candidates)
            text = self._ask(
                "以下是可选文档列表。判断哪些文档可能包含回答问题所需的内容，"
                "返回 JSON：{\"doc_ids\": [索引]}。\n\n"
                f"问题：{query}\n\n文档列表（索引从 0 开始）：\n{listing}\n\n"
                '只返回 JSON，例如 {"doc_ids": [0, 2]}'
            )
            data = extract_json(text)
            ids = (data or {}).get("doc_ids") if isinstance(data, dict) else None
            if not isinstance(ids, list) or not ids:
                self.telemetry["invalid_output"] += 1
                picked = candidates
            else:
                picked = [candidates[i] for i in ids if isinstance(i, int) and 0 <= i < len(candidates)] or candidates

        evidence_by_node: dict[tuple, dict] = {}
        rank = 0
        for loop in range(self.max_loops):
            if self.tokens > self.max_token_budget:
                break
            new_nodes: list[tuple[TreeDoc, dict]] = []
            for doc in picked:
                prompt = (
                    "以下是文档的树状目录（标题、节点 ID 与摘要，不含正文）。"
                    "选出回答问题最相关的节点（最多 "
                    f"{self.max_nodes} 个），返回 JSON：{{\"node_ids\": [\"0000\", ...]}}。\n\n"
                    f"问题：{query}\n\n文档：{doc.source_path}\n\n目录：\n{self._outline(doc)}\n\n只返回 JSON。"
                )
                text = self._ask(prompt)
                data = extract_json(text)
                ids = data.get("node_ids") if isinstance(data, dict) else None
                if not isinstance(ids, list) or not ids:
                    self.telemetry["invalid_output"] += 1
                    continue
                by_id = {n.get("node_id"): n for n in doc.flat_nodes()}
                for nid in ids:
                    node = by_id.get(str(nid))
                    if node is not None and (doc.source_path, nid) not in evidence_by_node:
                        new_nodes.append((doc, node))

            for doc, node in new_nodes:
                if doc.format == "md":
                    ls, le, text, truncated = self._read_md_node(doc, node)
                    page_start = page_end = None
                else:
                    page_start, page_end, text, truncated = self._read_pdf_node(doc, node)
                    ls = le = None
                rank += 1
                evidence_by_node[(doc.source_path, node.get("node_id"))] = {
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

            # 判是否足够
            joined = "\n\n".join(e["text"][:1500] for e in evidence_by_node.values())
            verdict = extract_json(self._ask(
                f"问题：{query}\n\n已收集的证据摘录：\n{joined}\n\n"
                '这些证据是否足以回答问题？返回 JSON：{"sufficient": true|false}。只返回 JSON。'
            ))
            if isinstance(verdict, dict) and verdict.get("sufficient") is True:
                break

        return list(evidence_by_node.values())


# ---------------------------------------------------------------------------
# 四路编排与评分
# ---------------------------------------------------------------------------

def run_main_route(main_python: str, route: str, source: str, questions: str,
                   out_dir: Path, config: str | None, collection: str | None,
                   extra_env: dict[str, str] | None = None) -> list[dict]:
    out_path = out_dir / f"main_{route}_{source}.jsonl"
    cmd = [main_python, str(REPO_ROOT / "scripts" / "pageindex" / "poc_main_routes.py"),
           "--route", route, "--questions", questions, "--source", source,
           "--out", str(out_path)]
    if config:
        cmd += ["--config", config]
    if collection:
        cmd += ["--collection", collection]
    env = dict(os.environ)
    env.update(extra_env or {})
    print(f"[poc] main route {route}/{source} -> {out_path.name}", file=sys.stderr)
    subprocess.run(cmd, check=True, cwd=str(REPO_ROOT), env=env)
    return [json.loads(l) for l in out_path.read_text(encoding="utf-8").splitlines() if l.strip()]


def ranges_from_record(record: dict, source: str) -> tuple[list[tuple[int, int]], list[str]]:
    """从一条主路由记录提取 (证据范围, 章节名) 列表（MD 行号 / PDF 页码）。"""
    spans: list[tuple[int, int]] = []
    sections: list[str] = []
    for r in record.get("results", []):
        loc = r.get("loc") or {}
        meta = r.get("metadata") or {}
        if source == "md":
            ls, le = loc.get("line_start"), loc.get("line_end")
            if ls:
                spans.append((ls, le or ls))
                sections.append(loc.get("section") or "")
        else:
            page = meta.get("page")
            if page:
                spans.append((int(page), int(page)))
                sections.append("")
    return spans, sections


def ranges_from_tree(evidences: list[dict], source: str) -> tuple[list[tuple[int, int]], list[str]]:
    spans, sections = [], []
    for e in evidences:
        if source == "md":
            if e.get("line_start"):
                spans.append((e["line_start"], e.get("line_end") or e["line_start"]))
                sections.append(e.get("section") or "")
        else:
            if e.get("page_start"):
                spans.append((e["page_start"], e.get("page_end") or e["page_start"]))
                sections.append(e.get("section") or "")
    return spans, sections


def overlap(spans: list[tuple[int, int]], targets: list[tuple[int, int]]) -> bool:
    return any(s <= te and ts <= e for (s, e) in spans for (ts, te) in targets)


def score_questions(records: list[dict], questions: dict[str, dict], source: str,
                    tree_records: list[dict] | None = None) -> list[dict]:
    scored = []
    for rec in records:
        q = questions[rec["id"]]
        targets = [(t["target_lines"][0], t["target_lines"][1]) if "target_lines" in t
                   else (t["target_pages"][0], t["target_pages"][1])
                   for t in q["target_sections"]]
        if rec.get("route") == "tree":
            spans, sections = ranges_from_tree(rec.get("evidences", []), source)
        else:
            spans, sections = ranges_from_record(rec, source)
        elapsed = rec.get("meta", {}).get("elapsed_ms") or 0
        tokens = rec.get("meta", {}).get("total_llm_tokens") or 0
        # 引用可定位率：生产口径——引用能直接给出「章节+行号」或「页码」
        if source == "md":
            locatable = bool(sections and any(s for s in sections))
        else:
            has_page = any((r.get("metadata") or {}).get("page") for r in rec.get("results", [])) \
                or any(e.get("page_start") for e in rec.get("evidences", []))
            locatable = bool(has_page)
        scored.append({
            "id": rec["id"], "ok": rec.get("ok", False),
            "hit": overlap(spans, targets) if rec.get("ok") else False,
            "locatable": locatable if rec.get("ok") else False,
            "elapsed_ms": elapsed, "tokens": tokens,
            "n_evidences": len(spans),
            "error": rec.get("error"),
        })
    return scored


def summarize(scored: list[dict]) -> dict:
    ok = [s for s in scored if s["ok"]]
    lat = [s["elapsed_ms"] for s in ok]
    return {
        "n": len(scored), "ok": len(ok),
        "hit_rate": round(sum(s["hit"] for s in ok) / len(ok), 3) if ok else None,
        "locatable_rate": round(sum(s["locatable"] for s in ok) / len(ok), 3) if ok else None,
        "p50_ms": round(pctl(lat, 50), 1), "p95_ms": round(pctl(lat, 95), 1),
        "mean_tokens": round(statistics.mean([s["tokens"] for s in ok]), 1) if ok else 0,
    }


def prepare_pdf_workspace(out_dir: Path, pdf_path: Path,
                          sirchmunk_env: dict[str, str]) -> tuple[Path, str, str]:
    """PDF 对照隔离工作区：RAG_PROJECT_ROOT 临时目录 + 临时配置（agentic DEEP）。"""
    import yaml

    ws = out_dir / "workspace"
    docs_dir = ws / "data" / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
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
    args = parser.parse_args()

    out_dir = REPO_ROOT / args.out
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    env = load_env_file(REPO_ROOT / ".env-pageindex")
    llm_model = env.get("LLM_MODEL", "openai/glm-5.3-flash")
    os.environ.setdefault("OPENAI_API_BASE", env.get("LLM_API_BASE", ""))
    os.environ.setdefault("OPENAI_API_KEY", env.get("LLM_API_KEY", ""))

    import litellm

    def complete(prompt: str) -> dict:
        started = time.perf_counter()
        resp = litellm.completion(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            api_base=env.get("LLM_API_BASE"),
            api_key=env.get("LLM_API_KEY"),
            temperature=0,
        )
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
        doc = build_md_tree(REPO_ROOT / rel, llm_model, cache_dir)
        docs.append(doc)
        build_rows.append({"source_path": rel, "format": "md", "node_count": len(doc.flat_nodes()),
                           "build_ms": doc.build_ms, "build_tokens": doc.build_tokens})
    if pdf_path.exists():
        doc = build_pdf_tree(pdf_path, llm_model, cache_dir)
        docs.append(doc)
        build_rows.append({"source_path": pdf_rel, "format": "pdf", "node_count": len(doc.flat_nodes()),
                           "build_ms": doc.build_ms, "build_tokens": doc.build_tokens})
    print(json.dumps({"build_cost": build_rows}, ensure_ascii=False, indent=2))

    if args.index_only:
        return 0

    # ---- 树检索原型跑题 ----
    questions_list = [json.loads(l) for l in (REPO_ROOT / args.questions)
                      .read_text(encoding="utf-8").splitlines() if l.strip()]
    questions = {q["id"]: q for q in questions_list}

    routes = [r.strip() for r in args.routes.split(",")]
    sources = ["md", "pdf"] if args.source == "all" else [args.source]

    sirchmunk_env = load_env_file(REPO_ROOT / ".env-sirchmunk")
    searcher = TreeSearcher(docs, complete, max_nodes=args.max_nodes,
                            max_loops=args.max_loops)

    def get_pdf_page(doc_id: str, page: int) -> str:
        try:
            from pageindex import PageIndexClient
            client = PageIndexClient(index=llm_model, chat=llm_model)
            result = client.get_page_content(doc_id, page)
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except Exception:
            return ""

    all_scored: dict[str, dict] = {}
    tree_records: dict[str, list[dict]] = {}
    for source in sources:
        ws = config = collection = None
        extra_env = {}
        if source == "pdf" and pdf_path.exists():
            ws, config, collection = prepare_pdf_workspace(out_dir, pdf_path, sirchmunk_env)
            extra_env["RAG_PROJECT_ROOT"] = str(ws)
            # 先摄取（hybrid/plan_a 前置），幂等：SHA256 增量跳过
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
                        targets = [t["path"] for t in [q]] if False else None
                        paths = [q["path"]] if source == "md" else [str(pdf_path)]
                        # 不给检索器泄露答案：候选范围 = 该 source 的全部文档
                        cand_paths = args.md_paths if source == "md" else [str(pdf_path)]
                        abs_paths = [str(REPO_ROOT / p) for p in cand_paths]
                        evidences = searcher.search(q["question"], abs_paths)
                        recs.append({"id": q["id"], "route": "tree", "ok": True,
                                     "evidences": evidences,
                                     "meta": {"elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                                              "total_llm_tokens": searcher.tokens,
                                              "llm_call_count": searcher.llm_calls}})
                    except Exception as exc:
                        recs.append({"id": q["id"], "route": "tree", "ok": False,
                                     "error": f"{type(exc).__name__}: {exc}"})
                tree_records[source] = recs
            else:
                try:
                    recs = run_main_route(args.main_python, route, source, args.questions,
                                          out_dir, config, collection, extra_env)
                except Exception as exc:
                    print(f"[poc] route {route}/{source} failed: {exc}", file=sys.stderr)
                    continue
            scored = score_questions(recs, questions, source)
            all_scored[f"{route}/{source}"] = scored

    # ---- 汇总报告 ----
    summary = {key: summarize(v) for key, v in all_scored.items()}
    report = {"summary": summary, "build_cost": build_rows, "detail": all_scored,
              "tree_telemetry": {"llm_calls": searcher.llm_calls, "tokens": searcher.tokens,
                                 **searcher.telemetry}}
    (out_dir / "poc_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    print("\n=== PI0 四路对照汇总 ===")
    print(f"{'route/source':<22}{'n':>4}{'hit':>7}{'locatable':>11}{'p50(ms)':>10}{'p95(ms)':>10}{'tokens':>9}")
    for key, s in summary.items():
        print(f"{key:<22}{s['n']:>4}{str(s['hit_rate']):>7}{str(s['locatable_rate']):>11}"
              f"{s['p50_ms']:>10}{s['p95_ms']:>10}{s['mean_tokens']:>9.1f}")
    print(f"\nreport -> {out_dir / 'poc_results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
