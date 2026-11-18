# Plan: [AR1] 白名单追加 ./data/uploads + ensure_local_roots 入口预建

Status: implemented

## Deviations

- 无设计偏离。一处测试编写 bug：入口用例最初用配置中全部根（含规格明确不预建的 `data/` 外根）构造 PathGuard 断言，被 PathGuard 既有 fail-closed 契约正确拒绝；经用户确认后修为只用实际上传根构造。Fix-Rounds: 1。

## Files

1. `src/libs/agentic_search/local_roots.py`（新建）— `ensure_local_roots(agentic, base=None)`
2. `src/libs/agentic_search/__init__.py` — 导出 `ensure_local_roots`
3. `config/settings.yaml` — `allowed_roots` 追加 `- ./data/uploads`（含外发提示注释）
4. `scripts/start_api.py` — `main()` 加载配置后调用
5. `src/mcp_server/server.py` — `main()` 加载配置后调用（`build_default_protocol_handler` 不动）
6. `scripts/query.py` — `--agentic` 且 enabled 分支调用
7. `tests/unit/test_agentic_local_roots.py`（新建）— §4 的 ensure_local_roots / PathGuard 双根 / 入口三组离线测试

## Order

先建 `local_roots.py`（纯新增），再接三个入口，再改配置，最后写测试。配置最后改：入口接好之前把 uploads 加进白名单会让本机 agentic 立即依赖一个可能不存在的目录。

## Design

- `ensure_local_roots(agentic, base=None)`：`base` 缺省 `Path.cwd() / "data"`（可注入供测试）。`enabled` 为 false 或 `allowed_roots` 为空直接返回 `[]`。
- 每个根按 PathGuard 同款解析（`Path(raw).expanduser().resolve()`）；仅当 resolved 位于 `base` 之下（或等于 base）、且字符串不命中 `denied_substrings` 时 `mkdir(parents=True, exist_ok=True)`；OSError 记 warning 不抛（logging 默认走 stderr，符合 MCP stdout 纪律）。
- 返回本次实际创建的路径列表（测试断言用）。
- PathGuard 与配置模型不改；`create_app` 不碰（零 IO 不变）。

## Risks

- **PathGuard 既有语义**：不触碰 `path_guard.py`，`test_agentic_path_guard.py` 用例必须原样通过。
- **MCP stdout**：入口新增调用只用 logging（stderr），不动 stdout。
- **入口测试拉起真实 main()**：`mcp_server.main` 用 `monkeypatch.chdir` + 临时 `config/settings.yaml`，stdin 空（立即 EOF），`setup_app_logging` 落盘均在临时 CWD 下，离线。
- **start_api 的调用点**：放 `try` 内 `create_app` 之前，`ConfigError` 既有处理不变；ensure 自身不抛。

## Verification

- `pytest tests/unit/test_agentic_local_roots.py -q`（新增，预期 ~10 例全绿）
- `pytest tests/unit/test_agentic_path_guard.py tests/unit/test_agentic_tool.py tests/unit/test_query_agentic.py -q`（既有语义不破坏）
- `python scripts/check_redlines.py`
- Fix-Rounds 记录进 commit trailer。

## Deviations
