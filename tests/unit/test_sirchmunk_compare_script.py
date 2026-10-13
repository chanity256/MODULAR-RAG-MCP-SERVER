"""scripts/sirchmunk/compare.py 纯函数离线单测（SX0.4 基线采集口径）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/fixtures/agentic/contract_response_telemetry.json"


@pytest.fixture(scope="module")
def compare():
    spec = importlib.util.spec_from_file_location("sx_compare", REPO_ROOT / "scripts/sirchmunk/compare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_data() -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload.get("data", payload)


def test_extract_metrics_uses_converter_telemetry(compare):
    data = _fixture_data()
    m = compare.extract_metrics(data, "DEEP")
    assert m["telemetry"] is not None
    assert m["llm_call_count"] == m["telemetry"]["llm_call_count"]
    assert m["total_llm_tokens"] == data.get("total_llm_tokens")
    assert all(isinstance(f, str) and f for f in m["evidence_files"])
    # 白名单：字符串 ad-hoc 键不进 telemetry
    assert all(not isinstance(v, str) for v in m["telemetry"].values())


def test_extract_metrics_tolerates_minimal_payload(compare):
    m = compare.extract_metrics({"answer": "x", "llm_usages": [{}, {}]}, "FAST")
    assert m["answer"] == "x"
    assert m["evidence_files"] == []
    assert m["telemetry"] is None
    assert m["llm_call_count"] == 2


def test_extract_metrics_malformed_cluster(compare):
    m = compare.extract_metrics({"answer": "", "cluster": {"evidences": ["bad", {"file_or_url": None}]}}, "FAST")
    assert m["evidence_files"] == []


def test_json_out_written(compare, tmp_path, monkeypatch):
    rows = [{"id": "Q1", "category": "c", "query": "q", "mode": "FAST", "hit": True,
             "evidence_files": [], "llm_call_count": 1, "total_llm_tokens": 10, "telemetry": None,
             "elapsed_ms": 1.0, "wall_elapsed_ms": 1.0, "sleep_suspected": False,
             "answer_preview": "", "answer": "", "error": None}]
    monkeypatch.setattr(compare, "run", lambda *a, **k: rows)
    out = tmp_path / "rows.json"
    assert compare.main(["--json-out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == rows
