"""MCP Protocol Handler — 阶段 E2。

封装 JSON-RPC 2.0 协议解析与能力协商（对齐 DEV_SPEC 3.2）：
- ``initialize``：返回 serverInfo + capabilities.tools
- ``tools/list``：返回已注册 tools 的 schema（name/description/inputSchema）
- ``tools/call``：路由到具体 tool 执行，异常转换为 JSON-RPC error

错误码规范（JSON-RPC 2.0）：
- -32600 Invalid Request（非对象/缺 jsonrpc/缺 method/非通知却缺 id 语义破坏）
- -32601 Method not found
- -32602 Invalid params
- -32603 Internal error（不泄露堆栈）

Tools 由 E3-E5 注册（``register_tool``）；E2 阶段 tools/list 返回空列表属合法状态。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

# 跟踪 MCP 最新稳定协议版本（DEV_SPEC 3.2.3）。
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "modular-rag-mcp-server"
SERVER_VERSION = "0.1.0"

# JSON-RPC 2.0 错误码
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# 已知请求方法（通知除外）
SUPPORTED_METHODS = ("initialize", "tools/list", "tools/call")

ToolHandler = Callable[[dict], Any]


class ToolInvalidParams(ValueError):
    """Tool 参数校验失败：映射为 JSON-RPC -32602（而非 -32603）。

    Tool handler 主动抛出本异常时，``message`` 会原样进入错误响应
    （仅限参数描述，不得包含内部细节）；其他异常仍统一 -32603
    且不泄露消息文本。
    """


@dataclass
class ToolDefinition:
    """单个 MCP Tool 的声明式定义。"""

    name: str
    description: str
    input_schema: dict
    handler: ToolHandler

    def to_list_entry(self) -> dict:
        """tools/list 返回的 schema 条目（对齐 MCP Tool 结构）。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


class ProtocolHandler:
    """JSON-RPC 2.0 消息解析 + MCP 核心方法路由。"""

    def __init__(
        self,
        tools: list[ToolDefinition] | None = None,
        server_name: str = SERVER_NAME,
        server_version: str = SERVER_VERSION,
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools or []:
            self.register_tool(tool)
        self.server_name = server_name
        self.server_version = server_version

    # ------------------------------------------------------------------ #
    # Tool 注册（供 E3-E5 的 tools 模块调用）
    # ------------------------------------------------------------------ #
    def register_tool(
        self,
        name_or_tool: "str | ToolDefinition",
        description: str | None = None,
        input_schema: dict | None = None,
        handler: ToolHandler | None = None,
    ) -> None:
        """注册 tool：既接受 ``ToolDefinition`` 也接受逐字段参数。"""
        if isinstance(name_or_tool, ToolDefinition):
            tool = name_or_tool
        else:
            if not isinstance(name_or_tool, str) or not name_or_tool:
                raise ValueError("tool name must be a non-empty string")
            if not callable(handler):
                raise TypeError(f"handler must be callable for tool {name_or_tool!r}")
            tool = ToolDefinition(
                name=name_or_tool,
                description=description or "",
                input_schema=input_schema or {"type": "object", "properties": {}},
                handler=handler,
            )
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    # ------------------------------------------------------------------ #
    # 消息级入口（供 MCPServer 作为 message_handler 注入）
    # ------------------------------------------------------------------ #
    def handle_message(self, message: Any) -> "dict | None":
        """处理单条 JSON-RPC 消息，返回响应 dict 或 None（通知）。"""
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "Message must be a JSON object")
        if message.get("jsonrpc") != "2.0":
            return _error(
                message.get("id"), INVALID_REQUEST, "Missing or invalid 'jsonrpc' (expected '2.0')"
            )
        method = message.get("method")
        if not isinstance(method, str) or not method:
            return _error(message.get("id"), INVALID_REQUEST, "Missing 'method'")

        msg_id = message.get("id")
        is_request = "id" in message
        if not is_request:
            # 通知（如 notifications/initialized）不产生响应。
            return None

        params = message.get("params")
        if params is not None and not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "'params' must be an object")

        if method == "initialize":
            return _result(msg_id, self.handle_initialize(params or {}))
        if method == "tools/list":
            return _result(msg_id, self.handle_tools_list())
        if method == "tools/call":
            name = (params or {}).get("name")
            arguments = (params or {}).get("arguments", {})
            return self.handle_tools_call(msg_id, name, arguments)
        return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method!r}")

    # ------------------------------------------------------------------ #
    # 核心方法
    # ------------------------------------------------------------------ #
    def handle_initialize(self, params: dict | None = None) -> dict:
        """能力协商：声明 capabilities.tools + serverInfo。"""
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                # 能力协商：tools 已启用（listChanged=False，tools 静态注册）。
                "tools": {"listChanged": False}
            },
            "serverInfo": {"name": self.server_name, "version": self.server_version},
        }

    def handle_tools_list(self) -> dict:
        """返回已注册 tools 的 schema 列表。"""
        return {"tools": [tool.to_list_entry() for tool in self._tools.values()]}

    def handle_tools_call(self, msg_id: Any, name: Any, arguments: Any) -> dict:
        """路由到具体 tool 执行；异常转换为 JSON-RPC error（不泄露堆栈）。"""
        if not isinstance(name, str) or not name:
            return _error(msg_id, INVALID_PARAMS, "'params.name' must be a non-empty string")
        if name not in self._tools:
            return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name!r}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error(msg_id, INVALID_PARAMS, "'params.arguments' must be an object")

        try:
            output = self._tools[name].handler(arguments)
        except ToolInvalidParams as exc:
            return _error(msg_id, INVALID_PARAMS, str(exc) or "Invalid tool arguments")
        except Exception:
            # 内部异常统一 -32603，且消息中不包含堆栈/异常文本（防泄露）。
            return _error(msg_id, INTERNAL_ERROR, f"Tool execution failed: {name}")

        return _result(msg_id, self._wrap_tool_output(output))

    @staticmethod
    def _wrap_tool_output(output: Any) -> dict:
        """把 tool handler 输出包装为 MCP tool result（content 数组）。"""
        if isinstance(output, dict) and "content" in output:
            return output
        text = output if isinstance(output, str) else str(output)
        return {"content": [{"type": "text", "text": text}]}
