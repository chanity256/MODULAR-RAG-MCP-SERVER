"""树检索逐层下钻循环（PI1，纯标准库）。

从 PI0 原型（``poc_compare.py`` ``TreeSearcher``，PI0.6 复测 hit 0.64 的
形态）忠实移植为可注入的纯模块（规格 §3.2）：选文档 → 选节点 → 读原文
→ 判是否足够闭环。LLM 经注入的 ``complete(prompt) -> {"text", "tokens"}``，
原文读取经注入的 ``read_node(doc, node) -> NodeRead``（D12 回映射细节由
调用方实现：MD 证据 ``source_path`` 永为原 MD 路径，回定位失败
``line_*`` 为 ``None``）；超长节点下钻的近似行区间经 ``node_span`` 注入
（MD 接 ``HeadingIndex.locate``，PDF 传 ``None``）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

MAX_EVIDENCE_CHARS = 8000
PREFILTER_THRESHOLD = 25   # 单层候选节点超过该数先做压缩预筛
PREFILTER_KEEP = 15


@dataclass
class LoopDoc:
    """检索候选文档（树本体来自上游 DocStore 的 structure）。"""
    source_path: str
    format: str                 # "md" | "pdf"
    tree: dict
    doc_id: str | None = None
    title: str = ""


@dataclass
class NodeRead:
    """``read_node`` 的返回：单节点原文与位置（D8 / D12 字段口径）。"""
    text: str
    line_start: int | None = None
    line_end: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    truncated: bool = False
    how: str = "relocated"      # relocated | unlocated | page_fallback | pdf


@dataclass
class LoopResult:
    evidences: list[dict] = field(default_factory=list)
    llm_call_count: int = 0
    total_llm_tokens: int = 0
    elapsed_ms: float = 0.0
    telemetry: dict = field(default_factory=dict)


def extract_json(text: str | None) -> dict | list | None:
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


def ensure_node_ids(structure: list[dict]) -> list[dict]:
    """缺 ``node_id`` 的节点按 BFS 序补 ``"0000"`` 式 id（上游一般自带；
    兜底保证选节点 JSON 可引用）。返回原结构（就地补齐）。"""
    counter = 0
    stack = list(structure)
    while stack:
        node = stack.pop(0)
        if not node.get("node_id"):
            node["node_id"] = f"{counter:04d}"
        counter += 1
        stack = list(node.get("nodes", [])) + stack
    return structure


def flat_nodes(tree: dict) -> list[dict]:
    """全树扁平化（先根后子，按出现行序稳定排序）。"""
    out: list[dict] = []
    stack = list(tree.get("structure", []))
    while stack:
        node = stack.pop(0)
        out.append(node)
        stack = list(node.get("nodes", [])) + stack
    return sorted(out, key=lambda n: n.get("line_num") or n.get("start_index") or 0)


def section_titles_for(tree: dict, node: dict) -> list[str]:
    """从根到该节点的标题路径（`` > `` 连接前先取列表；D8 ``section``）。"""
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

    found = find(tree.get("structure", []), [])
    return [t for t in (found or []) if t]


def _estimate_tokens(prompt: str) -> int:
    return max(1, len(prompt) // 2)  # 中文粗估：~2 字符 / token


def search(query: str, docs: list[LoopDoc], *, complete,
           read_node, max_nodes: int = 5, max_loops: int = 3,
           max_token_budget: int = 32000, node_span: Callable | None = None,
           prefilter_threshold: int = PREFILTER_THRESHOLD,
           prefilter_keep: int = PREFILTER_KEEP,
           max_evidence_chars: int = MAX_EVIDENCE_CHARS,
           max_workers: int = 3) -> LoopResult:
    """逐层下钻检索（预算在每次 LLM 调用前检查：预估超剩余预算不发起调用）。

    ``read_node(doc, node) -> NodeRead``；``node_span(doc, node) ->
    (line_start, line_end) | None`` 供超长节点下钻判断（MD 传 heading 定位，
    PDF 传 ``None``）。证据按选中顺序 ``score = 1 / rank``。
    """
    started = time.perf_counter()
    candidates = list(docs)
    telemetry: dict = {}
    llm_calls = 0
    tokens = 0
    lock = threading.Lock()

    def tel(key: str, n: int = 1) -> None:
        with lock:
            telemetry[key] = telemetry.get(key, 0) + n

    def ask(prompt: str) -> str | None:
        """调用前预算检查：预估超剩余预算则不发起调用（返回 None）。"""
        nonlocal llm_calls, tokens
        estimate = _estimate_tokens(prompt)
        with lock:
            if tokens + estimate > max_token_budget:
                telemetry["budget_stopped"] = telemetry.get("budget_stopped", 0) + 1
                return None
        result = complete(prompt)
        with lock:
            llm_calls += 1
            tokens += int(result.get("tokens") or 0)
        return result.get("text")

    def ask_json(prompt: str) -> dict | None:
        """LLM 输出非法 JSON 重试一次，仍非法记 ``invalid_output`` 返回 None。"""
        for attempt in (1, 2):
            text = ask(prompt)
            data = extract_json(text)
            if isinstance(data, dict):
                return data
            if attempt == 1 and text is not None:
                continue  # 非法 JSON（非预算截停）重试一次
            break
        tel("invalid_output")
        return None

    # 1) 选文档（候选 > 1 时）
    picked = candidates
    if len(candidates) > 1:
        listing = "\n".join(f"- [{i}] {d.title or d.source_path}（{d.format}）"
                            for i, d in enumerate(candidates))
        data = ask_json(
            "以下是可选文档列表。判断哪些文档可能包含回答问题所需的内容，"
            "返回 JSON：{\"doc_ids\": [索引]}。\n\n"
            f"问题：{query}\n\n文档列表（索引从 0 开始）：\n{listing}\n\n"
            '只返回 JSON，例如 {"doc_ids": [0, 2]}'
        )
        ids = (data or {}).get("doc_ids") if isinstance(data, dict) else None
        if not isinstance(ids, list) or not ids:
            picked = candidates  # 选文档失败不终止：退回全候选
        else:
            picked = [candidates[i] for i in ids
                      if isinstance(i, int) and 0 <= i < len(candidates)] or candidates

    evidence_by_node: dict[tuple, dict] = {}
    state = {"rank": 0, "selected": 0, "truncated": False}

    def read_one(doc: LoopDoc, node: dict) -> dict | None:
        key = (doc.source_path, node.get("node_id"))
        if key in evidence_by_node:
            return None  # 同节点去重
        with lock:
            if state["selected"] >= max_nodes:
                return None  # 总输出节点硬截断：不再新增证据
        rd: NodeRead = read_node(doc, node)
        if doc.format == "md":
            if rd.how == "unlocated":
                tel("unlocated")
            elif rd.how == "page_fallback":
                tel("page_fallback")
        with lock:
            if state["selected"] >= max_nodes:
                return None
            state["selected"] += 1
            state["rank"] += 1
            state["truncated"] = state["truncated"] or rd.truncated
            rank = state["rank"]
        return {
            "source_path": doc.source_path,
            "doc_id": doc.doc_id,
            "node_id": node.get("node_id"),
            "section": " > ".join(section_titles_for(doc.tree, node)),
            "line_start": rd.line_start,
            "line_end": rd.line_end,
            "page_start": rd.page_start,
            "page_end": rd.page_end,
            "text": rd.text,
            "score": round(1 / rank, 4),
            "truncated": rd.truncated,
        }

    def select_for_doc(doc: LoopDoc, frontier: list[dict]
                       ) -> tuple[list[dict], list[dict], bool]:
        """单文档一轮：返回 (reads, next_frontier, progressed)。"""
        if not frontier:
            return [], [], False
        nodes = frontier
        if len(nodes) > prefilter_threshold:
            listing = "\n".join(f"- [{n.get('node_id')}] {n.get('title')}"
                                for n in nodes)
            data = ask_json(
                "下面是文档某一层级的候选节点列表（过多，请先压缩筛选）。\n"
                f"保留与问题可能相关的最多 {prefilter_keep} 个节点，返回 JSON："
                "{\"keep\": [\"0001\"]}。只返回 JSON。\n\n"
                f"问题：{query}\n\n候选节点：\n" + listing
            )
            keep = (data or {}).get("keep") if isinstance(data, dict) else None
            if isinstance(keep, list) and keep:
                by_id = {str(n.get("node_id")): n for n in nodes}
                nodes = [by_id[str(k)] for k in keep if str(k) in by_id] or nodes
                tel("prefilter_used")
        lines = []
        for n in nodes:
            summary = (n.get("summary") or n.get("node_summary")
                       or n.get("prefix_summary") or "").strip()
            if len(summary) > 120:
                summary = summary[:120] + "…"
            has_children = "＋" if n.get("nodes") else "·"
            lines.append(f"- [{n.get('node_id')}] {has_children} {n.get('title')}"
                         + (f" — {summary}" if summary else ""))
        data = ask_json(
            "下面是文档某一层级的节点列表（＋= 有子节点可展开，·= 叶节点）。\n"
            f"选出与回答问题相关的节点（最多 {max_nodes} 个）：要读原文的放进 "
            "\"read\"，要看其子节点的放进 \"expand\"（只能选带＋的）。\n"
            "返回 JSON：{\"read\": [\"0000\"], \"expand\": [\"0001\"]}。"
            "只返回 JSON。\n\n"
            f"问题：{query}\n文档：{doc.source_path}\n\n节点列表：\n" + "\n".join(lines)
        )
        read_ids = data.get("read") if isinstance(data, dict) else None
        expand_ids = data.get("expand") if isinstance(data, dict) else None
        if not isinstance(read_ids, list) and not isinstance(expand_ids, list):
            return [], [], False
        by_id = {str(n.get("node_id")): n for n in flat_nodes(doc.tree)}
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

    frontier: dict[str, list[dict]] = {}
    for d in picked:
        structure = ensure_node_ids(d.tree.get("structure", []))
        frontier[d.source_path] = list(structure)

    for _loop in range(max_loops):
        if all(not v for v in frontier.values()):
            break
        # 2) 逐层选节点（多文档并行）
        jobs = [(doc, frontier.get(doc.source_path) or []) for doc in picked
                if frontier.get(doc.source_path)]
        if len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as pool:
                futures = [pool.submit(select_for_doc, doc, nodes)
                           for doc, nodes in jobs]
                results = [f.result() for f in futures]
        else:
            results = [select_for_doc(doc, nodes) for doc, nodes in jobs]
        new_reads: list[tuple[LoopDoc, dict]] = []
        progressed = False
        for (doc, _), (reads, next_level, prog) in zip(jobs, results):
            frontier[doc.source_path] = next_level
            progressed = progressed or prog
            for node in reads:
                if state["selected"] >= max_nodes:
                    break
                new_reads.append((doc, node))

        # 3) 读原文（max_nodes 硬截断 + 超长下钻）
        for doc, node in list(new_reads):
            if node_span is not None and doc.format == "md":
                # 超长节点优先下钻子节点，不急读（原型 400 行阈值）
                span = node_span(doc, node)
                if span and (span[1] - span[0] + 1) > 400 and node.get("nodes"):
                    frontier.setdefault(doc.source_path, []).extend(
                        node.get("nodes") or [])
                    new_reads.remove((doc, node))
                    progressed = True
                    continue
            ev = read_one(doc, node)
            if ev is not None:
                evidence_by_node[(doc.source_path, node.get("node_id"))] = ev

        if not new_reads and not progressed:
            break
        if state["selected"] >= max_nodes:
            telemetry["max_nodes_capped"] = telemetry.get("max_nodes_capped", 0) + 1
            break

        # 4) 判是否足够（有新证据时）：足够则提前收敛
        if evidence_by_node:
            snippets = "\n\n".join(
                f"[{e['section']}] {e['text'][:500]}" for e in
                sorted(evidence_by_node.values(), key=lambda x: x["score"], reverse=True))
            data = ask_json(
                "基于已读到的证据，判断是否足够回答问题。返回 JSON："
                "{\"sufficient\": true/false, \"missing\": \"还缺什么\"}。只返回 JSON。\n\n"
                f"问题：{query}\n\n已有证据：\n{snippets[:6000]}"
            )
            if isinstance(data, dict) and data.get("sufficient") is True:
                telemetry["sufficient_stop"] = telemetry.get("sufficient_stop", 0) + 1
                break

    if state["truncated"]:
        telemetry["truncated_evidence"] = telemetry.get("truncated_evidence", 0) + 1
    evidences = sorted(evidence_by_node.values(),
                       key=lambda e: e["score"], reverse=True)
    evidences = evidences[:max_nodes]  # 防御：超预算截停时已选数可能超限
    return LoopResult(evidences=evidences, llm_call_count=llm_calls,
                      total_llm_tokens=tokens,
                      elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                      telemetry=telemetry)
