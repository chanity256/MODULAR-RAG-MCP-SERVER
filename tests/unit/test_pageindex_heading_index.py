"""PI1 heading_index 单测（规格 §4：ATX/Setext、围栏、front matter、合成节点、
消歧、归一化、模糊兜底；零网络零模型）。"""

import sys
from pathlib import Path

PAGEINDEX_DIR = Path(__file__).resolve().parents[2] / "scripts" / "pageindex"
sys.path.insert(0, str(PAGEINDEX_DIR))

from heading_index import HeadingIndex, build_heading_index, normalize_title  # noqa: E402


def test_atx_closing_hashes_stripped():
    # 10.9 #7：闭合井号序列不出现在标题里（CommonMark：序列前须有空白）
    md = "## Foo ##\nbody\n## C#\nc2\n## Bar ##tail\n"
    heads = build_heading_index(md)
    assert [h["title"] for h in heads] == ["Foo", "C#", "Bar ##tail"]
    idx = HeadingIndex(md)
    assert idx.locate("Foo") == (1, 2)


def test_locate_longest_parent_suffix_beats_shorter():
    # 10.9 #6：祖先较少的候选也能对齐到短后缀，须取最长匹配而非 cands[0]
    md = "# A\n## Dup\nx\n# B\n## A\n### Dup\ny\n"
    idx = HeadingIndex(md)
    assert idx.locate("Dup", ["B", "A"]) == (6, 8)   # B > A > Dup，而非 A > Dup


def test_locate_ambiguous_after_narrowing_returns_none():
    # 两个候选与父路径同长对齐 → 歧义返回 None（规格 3.2：仍歧义返回 None）
    md = "# A\n## B\n## B\n"
    idx = HeadingIndex(md)
    assert idx.locate("B", ["A"]) is None


def test_bom_does_not_hide_first_heading():
    # 10.9 #21：UTF-8 BOM 不剥离会丢首个 ATX 标题
    heads = build_heading_index("# Title\nbody\n## Sub\n")
    assert [h["title"] for h in heads] == ["Title", "Sub"]
    heads = build_heading_index("﻿# Title\nbody\n## Sub\n")
    assert [h["title"] for h in heads] == ["Title", "Sub"]
    assert heads[0]["line"] == 1


def test_setext_false_positives_rejected():
    # 10.9 #22：下划线行的上一行必须是段落行
    assert build_heading_index("```\ncode\n```\n---\nafter\n") == []
    assert build_heading_index("1. item\n---\n") == []
    assert build_heading_index("- item\n===\n") == []
    assert build_heading_index("> quote\n---\n") == []
    # 段落行后接 --- 仍是合法 Setext（保持 CommonMark 行为）
    assert [h["title"] for h in build_heading_index("Title\n---\n")] == ["Title"]


def test_atx_levels_and_line_spans():
    md = "# A\na1\n## B\nb1\nb2\n### C\nc1\n## D\nd1"
    idx = HeadingIndex(md)
    assert [h["level"] for h in idx.heads] == [1, 2, 3, 2]
    assert [h["title"] for h in idx.heads] == ["A", "B", "C", "D"]
    # line_end = 下一个同级或更高级标题的前一级（level <= 自身）标题的前一行
    assert idx.locate("A") == (1, 9)          # 一级标题：无更高级 → 文件尾
    assert idx.locate("B", ["A"]) == (3, 7)   # B 到 ## D（同级）前一行
    assert idx.locate("C", ["A", "B"]) == (6, 7)
    assert idx.locate("D", ["A"]) == (8, 9)   # 末个标题延伸到文件尾


def test_setext_headings():
    md = "Title One\n=\nbody\nTitle Two\n--\nbody2"
    heads = build_heading_index(md)
    assert [(h["level"], h["title"]) for h in heads] == [(1, "Title One"), (2, "Title Two")]
    idx = HeadingIndex(md)
    assert idx.locate("Title One") == (1, 6)  # 一级：无同级/更高级 → 文件尾
    assert idx.locate("Title Two", ["Title One"]) == (4, 6)


def test_setext_single_dash_underline_not_heading():
    # 单个 - 与列表项歧义（POC 同口径）：需 ≥2 个 - 才算 Setext 下划线
    md = "Title Two\n-\nbody"
    assert build_heading_index(md) == []


def test_setext_list_item_not_heading():
    md = "intro\n\n- item one\n- item two\n\n## Real\n"
    heads = build_heading_index(md)
    assert [h["title"] for h in heads] == ["Real"]


def test_fence_code_block_hash_not_heading():
    md = "# Real\n```python\n# not a heading\n~~~\n# also not\n```\nafter"
    heads = build_heading_index(md)
    assert [h["title"] for h in heads] == ["Real"]


def test_front_matter_skipped():
    md = "---\ntitle: x\nmodified_date: 2026-01-01\n---\n# Real\nbody"
    heads = build_heading_index(md)
    assert [h["title"] for h in heads] == ["Real"]
    assert heads[0]["line"] == 5


def test_empty_atx_title_skipped():
    md = "##\n# Real\n"
    assert [h["title"] for h in build_heading_index(md)] == ["Real"]


def test_parents_stack():
    md = "# A\n## B\n### C\n## D\n### E"
    heads = build_heading_index(md)
    assert heads[2]["parents"] == ["A", "B"]
    assert heads[4]["parents"] == ["A", "D"]


def test_preface_synthetic_node():
    md = "前言正文第一行\n前言第二行\n# First\nbody"
    idx = HeadingIndex(md)
    assert idx.locate("Preface") == (1, 2)


def test_preface_without_content_returns_none():
    idx = HeadingIndex("# Only\n")
    assert idx.locate("Preface") is None


def test_intro_synthetic_node():
    md = "# Parent\n父节点正文\n## Child One\nC1\n## Child Two\n"
    idx = HeadingIndex(md)
    # <parent> (intro)：父标题行到首个子标题前
    assert idx.locate("Parent (intro)") == (1, 2)
    assert idx.locate("Parent") == (1, 6)


def test_intro_without_children_falls_back_to_full_span():
    md = "# Parent\n正文无子节点\n# Next\n"
    idx = HeadingIndex(md)
    assert idx.locate("Parent (intro)") == (1, 2)


def test_same_title_disambiguated_by_parent_path():
    md = "# A\n## Dup\na-dup\n# B\n## Dup\nb-dup\n"
    idx = HeadingIndex(md)
    assert idx.locate("Dup", ["A"]) == (2, 3)
    assert idx.locate("Dup", ["B"]) == (5, 7)  # 末段延伸到文件尾（含末行空行）
    # 无父路径且歧义 → None
    assert idx.locate("Dup") is None


def test_parent_path_tail_alignment_tolerates_skip_level():
    md = "# A\n## B\n### Dup\nx\n# C\n### Dup\ny\n"
    idx = HeadingIndex(md)
    # Flash 树可能跳层：只给 ["B"] 也能对齐到 A>B>Dup
    assert idx.locate("Dup", ["B"]) == (3, 4)
    assert idx.locate("Dup", ["A"]) is None  # A 尾对齐不上 B 下的 Dup


def test_normalization_emphasis_and_width():
    md = "# **3.** **网页RAG项目选型**\nbody\n# ＲＡＧ\nbody2"
    idx = HeadingIndex(md)
    assert idx.locate("3. 网页RAG项目选型") == (1, 2)
    assert idx.locate("RAG") == (3, 4)


def test_normalization_radical_forms():
    md = "# 如何见门风\nbody"
    idx = HeadingIndex(md)
    # PDF 抽取把 见/门/风 映射为部件形（⻅⻔⻛）：归一化映射回正字后精确命中
    assert idx.locate("如何⻅⻔⻛") == (1, 2)


def test_fuzzy_containment_fallback_unique():
    md = "# 大模型面试指南\nbody"
    idx = HeadingIndex(md)
    # 上游 merge 档 LLM 重写标题（原标题被扩写）：按包含关系回收唯一候选
    assert idx.locate("大模型面试指南内容说明") == (1, 2)


def test_fuzzy_containment_ambiguous_returns_none():
    md = "# 大模型面试指南\n## 大模型面试指南详解\n"
    idx = HeadingIndex(md)
    # 查询与两个标题互含 → 歧义放弃
    assert idx.locate("大模型面试") is None


def test_no_match_returns_none_and_empty_title():
    idx = HeadingIndex("# A\n")
    assert idx.locate("不存在") is None
    assert idx.locate("") is None
    assert idx.locate("   ") is None


def test_normalize_title_pure_function():
    assert normalize_title(" **A** _B_ `C` [D] ") == "ABCD"
    assert normalize_title("⻅⻔⻛") == "见门风"
