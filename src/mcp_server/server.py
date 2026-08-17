"""MCP Server 入口（Stdio Transport）— 阶段 E1。

约束（对齐 DEV_SPEC 3.2.2）：
- ``stdout`` 仅输出合法 MCP JSON-RPC 消息（每行一个 JSON 对象）。
- 所有日志/调试信息一律走 ``stderr``（复用 ``observability.logger``）。

阶段 E1 仅保证：启动 server 能完成 ``initialize`` 握手、
``notifications/initialized`` 静默接收、stdin EOF 优雅退出。
完整协议解析（``tools/list`` / ``tools/call`` / 错误码规范）在 E2 的
``protocol_handler.py`` 中实现，本模块预留 ``message_handler`` 注入点。
"""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, Callable, TextIO

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from observability.logger import get_logger

# 跟踪 MCP 最新稳定协议版本（DEV_SPEC 3.2.3）。
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "modular-rag-mcp-server"
SERVER_VERSION = "0.1.0"

# JSON-RPC 2.0 错误码
PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601

# E2 将注入的完整消息处理器：dict -> dict | None（None 表示无需响应的通知）。
MessageHandler = Callable[[dict], "dict | None"]


def default_message_handler(message: dict) -> dict | None:
    """E1 最小消息处理：initialize 握手 + 通知静默 + 其余方法占位报错。

    E2 的 ``ProtocolHandler`` 落地后经 ``MCPServer(message_handler=...)``
    替换本实现。
    """
    method = message.get("method")
    msg_id = message.get("id")
    is_request = msg_id is not None

    if method == "initialize" and is_request:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    # 能力协商：声明支持 tools（具体 tool schema 由 E2/E3 补齐）。
                    "tools": {}
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if not is_request:
        # 通知（如 notifications/initialized）不产生响应。
        return None

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": METHOD_NOT_FOUND,
            "message": f"Method not found: {method!r} (E2 ProtocolHandler 尚未接入)",
        },
    }


class MCPServer:
    """MCP Stdio Server。

    Args:
        message_handler: 单条消息处理器，返回响应 dict 或 None（通知）。
            缺省使用 E1 的最小实现（仅 initialize）。
        logger: 注入 logger（缺省 stderr logger）。
    """

    def __init__(
        self,
        message_handler: MessageHandler | None = None,
        logger: Any = None,
    ) -> None:
        self.message_handler = message_handler or default_message_handler
        self.logger = logger or get_logger("mcp_server")

    def serve(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
        """运行 stdio 主循环，直到 stdin EOF 或 ``shutdown`` 请求。

        Returns:
            进程退出码（0=正常退出）。
        """
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        self.logger.info(
            "MCP server starting (stdio): name=%s version=%s protocol=%s",
            SERVER_NAME,
            SERVER_VERSION,
            PROTOCOL_VERSION,
        )
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            response = self.process_line(line)
            if response is _SHUTDOWN:
                self.logger.info("MCP server shutting down")
                break
            if response is not None:
                # stdout 只写 JSON-RPC 消息，每行一个 JSON 对象。
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
        self.logger.info("MCP server stopped")
        return 0

    def process_line(self, line: str) -> Any:
        """处理单行输入，返回响应 dict / None（通知或无需响应）/ ``_SHUTDOWN``。"""
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            self.logger.warning("Invalid JSON on stdin: %s", exc)
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": PARSE_ERROR, "message": f"Parse error: {exc}"},
            }
        if not isinstance(message, dict):
            self.logger.warning("Non-object JSON-RPC message ignored")
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": PARSE_ERROR, "message": "Message must be a JSON object"},
            }
        if message.get("method") == "shutdown":
            return _SHUTDOWN
        try:
            response = self.message_handler(message)
        except Exception as exc:  # handler 异常不得污染 stdout 通道
            self.logger.exception("Message handler failed")
            if message.get("id") is None:
                return None
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32603, "message": "Internal error"},
            }
        self.logger.debug("Handled method=%s", message.get("method"))
        return response


class _ShutdownSentinel:
    """内部哨兵：收到 shutdown 请求时优雅退出主循环。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<shutdown>"


_SHUTDOWN = _ShutdownSentinel()


def main(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """模块级入口：``python -m mcp_server.server``。"""
    return MCPServer().serve(stdin=stdin, stdout=stdout)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
