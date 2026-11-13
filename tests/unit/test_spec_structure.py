"""SD7.1：规格结构校验（借鉴 OpenSpec ``validate``，AI_NATIVE_SDLC_SPEC §3.7）。

约束范围按编号划界，可机器判定：

- ``docs/specs/`` 下所有在途规格；
- ``docs/frozen/`` 中编号 ≥ 10 的冻结规格（``<NN>_<NAME>_frozen.md``）。

编号 01–09 与无编号的谱系存档豁免，不回改已冻结文档。每份受约束规格必须有三项结构：

1. 任务表：表头同时含「任务」与「验收标准」列；
2. 测试方案：标题（``##`` / ``###``）含「测试方案」；
3. 范围：标题分别含 ``In Scope`` 与 ``Out of Scope``（大小写不敏感）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = REPO_ROOT / "docs" / "specs"
FROZEN_DIR = REPO_ROOT / "docs" / "frozen"

#: 冻结规格编号 ≥ 此值才受约束（01–09 早于本规格冻结，豁免）。
MIN_FROZEN_NUMBER = 10

_NUMBERED_FROZEN = re.compile(r"^(\d{2})_.+_frozen\.md$")
_FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
_HEADING = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.M)
_TABLE_HEADER = re.compile(r"^\|(.+)\|\s*\n\|[\s:|-]+\|\s*$", re.M)


def in_scope(path: Path) -> bool:
    if path.parent.name == "specs":
        return path.suffix == ".md"
    match = _NUMBERED_FROZEN.match(path.name)
    return bool(match) and int(match.group(1)) >= MIN_FROZEN_NUMBER


def scoped_specs() -> list[Path]:
    candidates = sorted(SPECS_DIR.glob("*.md")) + sorted(FROZEN_DIR.glob("*.md"))
    return [p for p in candidates if in_scope(p)]


def structure_problems(text: str) -> list[str]:
    body = _FENCE.sub("", text)
    headings = [h.lower() for h in _HEADING.findall(body)]
    headers = [[cell.strip() for cell in row.split("|")] for row in _TABLE_HEADER.findall(body)]
    problems = []
    if not any(any("任务" in c for c in cells) and any("验收标准" in c for c in cells) for cells in headers):
        problems.append("缺少含「任务」与「验收标准」列的任务表")
    if not any("测试方案" in h for h in headings):
        problems.append("缺少「测试方案」章节")
    if not any("in scope" in h for h in headings):
        problems.append("缺少 In Scope 章节")
    if not any("out of scope" in h for h in headings):
        problems.append("缺少 Out of Scope 章节")
    return problems


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@pytest.mark.parametrize("path", scoped_specs(), ids=_rel)
def test_scoped_spec_has_required_structure(path: Path) -> None:
    assert structure_problems(path.read_text(encoding="utf-8")) == []


def test_scope_boundary_by_number() -> None:
    names = {_rel(p) for p in scoped_specs()}
    assert "docs/frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md" in names
    assert all(not n.startswith("docs/frozen/0") for n in names)
    exempt = [p for p in FROZEN_DIR.glob("*.md") if not in_scope(p)]
    assert any(p.name == "01_DEV_SPEC_frozen.md" for p in exempt)
    assert any(p.name == "ci-pipeline_backlog_frozen.md" for p in exempt)


def test_in_scope_rules() -> None:
    assert in_scope(SPECS_DIR / "NEW_SPEC.md")
    assert in_scope(FROZEN_DIR / "11_NEXT_SPEC_frozen.md")
    assert not in_scope(FROZEN_DIR / "09_WEBUI_SIDECAR_SPEC_frozen.md")
    assert not in_scope(FROZEN_DIR / "graph-rag_backlog_frozen.md")


_GOOD = """\
# X_SPEC

## 2. 核心目标与范围

### 2.1 In Scope

- a

### 2.2 Out of Scope

- b

## 4. 测试方案

- c

## 5. 任务排期

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| X1 | t | ok | [ ] |
"""


def test_checker_accepts_complete_spec() -> None:
    assert structure_problems(_GOOD) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda t: t.replace("| 验收标准 |", "| 备注 |"), "任务表"),
        (lambda t: t.replace("## 4. 测试方案", "## 4. 测试"), "测试方案"),
        (lambda t: t.replace("### 2.1 In Scope", "### 2.1 范围内"), "In Scope"),
        (lambda t: t.replace("### 2.2 Out of Scope", "### 2.2 不做"), "Out of Scope"),
        (lambda t: "```\n" + t + "```\n", "测试方案"),
    ],
    ids=["no-acceptance-column", "no-test-plan", "no-in-scope", "no-out-of-scope", "only-inside-code-fence"],
)
def test_checker_flags_each_missing_item(mutate, expected) -> None:
    problems = structure_problems(mutate(_GOOD))
    assert any(expected in p for p in problems), problems
