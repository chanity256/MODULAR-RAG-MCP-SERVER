"""SX5.1: search_stream 基类默认 + HTTP 后端 SSE 解析单元测试（罐头帧，离线）。

帧格式按上游 ``sirchmunk/api/search.py``（b314e11）``_sse_event`` /
``_sse_generator`` 复刻：``event: <type>\\ndata: <json>\\n\\n``，保活为
``: heartbeat\\n\\n``。
"""

from __future__ import annotations

import json

import httpx
import pytest

from libs.agentic_search.base_agentic_search import (
    AgenticSearchBackendError,
    AgenticSearchRequest,
    AgenticSearchResponse,
    AgenticSearchResponseError,
    AgenticSearchTimeoutError,
    BaseAgenticSearch,
)
from libs.agentic_search.fake_backend import DEFAULT_STREAM_EVENTS, FakeAgenticSearch
from libs.agentic_search.http_backend import SirchmunkHttpBackend


class _Config:
    base_url = "http://127.0.0.1:8584"
    api_token = "tok-1"
    timeout_s = 5.0
    top_k_files = 5
    max_loops = 6
    max_token_budget = 32000


_RESULT_DATA = {
    "answer": "deep answer",
    "cluster": {
        "confidence": 0.7,
        "search_results": ["/tmp/x/a.md"],
        "evidences": [
            {"file_or_url": "/tmp/x/a.md", "snippets": [{"snippet": "hit", "start": 3, "end": 6}]}
        ],
    },
    "llm_usages": [{"total_tokens": 9}],
    "total_llm_tokens": 9,
    # SX1 telemetry 合并在 data 顶层（上游 to_dict 逐键 setdefault）。
    "loop_count": 2,
    "zero_llm_path": False,
}


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _log(message: str, *, level: str = "info", ts: float = 1.5, flush: bool = False) -> str:
    return _frame("log", {"level": level, "message": message, "timestamp": ts, "flush": flush})


def _result(data: dict | None = None) -> str:
    return _frame("result", {"success": True, "data": data if data is not None else _RESULT_DATA})


def _stream_handler(body: str, captured: dict | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            status,
            content=body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    return handler


def _backend(handler, config=_Config) -> SirchmunkHttpBackend:
    return SirchmunkHttpBackend(config, transport=httpx.MockTransport(handler))


def _request(**overrides) -> AgenticSearchRequest:
    defaults = dict(query="q", mode="DEEP", paths=["/tmp/x"])
    defaults.update(overrides)
    return AgenticSearchRequest(**defaults)


class _Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, float | None]] = []

    def __call__(self, level: str, message: str, timestamp: float | None) -> None:
        self.items.append((level, message, timestamp))


# --------------------------------------------------------------------- #
# 基类默认 / Fake 后端
# --------------------------------------------------------------------- #


class _SyncOnly(BaseAgenticSearch):
    def search(self, request):  # pragma: no cover - 不会被调用
        raise AssertionError


def test_base_search_stream_default_not_implemented():
    with pytest.raises(NotImplementedError):
        _SyncOnly().search_stream(_request(), lambda *a: None)


def test_fake_search_stream_emits_events_then_response():
    events = _Events()
    fake = FakeAgenticSearch()
    response = fake.search_stream(_request(), events)
    assert events.items == list(DEFAULT_STREAM_EVENTS)
    assert response.answer == "fake agentic answer" and response.mode == "DEEP"
    assert fake.stream_calls == 1


def test_fake_search_stream_unsupported_and_error():
    with pytest.raises(NotImplementedError):
        FakeAgenticSearch(supports_stream=False).search_stream(_request(), lambda *a: None)
    events = _Events()
    with pytest.raises(AgenticSearchBackendError):
        FakeAgenticSearch(error=AgenticSearchBackendError("down")).search_stream(_request(), events)
    assert len(events.items) == len(DEFAULT_STREAM_EVENTS)


# --------------------------------------------------------------------- #
# 请求形态：端点 / 鉴权 / payload 与同步完全一致
# --------------------------------------------------------------------- #


def test_stream_request_shape_matches_sync_payload():
    captured: dict = {}
    backend = _backend(_stream_handler(_result(), captured))
    request = _request(include=["*.md"], exclude=["*.log"], max_depth=3, enable_dir_scan=False)
    backend.search_stream(request, _Events())

    assert captured["url"].endswith("/api/v1/search/stream")
    assert captured["headers"].get("authorization") == "Bearer tok-1"
    assert captured["payload"] == backend._build_payload(request)
    assert captured["payload"]["llm_fallback"] is False
    assert captured["payload"]["include_patterns"] == ["*.md"]
    assert captured["payload"]["exclude_patterns"] == ["*.log"]


def test_stream_no_auth_header_without_token():
    class NoToken(_Config):
        api_token = ""

    captured: dict = {}
    _backend(_stream_handler(_result(), captured), config=NoToken).search_stream(_request(), _Events())
    assert "authorization" not in captured["headers"]


# --------------------------------------------------------------------- #
# 帧解析
# --------------------------------------------------------------------- #


def test_log_heartbeat_and_result_frames():
    body = ": heartbeat\n\n" + _log("step 1") + ": heartbeat\n\n" + _log("step 2", level="warning", ts=2.0) + _result()
    events = _Events()
    response = _backend(_stream_handler(body)).search_stream(_request(), events)

    assert events.items == [("info", "step 1", 1.5), ("warning", "step 2", 2.0)]
    assert isinstance(response, AgenticSearchResponse)
    assert response.answer == "deep answer"
    assert response.mode == "DEEP"
    assert response.source_files == ["/tmp/x/a.md"]
    assert response.evidences[0].snippet == "hit"
    assert response.total_llm_tokens == 9
    assert response.elapsed_ms is not None and response.elapsed_ms >= 0
    # SX1 telemetry 经 parse_search_context 一并生效。
    assert response.telemetry is not None
    assert response.telemetry["loop_count"] == 2
    assert response.telemetry["zero_llm_path"] is False


def test_result_matches_parse_search_context():
    from libs.agentic_search.converter import parse_search_context

    response = _backend(_stream_handler(_result())).search_stream(_request(), _Events())
    expected = parse_search_context(_RESULT_DATA, mode="DEEP")
    expected.elapsed_ms = response.elapsed_ms
    assert response.to_dict() == expected.to_dict()


def test_crlf_line_endings_parsed():
    body = (_log("crlf") + _result()).replace("\n", "\r\n")
    events = _Events()
    response = _backend(_stream_handler(body)).search_stream(_request(), events)
    assert events.items == [("info", "crlf", 1.5)]
    assert response.answer == "deep answer"


def test_flush_fragments_coalesced_into_one_line():
    body = (
        _log("[role=assistant] ", flush=True, ts=1.0)
        + _log("hel", flush=True, ts=1.1)
        + _log("lo", flush=True, ts=1.2)
        + _log("", flush=True, ts=1.3)
        + _log("next step", ts=2.0)
        + _log("dangling", flush=True, ts=3.0)
        + _result()
    )
    events = _Events()
    _backend(_stream_handler(body)).search_stream(_request(), events)
    assert events.items == [
        ("info", "[role=assistant] hello", 1.0),
        ("info", "next step", 2.0),
        ("info", "dangling", 3.0),
    ]


def test_malformed_log_frame_skipped():
    body = "event: log\ndata: {not json\n\n" + _frame("log", ["not", "a", "dict"]) + _log("ok") + _result()
    events = _Events()
    response = _backend(_stream_handler(body)).search_stream(_request(), events)
    assert events.items == [("info", "ok", 1.5)]
    assert response.answer == "deep answer"


def test_unknown_event_ignored():
    body = _frame("progress", {"pct": 50}) + _result()
    assert _backend(_stream_handler(body)).search_stream(_request(), _Events()).answer == "deep answer"


def test_error_frame_maps_backend_error():
    body = _log("starting") + _frame("error", {"error": "LLM_API_KEY is not configured."})
    events = _Events()
    with pytest.raises(AgenticSearchBackendError, match="LLM_API_KEY"):
        _backend(_stream_handler(body)).search_stream(_request(), events)
    assert events.items == [("info", "starting", 1.5)]


def test_error_frame_non_json_still_backend_error():
    with pytest.raises(AgenticSearchBackendError, match="boom"):
        _backend(_stream_handler("event: error\ndata: boom\n\n")).search_stream(_request(), _Events())


def test_stream_end_without_result_raises():
    body = _log("step 1") + ": heartbeat\n\n"
    with pytest.raises(AgenticSearchBackendError, match="without a result"):
        _backend(_stream_handler(body)).search_stream(_request(), _Events())


def test_truncated_result_frame_discarded():
    # 缺结尾空行的残帧按 SSE 规范丢弃 → 视为无 result。
    body = _result().rstrip("\n")
    with pytest.raises(AgenticSearchBackendError, match="without a result"):
        _backend(_stream_handler(body)).search_stream(_request(), _Events())


def test_empty_stream_raises():
    with pytest.raises(AgenticSearchBackendError):
        _backend(_stream_handler("")).search_stream(_request(), _Events())


def test_malformed_result_frame_maps_response_error():
    with pytest.raises(AgenticSearchResponseError):
        _backend(_stream_handler("event: result\ndata: {oops\n\n")).search_stream(_request(), _Events())


def test_result_missing_answer_maps_response_error():
    with pytest.raises(AgenticSearchResponseError):
        _backend(_stream_handler(_result({"cluster": None}))).search_stream(_request(), _Events())


def test_result_success_false_maps_backend_error():
    body = _frame("result", {"success": False, "error": "nope"})
    with pytest.raises(AgenticSearchBackendError, match="nope"):
        _backend(_stream_handler(body)).search_stream(_request(), _Events())


# --------------------------------------------------------------------- #
# 传输层错误映射（同同步 search）
# --------------------------------------------------------------------- #


def test_http_error_status_maps_backend_error():
    with pytest.raises(AgenticSearchBackendError, match="HTTP 401"):
        _backend(_stream_handler('{"detail":"bad token"}', status=401)).search_stream(_request(), _Events())


def test_timeout_maps_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(AgenticSearchTimeoutError):
        _backend(handler).search_stream(_request(), _Events())


def test_connect_error_maps_backend_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AgenticSearchBackendError, match="unreachable"):
        _backend(handler).search_stream(_request(), _Events())


def test_total_deadline_enforced_despite_heartbeats(monkeypatch):
    # heartbeat 让 httpx 读超时永不触发；总时限必须由后端自行兜住。
    import libs.agentic_search.http_backend as http_backend

    clock = iter([0.0] + [float(i) for i in range(0, 100)])
    monkeypatch.setattr(http_backend.time, "perf_counter", lambda: next(clock))
    body = ": heartbeat\n\n" * 50 + _result()
    with pytest.raises(AgenticSearchTimeoutError, match="exceeded"):
        _backend(_stream_handler(body)).search_stream(_request(), _Events())
