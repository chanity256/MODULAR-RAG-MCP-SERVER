"""PI1 tree_loop 单测（fake complete / fake read_node，零 LLM 零网络）。

覆盖规格 §4：选文档、选节点、同节点去重、max_nodes 硬截断、下钻、截断、
「读原文 → 判是否足够」闭环、max_loops / 预算截停（调用前预算检查）、
单层预筛、非法 JSON 重试与降级、证据回映射（source_path 永为原 MD 路径、
unlocated telemetry）；导入不触发 pageindex / litellm。
"""

import sys
from pathlib import Path

PAGEINDEX_DIR = Path(__file__).resolve().parents[2] / "scripts" / "pageindex"
sys.path.insert(0, str(PAGEINDEX_DIR))

from tree_loop import (LoopDoc, NodeRead, ensure_node_ids,  # noqa: E402
                       flat_nodes, search, section_titles_for)


def mknode(nid=None, title="", summary=None, children=None, line_num=None):
    node = {"title": title}
    if nid is not None:
        node["node_id"] = nid
    if summary:
        node["node_summary"] = summary
    if children:
        node["nodes"] = children
    if line_num is not None:
        node["line_num"] = line_num
    return node


def mkdoc(source="/docs/a.md", fmt="md", structure=None, doc_id="pi-1",
          title=""):
    return LoopDoc(source_path=source, format=fmt, doc_id=doc_id, title=title,
                   tree={"structure": structure or []})


class FakeComplete:
    """按 prompt 关键字路由的假 LLM；记录全部 prompt。"""

    def __init__(self, *, doc_ids=None, keep=None, read=None, expand=None,
                 sufficient=True, raw=None):
        self.doc_ids, self.keep, self.read, self.expand = doc_ids, keep, read, expand
        self.sufficient = sufficient
        self.raw = raw  # 非法 JSON 文本（优先于结构化响应）
        self.prompts: list[str] = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.raw is not None:
            return {"text": self.raw, "tokens": 100}
        if "可选文档列表" in prompt:
            import json
            return {"text": json.dumps({"doc_ids": self.doc_ids or []}), "tokens": 100}
        if "压缩筛选" in prompt:
            import json
            return {"text": json.dumps({"keep": self.keep or []}), "tokens": 100}
        if "节点列表" in prompt:
            import json
            return {"text": json.dumps({"read": self.read or [],
                                        "expand": self.expand or []}),
                    "tokens": 100}
        if "是否足够" in prompt:
            import json
            return {"text": json.dumps({"sufficient": self.sufficient,
                                        "missing": ""}), "tokens": 100}
        raise AssertionError(f"unrouted prompt: {prompt[:80]}")


def fake_read(doc, node, *, how="relocated", truncated=False):
    return NodeRead(text=f"text:{node.get('node_id')}", line_start=1, line_end=10,
                    truncated=truncated, how=how)


def test_single_doc_select_read_sufficient():
    doc = mkdoc(structure=[mknode("0000", "根", summary="根摘要",
                                  children=[mknode("0001", "子")])])
    llm = FakeComplete(read=["0000"], sufficient=True)
    result = search("q", [doc], complete=llm, read_node=fake_read)
    assert [e["node_id"] for e in result.evidences] == ["0000"]
    assert result.evidences[0]["score"] == 1.0
    assert result.evidences[0]["source_path"] == "/docs/a.md"  # 原 MD 路径（D12）
    assert result.evidences[0]["section"] == "根"
    assert result.evidences[0]["line_start"] == 1
    assert result.llm_call_count == 2          # 选节点 + 判足够
    assert result.total_llm_tokens == 200
    assert result.telemetry["sufficient_stop"] == 1


def test_section_path_nested():
    doc = mkdoc(structure=[mknode("0000", "根", children=[
        mknode("0001", "子节", children=[mknode("0002", "孙节")])])])
    llm = FakeComplete(read=["0002"], sufficient=True)
    result = search("q", [doc], complete=llm, read_node=fake_read)
    assert result.evidences[0]["section"] == "根 > 子节 > 孙节"


def test_multi_doc_selection_routes_to_picked():
    d0 = mkdoc(source="/docs/0.md", structure=[mknode("0000", "零")],
               title="零号文档")
    d1 = mkdoc(source="/docs/1.md", structure=[mknode("0000", "壹")],
               doc_id="pi-2", title="壹号文档")
    llm = FakeComplete(doc_ids=[1], read=["0000"], sufficient=True)
    result = search("q", [d0, d1], complete=llm, read_node=fake_read)
    assert "零号文档" in llm.prompts[0] and "壹号文档" in llm.prompts[0]  # 文档列表 prompt
    assert len(result.evidences) == 1
    assert result.evidences[0]["source_path"] == "/docs/1.md"


def test_multi_doc_selection_invalid_falls_back_to_all():
    d0 = mkdoc(source="/docs/0.md", structure=[mknode("0000", "零")])
    d1 = mkdoc(source="/docs/1.md", structure=[mknode("0000", "壹")])
    llm = FakeComplete(doc_ids=[], read=["0000"], sufficient=True)
    result = search("q", [d0, d1], complete=llm, read_node=fake_read)
    # 选文档 JSON 为空 → 退回全候选，两个文档各选一次
    assert {e["source_path"] for e in result.evidences} == {"/docs/0.md", "/docs/1.md"}


def test_same_node_dedup_across_loops():
    doc = mkdoc(structure=[mknode("0000", "根", children=[mknode("0001", "子")])])
    calls = {"n": 0}
    prompts: list[str] = []

    def complete(prompt):
        prompts.append(prompt)
        import json
        if "是否足够" in prompt:
            return {"text": json.dumps({"sufficient": False}), "tokens": 10}
        if "节点列表" in prompt:
            calls["n"] += 1
            # loop1 同时读并展开 0000；loop2 再次读 0000 → 应被去重
            payload = ({"read": ["0000"], "expand": ["0000"]} if calls["n"] == 1
                       else {"read": ["0000"]})
            return {"text": json.dumps(payload), "tokens": 10}
        raise AssertionError(f"unrouted prompt: {prompt[:80]}")

    result = search("q", [doc], complete=complete, read_node=fake_read,
                    max_loops=3)
    # 0000 只记一次证据；loop2 的重复读被去重
    assert [e["node_id"] for e in result.evidences] == ["0000"]
    assert len(result.evidences) == 1


def test_max_nodes_hard_cap():
    doc = mkdoc(structure=[mknode(str(i).zfill(4), f"n{i}") for i in range(5)])
    llm = FakeComplete(read=["0000", "0001", "0002", "0003", "0004"],
                       sufficient=False)
    result = search("q", [doc], complete=llm, read_node=fake_read, max_nodes=2)
    assert len(result.evidences) == 2
    assert result.telemetry["max_nodes_capped"] == 1


def test_budget_precheck_no_llm_call():
    doc = mkdoc(structure=[mknode("0000", "根")])
    llm = FakeComplete(read=["0000"], sufficient=True)

    def counting_read(doc, node):
        raise AssertionError("read_node must not be called")

    result = search("q", [doc], complete=llm, read_node=counting_read,
                    max_token_budget=1)
    assert result.llm_call_count == 0          # 预估超预算：调用前截停
    assert result.evidences == []
    assert result.telemetry["budget_stopped"] >= 1


def test_max_loops_without_sufficient():
    structure = [mknode("0000", "a", children=[mknode("0001", "b", children=[
        mknode("0002", "c")])])]
    doc = mkdoc(structure=structure)
    llm = FakeComplete(expand=["0000"], sufficient=False)
    # 每轮都 expand 当前层首节点：手动路由按 prompt 中节点列表首 id
    def complete(prompt):
        llm.prompts.append(prompt)
        import json
        if "是否足够" in prompt:
            return {"text": json.dumps({"sufficient": False}), "tokens": 10}
        import re
        m = re.search(r"\- \[(\d{4})\]", prompt)
        return {"text": json.dumps({"expand": [m.group(1)]}), "tokens": 10}

    result = search("q", [doc], complete=complete, read_node=fake_read,
                    max_loops=2)
    assert result.evidences == []
    assert "sufficient_stop" not in result.telemetry
    assert result.llm_call_count == 2  # 每轮一次选节点；无证据不判足够


def test_invalid_json_retry_once_then_valid():
    doc = mkdoc(structure=[mknode("0000", "根")])
    llm = FakeComplete(read=["0000"], sufficient=True)
    calls = {"n": 0}

    def complete(prompt):
        llm.prompts.append(prompt)
        import json
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "抱歉我不会 JSON", "tokens": 10}  # 第一次非法
        return FakeComplete.__call__(llm, prompt)

    result = search("q", [doc], complete=complete, read_node=fake_read)
    assert result.evidences and result.evidences[0]["node_id"] == "0000"
    assert "invalid_output" not in result.telemetry


def test_invalid_json_degrade_after_retry():
    doc = mkdoc(structure=[mknode("0000", "根")])
    llm = FakeComplete(raw="不是 JSON")
    result = search("q", [doc], complete=llm, read_node=fake_read)
    assert result.evidences == []
    assert result.telemetry["invalid_output"] == 1
    assert result.llm_call_count == 2          # 非法 JSON 重试一次


def test_prefilter_kicks_in_over_threshold():
    doc = mkdoc(structure=[mknode(str(i).zfill(4), f"节点{i}") for i in range(30)])
    keep = [str(i).zfill(4) for i in range(15)]
    llm = FakeComplete(keep=keep, read=[keep[0]], sufficient=True)
    result = search("q", [doc], complete=llm, read_node=fake_read)
    assert result.telemetry["prefilter_used"] == 1
    assert any("压缩筛选" in p for p in llm.prompts)
    assert result.evidences[0]["node_id"] == "0000"


def test_long_node_drills_down_instead_of_reading():
    child = mknode("0001", "子")
    doc = mkdoc(structure=[mknode("0000", "长章节", children=[child])])

    def node_span(d, node):
        return (1, 1000) if node.get("node_id") == "0000" else None

    def read_node(d, node):
        raise AssertionError("超长节点应先下钻而不是直接读")

    llm = FakeComplete(read=["0000"], sufficient=False)
    result = search("q", [doc], complete=llm, read_node=read_node,
                    node_span=node_span, max_loops=1)
    assert result.evidences == []
    assert result.llm_call_count == 1  # 仅选节点；下钻推进了一层


def test_short_span_reads_normally():
    doc = mkdoc(structure=[mknode("0000", "短节")])

    def node_span(d, node):
        return (1, 50)

    llm = FakeComplete(read=["0000"], sufficient=True)
    result = search("q", [doc], complete=llm, read_node=fake_read,
                    node_span=node_span)
    assert len(result.evidences) == 1


def test_unlocated_and_truncated_telemetry():
    doc = mkdoc(structure=[mknode("0000", "根"), mknode("0001", "乙")])

    def read_node(d, node):
        if node["node_id"] == "0000":
            return NodeRead(text="", how="unlocated")
        return NodeRead(text="x" * 9000, truncated=True, how="relocated")

    llm = FakeComplete(read=["0000", "0001"], sufficient=False)
    result = search("q", [doc], complete=llm, read_node=read_node)
    assert result.telemetry["unlocated"] == 1
    assert result.telemetry["truncated_evidence"] == 1
    unlocated = next(e for e in result.evidences if e["node_id"] == "0000")
    assert unlocated["line_start"] is None and unlocated["line_end"] is None


def test_scores_sorted_desc():
    doc = mkdoc(structure=[mknode("0000", "甲"), mknode("0001", "乙")])
    llm = FakeComplete(read=["0001", "0000"], sufficient=False)
    result = search("q", [doc], complete=llm, read_node=fake_read)
    assert [e["score"] for e in result.evidences] == [1.0, 0.5]
    # score 顺序即选中顺序（0001 先选）
    assert result.evidences[0]["node_id"] == "0001"


def test_empty_candidates_returns_empty():
    result = search("q", [], complete=FakeComplete(), read_node=fake_read)
    assert result.evidences == [] and result.llm_call_count == 0


def test_missing_node_ids_fallback_assignment():
    tree = {"structure": [mknode(title="甲", children=[mknode(title="乙")])]}
    ensure_node_ids(tree["structure"])
    ids = [n["node_id"] for n in flat_nodes(tree)]
    assert ids == ["0000", "0001"]


def test_section_titles_for_utility():
    tree = {"structure": [mknode("a", "甲", children=[mknode("b", "乙")])]}
    node = tree["structure"][0]["nodes"][0]
    assert section_titles_for(tree, node) == ["甲", "乙"]


def test_import_does_not_pull_pageindex_or_litellm():
    assert "pageindex" not in sys.modules
    assert "litellm" not in sys.modules
