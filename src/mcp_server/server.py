"""MCP Server 入口（Stdio Transport）— 阶段 E1。

约束（对齐 DEV_SPEC 3.2.2）：
- ``stdout`` 仅输出合法 MCP JSON-RPC 消息（每行一个 JSON 对象）。
- 所有日志/调试信息一律走 ``stderr``（复用 ``observability.logger``）。

阶段 E1 保证：启动 server 能完成 ``initialize`` 握手、通知静默接收、
stdin EOF 优雅退出。协议解析与方法路由（``initialize`` / ``tools/list`` /
``tools/call`` / 错误码规范）由 E2 的 ``protocol_handler.ProtocolHandler``
提供，本模块缺省将其作为 ``message_handler`` 接入。
"""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, Callable, TextIO

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from observability.logger import get_logger
from mcp_server.protocol_handler import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    ProtocolHandler,
)

# JSON-RPC 2.0 错误码
PARSE_ERROR = -32700

# E2+ 的完整消息处理器经 ``MCPServer(message_handler=...)`` 注入；
# 缺省使用 ``ProtocolHandler.handle_message``。
MessageHandler = Callable[[dict], "dict | None"]


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
        protocol_handler: ProtocolHandler | None = None,
    ) -> None:
        if message_handler is None:
            protocol_handler = protocol_handler or ProtocolHandler()
            message_handler = protocol_handler.handle_message
        self.message_handler = message_handler
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


def build_default_protocol_handler() -> ProtocolHandler:
    """构建注册了 E3+ tools 的缺省 ProtocolHandler。

    Tools 懒初始化（见 ``tools/query_knowledge_hub``），注册本身
    不触发网络/凭据依赖；后续 tool（E4/E5）在此追加注册。
    """
    from mcp_server.tools.query_knowledge_hub import create_query_knowledge_hub_definition

    handler = ProtocolHandler()
    handler.register_tool(create_query_knowledge_hub_definition())
    return handler


def main(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """模块级入口：``python -m mcp_server.server``。"""
    return MCPServer(protocol_handler=build_default_protocol_handler()).serve(
        stdin=stdin, stdout=stdout
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
