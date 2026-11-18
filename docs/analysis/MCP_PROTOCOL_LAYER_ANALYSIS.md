# MCP 协议层与官方 Python SDK 对比分析（MCP_PROTOCOL_LAYER_ANALYSIS）

> 版本：0.1 — 基于当前代码与协议规格的架构判断（2026-11-16）
>
> 基线：主规格 E 系列（MCP Server）冻结收口状态。性质：**分析文档，非可执行排期**；结论为「暂维持自研协议层」，后续若触发切换条件，落地前需另走 `docs/backlog/` 提案立项。
>
> 背景问题：项目 MCP 协议层（`src/mcp_server/`）为自研实现、未依赖官方 `mcp` Python SDK，与官方 SDK 的差距是否构成切换理由。

---

## 目录

- [1. 现状盘点：自研协议层实现了什么](#1-现状盘点自研协议层实现了什么)
- [2. 协议版本现状：没有 MCP 2.0](#2-协议版本现状没有-mcp-20)
- [3. 自研协议层 vs 官方 SDK 对比](#3-自研协议层-vs-官方-sdk-对比)
- [4. 切换判断与触发条件](#4-切换判断与触发条件)
- [5. 一句话总结](#5-一句话总结)

---

## 1. 现状盘点：自研协议层实现了什么

`src/mcp_server/` 全部自研，`pyproject.toml` 与 venv 均无 `mcp` SDK 依赖。构成：

| 文件 | 职责 |
|------|------|
| `server.py` | stdio 主循环：逐行读 stdin、分发消息处理器、写 stdout（只输出合法 JSON-RPC，日志走 stderr）；`shutdown` 请求与 stdin EOF 优雅退出 |
| `protocol_handler.py` | `PROTOCOL_VERSION = "2025-06-18"`；消息级校验（非对象 / 缺 `jsonrpc` / 非 `"2.0"` / 缺 `method` → `-32600`；`params` 非 object → `-32602`）；通知（无 `id` 键）静默返回 None；`id: null` 视为合法请求；`initialize`（能力协商 `capabilities.tools{listChanged:false}` + serverInfo）、`tools/list`、`tools/call` 三方法路由；未知请求方法 → `-32601`；tool handler 异常 → `-32603` 且错误消息仅含 tool 名不含异常文本（防泄露） |
| `tools/` | 三个已注册工具：`query_knowledge_hub` / `list_collections` / `get_document_summary`（`agentic_search` 仅在 `agentic_search.enabled` 时注册）；Tool 层参数校验、懒初始化、trace 记录 |

设计对齐 [01_DEV_SPEC_frozen.md](../frozen/01_DEV_SPEC_frozen.md) §3.2（E1/E2 交付记录见其任务表），`ProtocolHandler` 为吃 dict 返 dict 的纯逻辑类，不依赖 socket/进程，30 个单测直接覆盖。e2e 对 stdout 有逐行合法 JSON-RPC 断言。

## 2. 协议版本现状：没有 MCP 2.0

MCP 协议**没有 "2.0" 版本号**，官方按日期编号修订。版本线：

| 版本 | 说明 |
|------|------|
| `2024-11-05` | 首个正式版 |
| `2025-03-26` | 增加 Streamable HTTP 授权等 |
| **`2025-06-18`** | **本项目所用**（`protocol_handler.py` 常量，用于 `initialize` 握手协商） |
| `2025-11-25` | 最新批准版（2026-11 时点） |

注意区分两层：MCP 消息走 **JSON-RPC 2.0** 编码（项目内所有请求/响应校验 `"jsonrpc": "2.0"`），但这是底层消息格式，不是 MCP 协议版本。项目用的是倒数第二新的协议修订，落后最新一个周期；项目只用 tools 子集 + stdio，该面在两版之间无 breaking change，暂无升级压力。

## 3. 自研协议层 vs 官方 SDK 对比

| 维度 | 自研协议层 | 官方 `mcp` SDK |
|------|-----------|----------------|
| 传输 | 仅 stdio | stdio + Streamable HTTP + SSE |
| 协议面 | tools 三方法 | tools / resources / prompts / sampling / logging 全集，含进度通知、取消、ping 保活、`list_changed` 通知 |
| 版本跟进 | 手动对齐 spec 文本 | 随上游发布更新 |
| 依赖面 | 零新增依赖 | 拉入 pydantic v2、anyio、httpx 等异步栈 |
| 可测性 | dict 进 dict 出，纯单测（现状 30 用例） | 会话/传输层需更多集成式测试 |
| 注册方式 | `register_tool` 双风格手写 | `@mcp.tool` 装饰器，schema 自动推导 |
| 生态 | 无 | FastMCP 集成、client 双端、社区示例与工具链 |

### 自研的优势

1. **零依赖、供应链面小**：不引入 pydantic/anyio/httpx 异步栈，与项目「离线确定性测试」纪律天然契合，CI 依赖面不膨胀。
2. **完全可控、易锁死纪律**：stdout 纪律（只出合法 JSON-RPC）在自己掌握每个字节的实现里最可靠；错误消息防泄露等安全语义是显式实现并测试锁死的。
3. **协议面小而稳**：只用 tools 子集 + stdio，这是 MCP 最稳定的核心面，两版协议间无 breaking change，自研漂移风险低。
4. **纯逻辑可测**：`ProtocolHandler` 不碰进程与 socket，单测直覆盖；隔离干净（dict 进 dict 出），未来切换是有边界的改造。

### 自研的劣势

1. **功能覆盖窄**：无 resources / prompts / sampling、无协议级进度通知与取消（长耗时深度搜索目前只能靠 sidecar 日志转帧模拟进度，见 WS 规格做法）、无 ping 保活、无 `list_changed` 通知。
2. **仅 stdio**：无法以 HTTP 服务形式托管（多客户端共享、远程部署都做不了）；Web UI 走的是自己的 REST/WS 层而非 MCP。
3. **版本跟进是手动活**：MCP spec 每次修订需自行比对边界语义（如 `initialize` 版本协商规则），遗漏即隐性不合规。
4. **无生态红利**：schema 手写、无装饰器注册、无 client 实现可复用。

## 4. 切换判断与触发条件

**结论：当前不切换。** 理由：

- 实际用到的协议面（tools 三方法 + stdio）自研层已完整覆盖且有测试与 e2e 锁死，切换收益趋近于零；
- 切换成本实打实：重写 `mcp_server/`、重接 trace 与 stdout 纪律、迁移 30 单测 + e2e 逐行断言，并为异步栈重验禁网测试纪律；
- 落后一个协议版本对本项目无实际影响（见第 2 章）。

**触发条件**（命中任一再走 `docs/backlog/` 提案立项）：

1. 需要 **HTTP / 远程传输**：MCP server 要以共享服务或远程方式被多个客户端使用（注意与现状区分：Web UI 走 REST/WS，不算此需求）；
2. 需要**协议级长任务特性**：进度通知、取消、resources/prompts 等自研层不覆盖的能力成为真实需求；
3. **客户端生态强制升级协议版本**（如宿主要求 `2025-11-25`+），且自研跟进成本超过迁移成本。

届时迁移是低风险改造：`ProtocolHandler` 的 dict 进 dict 出边界意味着业务 tool 层无需改动，只换协议外壳。

## 5. 一句话总结

自研协议层以最小协议面换来了零依赖、强可控和好测试，在当前 tools + stdio 的使用形态下没有切换必要；真正该盯的是第 4 章的三个触发条件，命中任一再立项。
