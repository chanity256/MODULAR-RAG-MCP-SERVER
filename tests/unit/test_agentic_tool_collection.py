"""AC2: agentic_search MCP tool 可选 collection 参数单元测试（离线，注入 fake）。

覆盖 AGENTIC_COLLECTION_ISOLATION_SPEC §4 AC2 六组：定界集发送、空定界集
短路（D3）+ ``scoped_empty`` trace、证据后置过滤、collection 非法 -32602、
``paths``/``collection`` 互斥 -32602（D5）、不传 collection 零历史查询（基线
逐字节一致由既有 ``test_agentic_tool.py`` 保证）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys_path_guard = str(Path(__file__).resolve().parents[2] / "src")

sys.path.insert(0, sys_path_guard)

from libs.agentic_search.base_agentic_search import AgenticEvidence
from libs.agentic_search.fake_backend import FakeAgenticSearch
from mcp_server.protocol_handler import ToolInvalidParams
from mcp_server.tools.agentic_search import AgenticSearchTool

from tests.unit.test_agentic_tool import _Settings, _config


class FakeHistory:
    """fake 历史表句柄：按 ``list_processed()`` 契约返回并记录查询次数。"""

    def __init__(self, records: list[dict]) -> None:
        self._records = records
        self.calls = 0

    def list_processed(self) -> list[dict]:
        self.calls += 1
        return list(self._records)


def _rec(source_path: str, collection: str, status: str = "success") -> dict:
    return {"source_path": source_path, "collection": collection, "status": status}


@pytest.fixture()
def env(tmp_path: Path) -> dict:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("alpha", encoding="utf-8")
    (docs / "b.md").write_text("beta", encoding="utf-8")
    return {
        "docs": docs,
        "a": str(docs / "a.md"),
        "b": str(docs / "b.md"),
        "log_file": str(tmp_path / "traces.jsonl"),
        "settings": _Settings(_config(allowed_roots=[str(docs)]), str(tmp_path / "traces.jsonl")),
    }


def _tool(env: dict, history: FakeHistory, evidences: list[AgenticEvidence] | None = None,
          source_files: list[str] | None = None) -> AgenticSearchTool:
    fake = FakeAgenticSearch(
        evidences=evidences if evidences is not None else [
            AgenticEvidence(source_path=env["a"], snippet="fake evidence snippet", score=0.9),
        ],
        source_files=source_files if source_files is not None else [env["a"]],
    )
    return AgenticSearchTool(search=fake, settings=env["settings"], history=history)


class TestScopedPaths:
    def test_collection_scopes_paths_to_history_records(self, env):
        """AC2-1：带 collection 时发送的 paths 恰为该 collection 定界集。"""
        history = FakeHistory([_rec(env["a"], "A"), _rec(env["b"], "B")])
        tool = _tool(env, history)
        tool({"query": "q", "collection": "A"})
        assert tool._search.last_request.paths == [str(Path(env["a"]).resolve())]
        tool({"query": "q", "collection": "B"})
        assert tool._search.last_request.paths == [str(Path(env["b"]).resolve())]


class TestEmptyScope:
    def test_empty_scope_short_circuits_without_backend_call(self, env):
        """AC2-2：空定界集不调后端、零证据结构化结果、trace 记 scoped_empty。"""
        history = FakeHistory([_rec(env["a"], "A", status="failed"), _rec(env["b"], "B")])
        tool = _tool(env, history)
        result = tool({"query": "q", "collection": "A"})
        assert tool._search.last_request is None  # 未调用 sidecar
        assert result["structuredContent"]["agentic"]["evidence_files"] == []
        assert result["structuredContent"]["citations"] == []
        assert "A" in result["content"][0]["text"]
        assert result.get("isError") is None  # 正常结果而非错误
        trace = json.loads(Path(env["log_file"]).read_text(encoding="utf-8").strip().splitlines()[-1])
        stage = next(s for s in trace["stages"] if s["stage"] == "agentic_search")
        assert stage["method"] == "scoped_empty"
        assert stage["details"]["collection"] == "A"
        assert stage["details"]["scoped_paths"] == 0


class TestEvidencePostFilter:
    def test_out_of_scope_but_in_allowlist_evidence_dropped(self, env, tmp_path):
        """AC2-3：白名单内但定界集外的证据与引用文件被剔除。"""
        outside = tmp_path / "outside.md"
        outside.write_text("leak", encoding="utf-8")
        evidences = [
            AgenticEvidence(source_path=env["a"], snippet="in scope", score=1.0),
            AgenticEvidence(source_path=env["b"], snippet="other collection", score=0.8),
            AgenticEvidence(source_path=str(outside), snippet="out of allowlist", score=0.7),
        ]
        source_files = [env["a"], env["b"], str(outside)]
        history = FakeHistory([_rec(env["a"], "A"), _rec(env["b"], "B")])
        tool = _tool(env, history, evidences=evidences, source_files=source_files)
        result = tool({"query": "q", "collection": "A"})
        sources = [c["source"] for c in result["structuredContent"]["citations"]]
        assert sources == [str(Path(env["a"]).resolve())]
        assert result["structuredContent"]["agentic"]["evidence_files"] == [
            str(Path(env["a"]).resolve())
        ]


class TestValidation:
    def test_invalid_collection_rejected(self, env):
        """AC2-4：collection 非空非纯白字符串校验（-32602），不发请求。"""
        history = FakeHistory([])
        tool = _tool(env, history)
        for bad in ("", "   ", 42, [], {}):
            with pytest.raises(ToolInvalidParams, match="collection"):
                tool({"query": "q", "collection": bad})
        assert tool._search.last_request is None

    def test_paths_and_collection_mutually_exclusive(self, env):
        """AC2-5：paths 与 collection 同时给出 → -32602 且不调用后端（D5）。"""
        history = FakeHistory([_rec(env["a"], "A")])
        tool = _tool(env, history)
        with pytest.raises(ToolInvalidParams, match="mutually exclusive"):
            tool({"query": "q", "paths": [env["docs"]], "collection": "A"})
        assert tool._search.last_request is None
        assert history.calls == 0  # 互斥校验先于历史表查询

    def test_no_collection_queries_history_never(self, env):
        """AC2-6：不传 collection 时不查历史表、行为与基线一致。"""
        history = FakeHistory([_rec(env["b"], "B")])
        tool = _tool(env, history)
        tool({"query": "q"})
        assert history.calls == 0
        assert tool._search.last_request.paths == [str(env["docs"].resolve())]
