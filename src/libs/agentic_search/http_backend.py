"""Sirchmunk HTTP sidecar 后端 — 阶段 S1.4。

同步 httpx 调用独立进程 ``sirchmunk serve`` 的 ``POST /api/v1/search``
（Bearer 鉴权 + ``response_format=context``，spec 3.2）。主 venv 永不
import sirchmunk——所有上游耦合收敛在本文件。

错误映射（spec S1.4）：
- ``httpx.TimeoutException`` → :class:`AgenticSearchTimeoutError`
- ``httpx.ConnectError`` 等传输错误 → :class:`AgenticSearchBackendError`
- HTTP 4xx/5xx / ``success=false`` → :class:`AgenticSearchBackendError`
- 响应非 JSON / 结构畸形 → :class:`AgenticSearchResponseError`

流式（SX5）：:meth:`SirchmunkHttpBackend.search_stream` 消费
``POST /api/v1/search/stream`` 的 SSE 帧，错误映射同上，另加
``event: error`` / 无 result 帧断流 → :class:`AgenticSearchBackendError`、
总时长超 ``timeout_s`` → :class:`AgenticSearchTimeoutError`。仅 Dashboard
DEEP 使用，MCP tool 保持同步。

测试：``transport`` 可注入（``httpx.MockTransport`` 离线单测）；真实
sidecar 冒烟走 ``RAG_AGENTIC_INTEGRATION=1`` 门控（S1.7）。
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Iterable

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from libs.agentic_search.base_agentic_search import (
    AgenticLogCallback,
    AgenticSearchBackendError,
    AgenticSearchRequest,
    AgenticSearchResponse,
    AgenticSearchResponseError,
    AgenticSearchTimeoutError,
    BaseAgenticSearch,
)
from libs.agentic_search.agentic_search_factory import AgenticSearchFactory
from libs.agentic_search.converter import parse_search_context

__all__ = ["SirchmunkHttpBackend"]


# Note: 为何走 HTTP sidecar 而非进程内 import / 官方 MCP / CLI 子进程 — 见 .agents/notes/implemented/architecture/2026-10-05-sirchmunk-http-sidecar.md
@AgenticSearchFactory.register("sirchmunk_http")
class SirchmunkHttpBackend(BaseAgenticSearch):
    """通过 HTTP 调用 sirchmunk sidecar 的同步后端。"""

    def __init__(self, config: Any, *, transport: Any | None = None) -> None:
        """Args:
            config: ``AgenticSearchConfig``（base_url/api_token/timeout_s/
                top_k_files/max_loops/max_token_budget 驱动请求）。
            transport: 可注入 httpx transport（测试 MockTransport 用）。
        """
        self._config = config

        def _transport_factory() -> Any:
            import httpx

            return httpx.HTTPTransport() if transport is None else transport

        self._transport_factory = _transport_factory

    @property
    def backend_name(self) -> str:
        return "sirchmunk_http"

    def search(self, request: AgenticSearchRequest) -> AgenticSearchResponse:
        """执行一次 sidecar 检索（同步、可超时、错误分型）。"""
        import httpx

        started = time.perf_counter()
        payload = self._build_payload(request)
        headers = {"Authorization": f"Bearer {self._config.api_token}"} if self._config.api_token else {}
        with httpx.Client(
            transport=self._transport_factory(),
            timeout=self._config.timeout_s,
        ) as client:
            try:
                resp = client.post(
                    f"{self._config.base_url.rstrip('/')}/api/v1/search",
                    json=payload,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                raise AgenticSearchTimeoutError(
                    f"sidecar timeout after {self._config.timeout_s}s: {exc}"
                ) from exc
            except httpx.HTTPError as exc:
                raise AgenticSearchBackendError(f"sidecar unreachable: {exc}") from exc

        if resp.status_code >= 400:
            raise AgenticSearchBackendError(
                f"sidecar returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise AgenticSearchResponseError(f"sidecar returned non-JSON body: {exc}") from exc
        if not isinstance(body, dict) or body.get("success") is not True:
            error = body.get("error", "unknown error") if isinstance(body, dict) else body
            raise AgenticSearchBackendError(f"sidecar reported failure: {error}")

        response = parse_search_context(body.get("data"), mode=request.mode)
        response.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return response

    def search_stream(
        self,
        request: AgenticSearchRequest,
        on_event: AgenticLogCallback,
    ) -> AgenticSearchResponse:
        """经 ``POST /api/v1/search/stream`` 流式检索（SX5）。

        帧格式（上游 ``api/search.py`` ``_sse_event`` / ``_sse_generator``）：
        ``event: log`` → ``on_event``；``event: result`` → 与同步端点同形的
        ``{"success", "data"}``，复用 :func:`parse_search_context`；
        ``event: error`` → :class:`AgenticSearchBackendError`；``: heartbeat``
        注释行忽略。payload / 鉴权 / 错误映射与 :meth:`search` 一致。
        """
        import httpx

        started = time.perf_counter()
        payload = self._build_payload(request)
        headers = {"Authorization": f"Bearer {self._config.api_token}"} if self._config.api_token else {}
        timeout_s = self._config.timeout_s
        # httpx 的 timeout 只约束单次读，而上游每 0.5s 发一次 heartbeat，读超时
        # 永不触发；另设总时限对齐同步端点「整次请求 timeout_s」的语义。断开连接
        # 时上游会 task.cancel()（api/search.py:419-420），不再空耗 LLM 成本。
        deadline = started + timeout_s if timeout_s else None
        with httpx.Client(transport=self._transport_factory(), timeout=timeout_s) as client:
            try:
                with client.stream(
                    "POST",
                    f"{self._config.base_url.rstrip('/')}/api/v1/search/stream",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        raise AgenticSearchBackendError(
                            f"sidecar returned HTTP {resp.status_code}: {resp.text[:200]}"
                        )
                    body = self._consume_sse(resp.iter_lines(), on_event, deadline)
            except httpx.TimeoutException as exc:
                raise AgenticSearchTimeoutError(
                    f"sidecar timeout after {timeout_s}s: {exc}"
                ) from exc
            except httpx.HTTPError as exc:
                raise AgenticSearchBackendError(f"sidecar unreachable: {exc}") from exc

        if not isinstance(body, dict) or body.get("success") is not True:
            error = body.get("error", "unknown error") if isinstance(body, dict) else body
            raise AgenticSearchBackendError(f"sidecar reported failure: {error}")
        response = parse_search_context(body.get("data"), mode=request.mode)
        response.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return response

    def _consume_sse(
        self,
        lines: Iterable[str],
        on_event: AgenticLogCallback,
        deadline: float | None,
    ) -> Any:
        """逐行解析 SSE，回调 log 帧，返回 result 帧的 JSON body。"""
        relay = _LogRelay(on_event)
        event_type: str | None = None
        data_lines: list[str] = []
        for line in lines:
            if deadline is not None and time.perf_counter() > deadline:
                raise AgenticSearchTimeoutError(
                    f"sidecar stream exceeded {self._config.timeout_s}s without a result"
                )
            if line == "":
                if event_type is None and not data_lines:
                    continue
                result = self._dispatch_frame(event_type or "message", "\n".join(data_lines), relay)
                event_type, data_lines = None, []
                if result is not None:
                    relay.flush()
                    return result[0]
                continue
            if line.startswith(":"):
                continue  # 注释行（上游 ": heartbeat" 保活）
            field_name, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field_name == "event":
                event_type = value
            elif field_name == "data":
                data_lines.append(value)
        relay.flush()
        # 按 SSE 规范，末尾未以空行结束的残帧丢弃；无 result 即视为上游异常中断。
        raise AgenticSearchBackendError("sidecar stream ended without a result frame")

    @staticmethod
    def _dispatch_frame(event_type: str, data: str, relay: "_LogRelay") -> tuple[Any] | None:
        """处理一帧；仅 result 帧返回 ``(body,)``（包一层以区分 ``data: null``）。"""
        if event_type == "log":
            try:
                item = json.loads(data)
            except json.JSONDecodeError:
                return None  # 进度日志畸形不影响检索本身，跳过
            if isinstance(item, dict):
                relay.feed(item)
            return None
        if event_type == "error":
            relay.flush()
            try:
                item = json.loads(data)
            except json.JSONDecodeError:
                item = None
            message = item.get("error") if isinstance(item, dict) else None
            raise AgenticSearchBackendError(f"sidecar reported failure: {message or data or 'unknown error'}")
        if event_type == "result":
            try:
                return (json.loads(data),)
            except json.JSONDecodeError as exc:
                raise AgenticSearchResponseError(f"sidecar returned non-JSON result frame: {exc}") from exc
        return None  # 未知事件类型：前向兼容，忽略

    def _build_payload(self, request: AgenticSearchRequest) -> dict[str, Any]:
        cfg = self._config
        payload: dict[str, Any] = {
            "query": request.query,
            "mode": request.mode,
            "response_format": "context",
            # 恒发送绝对路径（PathGuard 已校验）；sidecar 相对路径按其
            # CWD 解析，绝不依赖（spec 4.6）。
            "paths": list(request.paths) if request.paths is not None else None,
            "top_k_files": request.top_k_files if request.top_k_files is not None else cfg.top_k_files,
            "max_loops": request.max_loops if request.max_loops is not None else cfg.max_loops,
            "max_token_budget": (
                request.max_token_budget
                if request.max_token_budget is not None
                else cfg.max_token_budget
            ),
            "enable_dir_scan": (
                request.enable_dir_scan
                if request.enable_dir_scan is not None
                else getattr(cfg, "enable_dir_scan", True)
            ),
            # 显式锁定而非依赖上游默认：无证据时让 LLM 凭自身知识作答会破坏
            # 「引用必须可溯源」（SX 2.2 Out of Scope），上游默认值变化也不受影响。
            "llm_fallback": False,
        }
        max_depth = request.max_depth if request.max_depth is not None else getattr(cfg, "max_depth", None)
        if max_depth is not None:
            payload["max_depth"] = max_depth
        if request.include is not None:
            payload["include_patterns"] = list(request.include)
        if request.exclude is not None:
            payload["exclude_patterns"] = list(request.exclude)
        return payload


class _LogRelay:
    """把上游 log 帧转成 ``on_event(level, message, timestamp)`` 回调。

    上游 LLM 流式输出逐 token 以 ``flush=True`` 发帧，且 ``end`` 参数在
    ``_log_callback`` 中被丢弃（``api/search.py:380-386``）——原样透传会让
    调用方收到成百上千条碎片。故 ``flush=True`` 碎片先缓冲，遇换行、空串
    结束符（``openai_chat.py`` 流尾 ``info("", end="\\n", flush=True)``）、
    下一条普通日志或流结束时拼成一行再回调。
    """

    def __init__(self, on_event: AgenticLogCallback) -> None:
        self._on_event = on_event
        self._parts: list[str] = []
        self._level = "info"
        self._timestamp: float | None = None

    def feed(self, item: dict[str, Any]) -> None:
        level = str(item.get("level") or "info")
        message = item.get("message")
        message = message if isinstance(message, str) else ("" if message is None else str(message))
        raw_ts = item.get("timestamp")
        timestamp = float(raw_ts) if isinstance(raw_ts, (int, float)) and not isinstance(raw_ts, bool) else None
        if item.get("flush") is True:
            if not self._parts:
                self._level, self._timestamp = level, timestamp
            self._parts.append(message)
            if message == "" or "\n" in message:
                self.flush()
            return
        self.flush()
        self._on_event(level, message, timestamp)

    def flush(self) -> None:
        if not self._parts:
            return
        text = "".join(self._parts).strip("\n")
        self._parts = []
        if text.strip():
            self._on_event(self._level, text, self._timestamp)
