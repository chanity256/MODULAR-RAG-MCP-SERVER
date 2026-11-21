# Plan: [AC2] MCP agentic_search 可选 collection 参数

Status: implemented

> 实测（2026-11-21）：AC2 六组测试 + 既有 tool 用例 29/29 通过；全量禁网
> pytest 2988 passed / 23 skipped / 1 xfailed；红线脚本通过；Fix-Rounds: 0。
> 实现与计划一致，无偏离。

## Files
- `src/mcp_server/tools/agentic_search.py` — schema + 校验 + 定界/短路/后置过滤
- `tests/unit/test_agentic_tool_collection.py` — 新文件（既有 `test_agentic_tool.py` 零改动）

## Order
1. `TOOL_INPUT_SCHEMA` 增加可选 `collection`（string，描述互斥语义）。
2. `AgenticSearchTool.__init__` 增加可注入 `history`（缺省懒构造
   `SQLiteIntegrityChecker()`）；新增 `_resolve_collection_paths(collection)`
   用 AC1 `CollectionPathResolver(history, path_guard)` 推导定界集。
3. `_validate_arguments` 返回值扩展为六元组（query/mode/paths/top_k/collection/scoped_paths）：
   - `collection` 非空非纯白字符串校验（非法 → `ToolInvalidParams` → -32602）；
   - `arguments["paths"] is not None` 且 collection 给定 → 互斥 -32602（D5，
     校验在 paths 缺省回退 allowed_roots **之前**判定）；
   - collection 给定时跳过「paths 缺省 allowed_roots + validate_paths」基线分支，
     改为 scoped = resolver.resolve(collection)（spec §3：空集在 validate_paths
     之前短路）。
4. `run()`：collection 给定且 scoped 为空 → `_scoped_empty_result`（不建 request、
   不调 backend；trace 记 `method="scoped_empty"`、details 含 collection 与
   scoped_paths: 0；owns_trace 时照常 finish + persist）；否则 request.paths =
   scoped_paths，`_build_result(..., scoped_paths=...)`。
5. `_build_result` 增加可选 `scoped_paths`：既有 `filter_evidence_paths` 归一之后，
   证据与 `source_files` 再按 `∈ set(scoped_paths)` 后置过滤（防御 sidecar 经
   file_read 返回白名单内、定界集外的文件）。scoped_paths=None 时逐字节走基线。
6. 新增测试（六组，见 Verification）。

## Risks
- **基线逐字节一致**（spec §4 AC2-6 / 既有测试语义）：不传 collection 时不构建
  resolver、不查历史表、schema 之外无任何行为差异；`_build_result` 默认参数保证
  既有调用形态不变。
- **stdout 纪律**：tool 层不打印；日志走 logging（stderr）。
- **PathGuard 契约**：只消费 `validate_paths`（经 AC1 组件），不改其方法。
- 历史句柄鸭子类型：`SQLiteIntegrityChecker.list_processed()` 原生满足 AC1 协议，
  不改 `libs/loader/`。

## Verification
- `pytest tests/unit/test_agentic_tool_collection.py tests/unit/test_agentic_tool.py -q`
  全绿（新六组：定界集发送 / 空集短路+trace / 证据后置过滤 / collection 非法
  -32602 / paths+collection 互斥 -32602 且不调后端 / 不传 collection 零历史查询）。
- `pytest tests -o addopts="" -q`（CI 等价禁网）全绿；`python scripts/check_redlines.py` 通过。
- 既有 `test_agentic_tool.py`（含 `test_default_paths_from_allowed_roots`）零改动通过。

## Deviations
（实现偏离时回填）
