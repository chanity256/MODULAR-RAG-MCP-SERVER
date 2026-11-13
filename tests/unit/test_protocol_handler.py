"""E2: Protocol Handler 协议解析与能力协商。"""

from __future__ import annotations

import pytest

from mcp_server.protocol_handler import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PROTOCOL_VERSION,
    SERVER_NAME,
    ToolDefinition,
    ProtocolHandler,
)


@pytest.fixture()
def handler() -> ProtocolHandler:
    return ProtocolHandler()


@pytest.fixture()
def handler_with_tool() -> ProtocolHandler:
    h = ProtocolHandler()
    h.register_tool(
        name_or_tool=ToolDefinition(
            name="echo",
            description="Echo the query",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=lambda args: f"echo: {args.get('query', '')}",
        )
    )
    return h


def _request(method: str, msg_id=1, params=None) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


# ------------------------------------------------------------------ #
# initialize / 能力协商
# ------------------------------------------------------------------ #
def test_initialize_returns_server_info_and_capabilities(handler):
    resp = handler.handle_message(_request("initialize"))
    result = resp["result"]
    assert resp["id"] == 1
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"] == {"name": SERVER_NAME, "version": "0.1.0"}


def test_initialize_ignores_client_params(handler):
    resp = handler.handle_message(
        _request("initialize", params={"protocolVersion": "2025-06-18", "capabilities": {}})
    )
    assert "result" in resp


def test_custom_server_info():
    h = ProtocolHandler(server_name="custom", server_version="9.9.9")
    result = h.handle_message(_request("initialize"))["result"]
    assert result["serverInfo"] == {"name": "custom", "version": "9.9.9"}


# ------------------------------------------------------------------ #
# tools/list
# ------------------------------------------------------------------ #
def test_tools_list_empty(handler):
    assert handler.handle_message(_request("tools/list"))["result"] == {"tools": []}


def test_tools_list_returns_registered_schema(handler_with_tool):
    result = handler_with_tool.handle_message(_request("tools/list"))["result"]
    assert len(result["tools"]) == 1
    tool = result["tools"][0]
    assert tool["name"] == "echo"
    assert tool["description"] == "Echo the query"
    assert tool["inputSchema"]["required"] == ["query"]


def test_register_tool_field_style_defaults(handler):
    handler.register_tool(name_or_tool="ping", handler=lambda args: "pong")
    tools = handler.handle_message(_request("tools/list"))["result"]["tools"]
    assert tools[0]["name"] == "ping"
    assert tools[0]["description"] == ""
    assert tools[0]["inputSchema"] == {"type": "object", "properties": {}}


def test_register_duplicate_tool_raises(handler):
    handler.register_tool(name_or_tool="t", handler=lambda args: None)
    with pytest.raises(ValueError, match="already registered"):
        handler.register_tool(name_or_tool="t", handler=lambda args: None)


@pytest.mark.parametrize("bad", [(-1, ""), ("x", None), ("y", 123)])
def test_register_tool_validation(handler, bad):
    name, fn = bad
    with pytest.raises((ValueError, TypeError)):
        handler.register_tool(name_or_tool=name, handler=fn)


# ------------------------------------------------------------------ #
# tools/call 路由
# ------------------------------------------------------------------ #
def _call(handler, name, arguments=None, msg_id=7):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return handler.handle_message(_request("tools/call", msg_id=msg_id, params=params))


def test_tools_call_routes_and_wraps_string_output(handler_with_tool):
    resp = _call(handler_with_tool, "echo", {"query": "hi"})
    assert resp == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"content": [{"type": "text", "text": "echo: hi"}]},
    }


def test_tools_call_passes_dict_output_through(handler_with_tool):
    handler_with_tool.register_tool(
        name_or_tool="structured",
        handler=lambda args: {"content": [{"type": "text", "text": "raw"}]},
    )
    resp = _call(handler_with_tool, "structured")
    assert resp["result"] == {"content": [{"type": "text", "text": "raw"}]}


def test_tools_call_default_arguments_empty_dict(handler_with_tool):
    resp = _call(handler_with_tool, "echo")
    assert resp["result"]["content"][0]["text"] == "echo: "


def test_tools_call_unknown_tool_invalid_params(handler_with_tool):
    resp = _call(handler_with_tool, "nope")
    assert resp["error"]["code"] == INVALID_PARAMS
    assert "nope" in resp["error"]["message"]


@pytest.mark.parametrize(
    "params",
    [
        {},  # 缺 name
        {"name": ""},  # 空 name
        {"name": 42},  # 非 str name
        {"name": "echo", "arguments": "not-a-dict"},  # 非 object arguments
    ],
)
def test_tools_call_invalid_params(handler_with_tool, params):
    resp = handler_with_tool.handle_message(_request("tools/call", params=params))
    assert resp["error"]["code"] == INVALID_PARAMS


def test_tools_call_internal_error_without_stack_leak():
    def boom(args):
        raise RuntimeError("secret-token /Users/whoami/private.py:42")

    h = ProtocolHandler()
    h.register_tool(name_or_tool="boom", handler=boom)
    resp = _call(h, "boom")
    assert resp["error"]["code"] == -32603
    assert "secret-token" not in resp["error"]["message"]
    assert "RuntimeError" not in resp["error"]["message"]


# ------------------------------------------------------------------ #
# 消息级校验 / 错误码
# ------------------------------------------------------------------ #
def test_non_object_message_invalid_request(handler):
    assert handler.handle_message(["not", "a", "dict"])["error"]["code"] == INVALID_REQUEST


@pytest.mark.parametrize(
    "message",
    [
        {"method": "initialize"},  # 缺 jsonrpc
        {"jsonrpc": "1.0", "id": 1, "method": "initialize"},  # 错版本
        {"jsonrpc": "2.0", "id": 1},  # 缺 method
        {"jsonrpc": "2.0", "id": 1, "method": 123},  # 非 str method
    ],
)
def test_invalid_request_shapes(handler, message):
    resp = handler.handle_message(message)
    assert resp["error"]["code"] == INVALID_REQUEST


def test_params_not_object_invalid_params(handler):
    resp = handler.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": [1, 2]}
    )
    assert resp["error"]["code"] == INVALID_PARAMS


def test_unknown_method_not_found(handler):
    resp = handler.handle_message(_request("resources/list"))
    assert resp["error"]["code"] == METHOD_NOT_FOUND
    assert "resources/list" in resp["error"]["message"]


@pytest.mark.parametrize(
    "notification",
    [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "anything"},
        {"jsonrpc": "2.0", "method": "bad-method"},  # 未知通知也静默（JSON-RPC 语义）
    ],
)
def test_notifications_return_none(handler, notification):
    assert handler.handle_message(notification) is None


def test_id_null_is_still_a_request(handler):
    # JSON-RPC 允许 id 为 null（仍视为请求）。
    resp = handler.handle_message({"jsonrpc": "2.0", "id": None, "method": "tools/list"})
    assert resp is not None and resp["id"] is None and "result" in resp


# ------------------------------------------------------------------ #
# 与 MCPServer 的接线
# ------------------------------------------------------------------ #
def test_mcp_server_defaults_to_protocol_handler():
    from mcp_server.server import MCPServer

    server = MCPServer()
    resp = server.process_line(
        '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
    )
    assert resp["result"] == {"tools": []}


def test_mcp_server_accepts_injected_protocol_handler():
    from mcp_server.server import MCPServer

    ph = ProtocolHandler()
    ph.register_tool(name_or_tool="ping", handler=lambda args: "pong")
    server = MCPServer(protocol_handler=ph)
    resp = server.process_line(
        '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ping"}}'
    )
    assert resp["result"]["content"][0]["text"] == "pong"
