"""SD2.2: REVIEW.md 与 Droid review 指令文件的 checklist 块逐字一致（防两份漂移）；SD2.3: Droid review workflow 锁定。"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

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
    for required in ("第 1 遍：bugs", "第 2 遍：security", "第 3 遍：合规", "最多 5 条 P3", "按 P0 → P3 排序",
                     "sirchmunk 上游源码", "删除或弱化已有测试断言", "frontend/out/"):
        assert required in block, required


# SD2.3：Droid 自动 review / @droid 响应 workflow 的离线锁定。
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DROID_REVIEW = WORKFLOWS / "droid-review.yml"
DROID_TAG = WORKFLOWS / "droid.yml"
API_KEY_EXPR = "${{ secrets.FACTORY_API_KEY }}"
_KEY_LIKE = re.compile(r"\bfk-[A-Za-z0-9_-]{8,}|\b[A-Za-z0-9]{40,}\b")


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 把裸键 on 解析成布尔 True
    if True in data:
        data["on"] = data.pop(True)
    return data


def _droid_step(data: dict, job: str) -> dict:
    steps = [s for s in data["jobs"][job]["steps"] if str(s.get("uses", "")).startswith("Factory-AI/droid-action@")]
    assert len(steps) == 1
    return steps[0]


class TestDroidWorkflows:
    def test_both_parse(self):
        for path in (DROID_REVIEW, DROID_TAG):
            data = _load(path)
            assert data["name"] and data["on"] and data["jobs"], path.name

    def test_review_triggers_only_on_non_draft_pr(self):
        data = _load(DROID_REVIEW)
        assert data["on"] == {"pull_request": {"types": ["opened", "ready_for_review", "reopened"]}}
        assert data["jobs"]["droid-review"]["if"] == "github.event.pull_request.draft == false"

    def test_review_options(self):
        data = _load(DROID_REVIEW)
        assert data["jobs"]["droid-review"]["timeout-minutes"] == 60
        opts = _droid_step(data, "droid-review")["with"]
        assert opts["automatic_review"] is True
        assert opts["automatic_security_review"] is True
        assert opts["review_depth"] == "shallow"
        assert opts["factory_api_key"] == API_KEY_EXPR

    def test_tag_requires_mention(self):
        data = _load(DROID_TAG)
        job = data["jobs"]["droid"]
        assert job["timeout-minutes"] == 60
        clauses = [c for c in job["if"].strip().splitlines() if c.strip()]
        assert len(clauses) == 5 and all("'@droid'" in c for c in clauses)
        assert _droid_step(data, "droid")["with"] == {"factory_api_key": API_KEY_EXPR}

    def test_tag_has_read_only_contents(self):
        assert _load(DROID_TAG)["jobs"]["droid"]["permissions"]["contents"] == "read"

    def test_no_literal_key_or_escaped_expression(self):
        for path in (DROID_REVIEW, DROID_TAG):
            raw = path.read_text(encoding="utf-8")
            assert API_KEY_EXPR in raw, path.name
            assert "\\${{" not in raw, path.name
            assert not _KEY_LIKE.search(raw), path.name

    def test_not_part_of_required_ci(self):
        ci = _load(WORKFLOWS / "ci.yml")
        assert set(ci["jobs"]) == {"python", "frontend"}
        raw = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        assert "droid-action" not in raw and "Droid" not in raw

    def test_do_not_trigger_paid_evals(self):
        paths = _load(WORKFLOWS / "agent-evals.yml")["on"]["pull_request"]["paths"]
        for path in paths:
            assert not path.startswith(".github/workflows")
            assert path not in (".github/**", ".github/*")
