"""KE2: ke2_experiment.py 纯函数核心的离线单测（零网络、零真实模型）。

覆盖：探针/预热题加载、臂 env 构造、逐请求判分（不统称 hit）、演化日志证据
解析、预算上限、预设判定门与成对统计。编排函数（真实 sidecar / LLM）只在
RAG_AGENTIC_INTEGRATION=1 下运行，不在离线套件内。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts/sirchmunk/ke2_experiment.py"
_spec = importlib.util.spec_from_file_location("ke2_experiment", _SCRIPT)
ke2 = importlib.util.module_from_spec(_spec)
sys.modules["ke2_experiment"] = ke2
_spec.loader.exec_module(ke2)


class TestLoadQuestions:
    def test_probe_groups_shape(self):
        groups = ke2.load_probe_groups()
        assert [g["group"] for g in groups] == ["P1", "P2"]
        for g in groups:
            roles = [q["role"] for q in g["formal"]]
            assert roles == ["seed", "repeat", "reword", "reword", "reword", "distractor"]
            assert len(g["train"]) == 2
            assert all(q["answer_should_contain"] for q in g["formal"])

    def test_warmup_fills_are_independent_topics(self):
        fills = ke2.load_warmup_questions()
        assert len(fills) == 6
        joined = " ".join(q["query"].lower() for q in fills)
        # 与两个探针主题（Web UI 启动命令 / BM25 IDF）不重叠
        assert "web ui" not in joined and "bm25" not in joined


class TestBuildArmEnv:
    BASE = (
        "LLM_API_KEY=secret\n"
        "SIRCHMUNK_WORK_PATH=./data/db/sirchmunk\n"
        "SIRCHMUNK_ENABLE_CLUSTER_REUSE=false\n"
        "SIRCHMUNK_API_TOKEN=tok\n"
        "SIRCHMUNK_BASE_URL=http://127.0.0.1:8584\n"
    )

    def test_arm_b_overrides(self, tmp_path):
        text = ke2.build_arm_env(self.BASE, tmp_path / "work", evolution=True)
        kv = dict(line.split("=", 1) for line in text.strip().splitlines())
        assert kv["SIRCHMUNK_ENABLE_CLUSTER_REUSE"] == "true"  # 两臂同构：复用恒开
        assert kv["SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION"] == "true"
        assert kv["SIRCHMUNK_WORK_PATH"] == str(tmp_path / "work")
        assert kv["LLM_API_KEY"] == "secret"  # 其余键原样保留
        assert kv["SIRCHMUNK_BASE_URL"] == ke2.BASE_URL

    def test_arm_a_evolution_false(self, tmp_path):
        text = ke2.build_arm_env(self.BASE, tmp_path / "work", evolution=False)
        kv = dict(line.split("=", 1) for line in text.strip().splitlines())
        assert kv["SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION"] == "false"
        assert kv["SIRCHMUNK_ENABLE_CLUSTER_REUSE"] == "true"

    def test_appends_model_cache_when_missing(self, tmp_path):
        text = ke2.build_arm_env("SIRCHMUNK_API_TOKEN=tok\n", tmp_path / "w", False)
        assert "EMBEDDING_CACHE_DIR=" in text

    def test_existing_evolution_line_is_overridden_not_duplicated(self, tmp_path):
        base = self.BASE + "SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION=true\n"
        text = ke2.build_arm_env(base, tmp_path / "w", evolution=False)
        values = [l.split("=", 1)[1] for l in text.strip().splitlines()
                  if l.startswith("SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION")]
        assert values == ["false"]


def _row(**kw) -> dict:
    base = {"arm": "A", "group": "P1", "role": "reword", "repeat": 1, "id": "x",
            "query": "q", "answer_should_contain": "start_api", "status": 200,
            "answer": "use start_api here", "source_hit": True, "total_llm_tokens": 100,
            "elapsed_s": 1.0}
    base.update(kw)
    return base


class TestJudgeRow:
    def test_valid_with_fast_reuse(self):
        judged = ke2.judge_row(_row(role="repeat"), {
            "hit": True, "fast_reused": True, "similarity": 1.0, "miss": False})
        assert judged["category"] == "valid"
        assert judged["fast_reused"] and judged["reuse_candidate"]
        assert not judged["soft_reused"]
        assert judged["answer_correct"]

    def test_soft_reuse_is_candidate_without_hard(self):
        judged = ke2.judge_row(_row(), {"hit": True, "fast_reused": False, "similarity": 0.9})
        assert judged["soft_reused"] and not judged["fast_reused"]

    def test_invalid_on_http_error(self):
        judged = ke2.judge_row(_row(status=500), None)
        assert judged["category"] == "invalid"
        assert "http_500" in judged["reasons"] and "log_block_not_aligned" in judged["reasons"]

    def test_invalid_on_empty_answer_with_rubric(self):
        judged = ke2.judge_row(_row(answer="   "), {"hit": False, "fast_reused": False})
        assert judged["category"] == "invalid"
        assert "empty_answer" in judged["reasons"]

    def test_no_rubric_question_can_be_valid(self):
        judged = ke2.judge_row(_row(answer="something", answer_should_contain=None),
                               {"hit": False, "fast_reused": False})
        assert judged["category"] == "valid"
        assert not judged["answer_correct"]


class TestEvolveEvents:
    def test_saved_ids_and_merges(self):
        text = "\n".join([
            "Saved knowledge cluster aaa",
            "[Evolve] Starting evolver step with 5 clusters in the buffer.",
            "[Evolve] Connected and merged clusters for 5 newly created clusters.",
            "Saved knowledge cluster aaa",  # 重复 id 只计一次
            "Saved knowledge cluster bbb",
        ])
        events = ke2.parse_evolve_events(text)
        assert events["saved_cluster_ids"] == ["aaa", "bbb"]
        assert events["phase1_merge_events"] == [5]
        assert events["evolve_steps"] == 2


class TestBudgetAndVerdict:
    def test_budget_caps(self):
        assert ke2.check_budget({"total_tokens": 100, "requests": 5}, ke2.PRESET) is None
        assert "token cap" in ke2.check_budget(
            {"total_tokens": ke2.PRESET["max_total_tokens"] + 1, "requests": 5}, ke2.PRESET)
        assert "request cap" in ke2.check_budget(
            {"total_tokens": 0, "requests": ke2.PRESET["max_requests"] + 1}, ke2.PRESET)

    def test_gate_positive(self):
        a = [_row(id=f"a{i}", fast_reused=False) for i in range(6)]
        b = [_row(id=f"b{i}", fast_reused=i < 2) for i in range(6)]
        for r in a + b:
            r["category"] = "valid"
        out = ke2.paired_analysis(a, b, ke2.PRESET)
        assert out["verdict"] == "positive" and out["diff"] == 2

    def test_gate_not_met_when_armA_also_hits(self):
        a = [_row(id=f"a{i}", fast_reused=True) for i in range(6)]
        b = [_row(id=f"b{i}", fast_reused=True) for i in range(6)]
        for r in a + b:
            r["category"] = "valid"
        out = ke2.paired_analysis(a, b, ke2.PRESET)
        assert out["verdict"] != "positive"  # A 臂非 0，按预设排除噪声后不记正面

    def test_both_zero_is_negative(self):
        rows = [_row(id=f"r{i}", fast_reused=False) for i in range(6)]
        for r in rows:
            r["category"] = "valid"
        out = ke2.paired_analysis(rows, [dict(r, id=r["id"] + "b") for r in rows], ke2.PRESET)
        assert out["verdict"] == "negative"

    def test_invalid_rows_excluded_from_stats(self):
        a = [_row(id="bad", status=500, answer="", fast_reused=False)]
        a[0]["category"] = "invalid"
        b = [_row(id="ok", fast_reused=True, category="valid")]
        out = ke2.paired_analysis(a, b, ke2.PRESET)
        assert out["reword_total_armA"] == 0  # 无效轮不进统计

    def test_no_valid_reword_rounds_is_not_assessable(self):
        """两侧均无有效 reword 轮 → 不得作确定性正负裁决（规格 6.2 / 第 4 章第 3 问）。"""
        rows = [_row(id=f"r{i}", status=500, answer="", fast_reused=False) for i in range(6)]
        out = ke2.paired_analysis(rows, [dict(r, id=r["id"] + "b") for r in rows], ke2.PRESET)
        assert out["verdict"] == "not_assessable"

    def test_judge_row_preserves_status_and_rubric(self):
        """二次判定（日志对齐）透传 status / answer_should_contain（KE2 首跑缺陷回归）。"""
        once = ke2.judge_row(_row(status=200), {"hit": True, "fast_reused": True})
        assert once["status"] == 200 and once["answer_should_contain"] == "start_api"
        twice = ke2.judge_row(once, {"hit": True, "fast_reused": True})
        assert twice["category"] == "valid" and twice["answer_correct"]


class TestWilson:
    def test_bounds(self):
        assert ke2.wilson_interval(0, 0) == (0.0, 0.0)
        lo, hi = ke2.wilson_interval(3, 3)
        assert 0.4 < lo < 1.0 and hi == 1.0  # 3/3 的 Wilson 下界约 0.44
        lo, hi = ke2.wilson_interval(0, 6)
        assert lo == 0.0 and hi < 0.5
