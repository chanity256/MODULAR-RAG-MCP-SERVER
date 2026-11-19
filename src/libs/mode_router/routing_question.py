"""三档检索模式的决策问题文案（MODE_ROUTER_SPEC MR2，单一来源）。

文案为 MR0.2 选定的 v2 版（按问题形态描述三档；v1 只讲「是否已摄取」，
基线命中率 46.7–63.3%，已弃用，见 MODE_ROUTER_SPEC §3.6 与 MR0.2 行）。
运行时后端与 ``scripts/eval_mode_router.py`` 共用这一份；MR6 / MR7 的
基线复测沿用同一文案与同一标注集（D2）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["WORDINGS", "DEFAULT_WORDING", "instructions_and_criteria"]

DEFAULT_WORDING = "zh"

#: 每个语言的 criteria 键集相同（三档真实模式）；zh 与 en 逐句对译，只比较语言差异。
WORDINGS: dict[str, dict[str, Any]] = {
    "zh": {
        "instructions": "应由哪种检索模式回答这个问题？",
        "criteria": {
            "hybrid": "解释一个概念或事实，一两段内容即可回答，包括同一处讲到的简单对比，例如 X 是什么、为什么、X 和 Y 有何区别；最快",
            "agentic_fast": (
                "需要逐字扫描本地文件：哪个文件或章节提到某个词、列出所有出现位置、从指定文件原样摘录原文或链接、"
                "列出某文件的标题，或查找新上传、尚未索引的文件"
            ),
            "agentic_deep": (
                "需要阅读并综合多个文件：对比分散在不同文档里的对象、汇总全部记录，"
                "或基于多个章节写综述、计划或论证；最慢、成本最高"
            ),
        },
    },
    "en": {
        "instructions": "Which retrieval mode should answer this question?",
        "criteria": {
            "hybrid": (
                "Explain a concept or fact that one or a few passages answer, including a simple comparison"
                " covered in one place, e.g. what is X, why does X, how does X differ from Y; fastest"
            ),
            "agentic_fast": (
                "Needs a literal scan of local files: which file or section mentions a term, list every"
                " occurrence, quote original text or links from a named file, list a file's headings, or"
                " look in newly uploaded files that are not indexed yet"
            ),
            "agentic_deep": (
                "Needs reading and combining many files: compare things described in different documents,"
                " aggregate across all records, or build a survey, plan or argument from several chapters;"
                " slowest and most expensive"
            ),
        },
    },
}


def instructions_and_criteria(
    candidates: tuple[str, ...], lang: str = DEFAULT_WORDING
) -> tuple[str, dict[str, str]]:
    """按候选集生成 ``choice`` 问题的 (instructions, criteria)。

    ``criteria`` 的键恰为 ``candidates``（保持给定顺序，允许是三档的子集，
    例如 ``allow_deep=false`` 时剔除 ``agentic_deep``）。
    """
    spec = WORDINGS.get(lang)
    if spec is None:
        raise ValueError(f"unknown wording lang {lang!r}; expected one of {sorted(WORDINGS)}")
    known = spec["criteria"]
    missing = [c for c in candidates if c not in known]
    if missing:
        raise ValueError(f"wording {lang!r} has no criteria text for candidates: {missing}")
    return spec["instructions"], {c: known[c] for c in candidates}
