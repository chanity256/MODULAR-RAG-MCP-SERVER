"""S1.4/S1.5: sidecar 响应解析 + 证据转换单元测试（离线）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from libs.agentic_search.base_agentic_search import (
    AgenticEvidence,
    AgenticSearchResponse,
    AgenticSearchResponseError,
)
from libs.agentic_search.converter import (
    evidence_to_retrieval_results,
    make_agentic_id,
    parse_search_context,
)


def _context_data(**overrides) -> dict:
    data = {
        "answer": "The formula lives in bm25_indexer.py.",
        "total_llm_tokens": 512,
        "llm_usages": [{"total_tokens": 256}, {"total_tokens": 256}],
        "cluster": {
            "confidence": 0.91,
            "search_results": ["docs/a.md", "docs/b.pdf"],
            "evidences": [
                {
                    "file_or_url": "docs/a.md",
                    "summary": "BM25 defaults",
                    "snippets": [
                        {"snippet": "k1=1.5, b=0.75", "start": 10, "end": 22, "score": 0.8},
                        "plain string snippet",
                    ],
                },
                {
                    "file_or_url": "docs/b.pdf",
                    "summary": "PDF evidence",
                    "snippets": [],
                },
            ],
        },
    }
    data.update(overrides)
    return data


class TestParseSearchContext:
    def test_full_context(self):
        response = parse_search_context(_context_data(), mode="DEEP")
        assert response.answer.startswith("The formula")
        assert response.mode == "DEEP"
        assert response.confidence == 0.91
        assert response.source_files == ["docs/a.md", "docs/b.pdf"]
        assert response.llm_call_count == 2
        assert response.total_llm_tokens == 512
        assert len(response.evidences) == 3  # 2 snippets + summary fallback

    def test_snippet_dict_offsets_and_score(self):
        response = parse_search_context(_context_data())
        first = response.evidences[0]
        assert (first.snippet, first.char_start, first.char_end, first.score) == (
            "k1=1.5, b=0.75", 10, 22, 0.8,
        )

    def test_snippet_plain_string_has_no_offsets(self):
        response = parse_search_context(_context_data())
        second = response.evidences[1]
        assert second.snippet == "plain string snippet"
        assert second.char_start is None and second.char_end is None and second.score is None

    def test_empty_snippets_falls_back_to_summary(self):
        response = parse_search_context(_context_data())
        third = response.evidences[2]
        assert third.source_path == "docs/b.pdf"
        assert third.snippet == "PDF evidence"

    def test_cluster_none_tolerated(self):
        response = parse_search_context({"answer": "hi there"}, mode="FAST")
        assert response.evidences == []
        assert response.source_files == []
        assert response.confidence is None
        assert response.llm_call_count is None

    def test_telemetry_missing_tolerated(self):
        data = _context_data()
        data.pop("total_llm_tokens")
        data.pop("llm_usages")
        response = parse_search_context(data)
        assert response.total_llm_tokens is None
        assert response.llm_call_count is None

    def test_malformed_snippet_skipped(self):
        data = _context_data()
        data["cluster"]["evidences"][0]["snippets"].append(42)
        response = parse_search_context(data)  # 单条畸形降级跳过
        assert len(response.evidences) == 3

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param("not-a-dict", id="non-dict"),
            pytest.param({"cluster": None}, id="missing-answer"),
            pytest.param({"answer": 123}, id="answer-not-str"),
        ],
    )
    def test_contract_violations_fail(self, data):
        with pytest.raises(AgenticSearchResponseError):
            parse_search_context(data)

    def test_evidences_not_list_fails(self):
        with pytest.raises(AgenticSearchResponseError, match="evidences"):
            parse_search_context({"answer": "a", "cluster": {"evidences": "bad"}})


class TestEvidenceToRetrievalResults:
    def test_deterministic_id_and_metadata_contract(self):
        response = parse_search_context(_context_data())
        first = response.evidences[0]
        results = evidence_to_retrieval_results(response)
        assert results[0].chunk_id == make_agentic_id(first)
        assert results[0].chunk_id.startswith("agentic:")
        assert len(results[0].chunk_id) == len("agentic:") + 12
        meta = results[0].metadata
        assert meta["retrieval_route"] == "agentic"
        assert meta["source_path"] == "docs/a.md"
        assert meta["char_start"] == 10 and meta["char_end"] == 22
        assert meta["agentic_confidence"] == 0.91
        assert meta["mode"] == "FAST"

    def test_id_stable_across_calls(self):
        evidence = AgenticEvidence(source_path="d/a.md", snippet="s", score=1.0, char_start=0, char_end=1)
        assert make_agentic_id(evidence) == make_agentic_id(
            AgenticEvidence(source_path="d/a.md", snippet="s", score=1.0, char_start=0, char_end=1)
        )
        assert make_agentic_id(evidence) != make_agentic_id(
            AgenticEvidence(source_path="d/a.md", snippet="s2", score=1.0, char_start=0, char_end=2)
        )

    def test_missing_score_defaults_zero(self):
        response = AgenticSearchResponse(
            answer="a", evidences=[AgenticEvidence(source_path="d/a.md", snippet="s")]
        )
        results = evidence_to_retrieval_results(response)
        assert results[0].score == 0.0

    def test_top_k_truncation_and_validation(self):
        response = parse_search_context(_context_data())
        assert len(evidence_to_retrieval_results(response, top_k=2)) == 2
        with pytest.raises(ValueError, match="top_k"):
            evidence_to_retrieval_results(response, top_k=0)
        with pytest.raises(ValueError, match="top_k"):
            evidence_to_retrieval_results(response, top_k=True)  # type: ignore[arg-type]

    def test_text_is_snippet_for_citations(self):
        response = parse_search_context(_context_data())
        results = evidence_to_retrieval_results(response)
        assert results[0].text == "k1=1.5, b=0.75"


# --------------------------------------------------------------------- #
# SX1：顶层 telemetry 白名单解析
# --------------------------------------------------------------------- #

_TELEMETRY_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/agentic/contract_response_telemetry.json"


def _telemetry_data() -> dict:
    return json.loads(_TELEMETRY_FIXTURE.read_text(encoding="utf-8"))["data"]


class TestTelemetry:
    def test_fixture_shape_locked(self):
        """契约 fixture 锁 telemetry shape（上游 b314e11 to_dict() 顶层合并）。"""
        response = parse_search_context(_telemetry_data(), mode="DEEP")
        assert response.telemetry == {
            "loop_count": 2,
            "max_loops": 6,
            "budget_remaining": 26880,
            "llm_call_count": 3,
            "read_file_count": 2,
            "search_history_len": 2,
            "retrieval_tool_counts": {"deep_file_extract": 1, "keyword_search": 2},
            "zero_llm_path": False,
            "loop_hygiene_rescue_used": True,
            "self_correct_entered": True,
        }
        assert response.total_llm_tokens == 5120
        assert response.llm_call_count == 3
        assert response.to_dict()["telemetry"] == response.telemetry

    def test_total_llm_tokens_not_duplicated(self):
        telemetry = parse_search_context(_telemetry_data()).telemetry
        assert "total_llm_tokens" not in telemetry

    def test_raw_logs_and_ad_hoc_strings_not_exported(self):
        telemetry = parse_search_context(_telemetry_data()).telemetry
        for key in ("retrieval_logs", "read_file_ids", "search_history", "llm_usages",
                    "computation_answer_before", "start_time", "answer", "cluster"):
            assert key not in telemetry

    def test_absent_when_no_telemetry_keys(self):
        """裁剪 fixture（只有 llm_usages）不伪造 telemetry。"""
        assert parse_search_context(_context_data()).telemetry is None
        assert parse_search_context({"answer": "a"}).telemetry is None

    def test_flag_alone_enables_telemetry(self):
        telemetry = parse_search_context({"answer": "a", "fallback_best_candidate": True}).telemetry
        assert telemetry is not None
        assert telemetry["fallback_best_candidate"] is True
        assert telemetry["loop_count"] is None
        assert telemetry["zero_llm_path"] is None

    def test_llm_call_count_key_preferred_over_usages(self):
        data = {"answer": "a", "llm_call_count": 5, "llm_usages": [{}], "loop_count": 1}
        response = parse_search_context(data)
        assert response.llm_call_count == 5
        assert response.telemetry["llm_call_count"] == 5

    def test_llm_call_count_falls_back_to_usages(self):
        response = parse_search_context({"answer": "a", "llm_usages": [{}, {}], "loop_count": 1})
        assert response.telemetry["llm_call_count"] == 2

    @pytest.mark.parametrize(
        ("llm_calls", "loops", "expected"),
        [
            pytest.param(0, 0, True, id="zero-zero"),
            pytest.param(0, 1, False, id="loop-without-llm"),
            pytest.param(2, 0, False, id="llm-without-loop"),
        ],
    )
    def test_zero_llm_path(self, llm_calls, loops, expected):
        data = {"answer": "a", "llm_call_count": llm_calls, "loop_count": loops}
        assert parse_search_context(data).telemetry["zero_llm_path"] is expected

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param({"answer": "a", "loop_count": 0}, id="llm-count-missing"),
            pytest.param({"answer": "a", "llm_call_count": 0}, id="loop-count-missing"),
            pytest.param({"answer": "a", "llm_call_count": 0, "loop_count": "0"}, id="loop-count-malformed"),
        ],
    )
    def test_zero_llm_path_unknown_when_key_missing(self, data):
        assert parse_search_context(data).telemetry["zero_llm_path"] is None

    def test_malformed_values_tolerated(self):
        data = {
            "answer": "a",
            "loop_count": True,  # bool 不当作 int
            "max_loops": "6",
            "budget_remaining": None,
            "read_file_ids": "not-a-list",
            "search_history": {"q": 1},
            "retrieval_logs": [
                {"tool_name": "keyword_search"},
                {"tool_name": ""},
                {"tool_name": 3},
                "garbage",
                {"no_tool": True},
            ],
        }
        telemetry = parse_search_context(data).telemetry  # 恒不抛
        assert telemetry["loop_count"] is None
        assert telemetry["max_loops"] is None
        assert telemetry["budget_remaining"] is None
        assert telemetry["read_file_count"] is None
        assert telemetry["search_history_len"] is None
        assert telemetry["retrieval_tool_counts"] == {"keyword_search": 1}

    def test_retrieval_logs_not_list(self):
        telemetry = parse_search_context({"answer": "a", "retrieval_logs": "x"}).telemetry
        assert telemetry["retrieval_tool_counts"] is None

    def test_non_bool_ad_hoc_keys_ignored(self):
        data = {"answer": "a", "loop_count": 1, "bridge_query": "text", "early_exit_round": 2}
        telemetry = parse_search_context(data).telemetry
        assert "bridge_query" not in telemetry
        assert "early_exit_round" not in telemetry

    def test_response_rejects_non_dict_telemetry(self):
        with pytest.raises(ValueError, match="telemetry"):
            AgenticSearchResponse(answer="a", telemetry=["bad"])  # type: ignore[arg-type]
