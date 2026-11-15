"""KE0: golden 集 gold rubric 漂移守卫（离线）。

KE2 成对实验的判分依据是 tests/fixtures/agentic/ 三个 fixture 的
``expected_sources`` 与 ``answer_should_contain``；语料（本仓库 tracked 文件）
漂移（改名、删除、措辞变化）会使 rubric 失真。此测试在离线套件中及早报警：
来源文件必须存在，判别片段必须仍出现在来源文件中。
KE2 运行时的语料以 corpus_snapshot.sh 物化的快照为准，本测试锁定的是
当前工作树与 fixture 的一致性。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTIC_FIXTURES = REPO_ROOT / "tests/fixtures/agentic"
FIXTURE_FILES = (
    "golden_questions.json",
    "golden_questions_docs.json",
    "reuse_probe_questions.json",
    "ke2_probe_questions.json",
)


def _questions() -> list[tuple[str, dict]]:
    questions: list[tuple[str, dict]] = []
    for name in FIXTURE_FILES:
        data = json.loads((AGENTIC_FIXTURES / name).read_text(encoding="utf-8"))
        for q in data["questions"]:
            questions.append((f"{name}:{q['id']}", q))
    return questions


ALL_QUESTIONS = _questions()


def test_fixture_questions_discovered() -> None:
    """fixture 解析正常（防 fixture 自身格式漂移导致静默空转）。"""
    ids = {qid.split(":", 1)[1] for qid, _ in ALL_QUESTIONS}
    for required in ("Q3", "P1-seed", "P1-reword", "D1"):
        assert required in ids


@pytest.mark.parametrize("qid,q", ALL_QUESTIONS, ids=[qid for qid, _ in ALL_QUESTIONS])
def test_expected_sources_exist(qid: str, q: dict) -> None:
    for src in q.get("expected_sources", []):
        path = REPO_ROOT / src
        assert path.exists(), f"{qid}: expected source missing: {src}"


@pytest.mark.parametrize("qid,q", ALL_QUESTIONS, ids=[qid for qid, _ in ALL_QUESTIONS])
def test_answer_fragment_in_expected_sources(qid: str, q: dict) -> None:
    fragment = q.get("answer_should_contain")
    if fragment is None:
        pytest.skip("no answer rubric")
    haystack = "\n".join(
        (REPO_ROOT / src).read_text(encoding="utf-8", errors="replace")
        for src in q["expected_sources"]
    )
    assert fragment in haystack, f"{qid}: rubric fragment {fragment!r} no longer in expected sources"
