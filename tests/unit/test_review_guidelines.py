"""SD2.2: REVIEW.md 与 Droid review 指令文件的 checklist 块逐字一致（防两份漂移）。"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEW_MD = REPO_ROOT / "REVIEW.md"
REVIEW_SKILL = REPO_ROOT / ".factory" / "skills" / "review-guidelines" / "SKILL.md"
_BLOCK = re.compile(r"<!-- review-checklist:start -->\n(.*?)<!-- review-checklist:end -->", re.S)


def _checklist(path: Path) -> str:
    blocks = _BLOCK.findall(path.read_text(encoding="utf-8"))
    assert len(blocks) == 1, f"{path.relative_to(REPO_ROOT)} must contain exactly one review-checklist block"
    return blocks[0]


def test_checklist_blocks_identical():
    assert _checklist(REVIEW_MD) == _checklist(REVIEW_SKILL)


def test_skill_frontmatter_name():
    text = REVIEW_SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: review-guidelines\n")


def test_checklist_covers_spec_requirements():
    block = _checklist(REVIEW_MD)
    for required in ("第 1 遍：bugs", "第 2 遍：security", "第 3 遍：合规", "最多 5 条 Nit",
                     "sirchmunk 上游源码", "删除或弱化已有测试断言", "frontend/out/"):
        assert required in block, required
