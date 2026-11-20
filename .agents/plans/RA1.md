# Plan: [RA1] 项目根解析 + MCP 默认配置路径锚定

Status: implemented

## Files

1. `src/core/project_root.py`（新建）— `resolve_project_root()`：`RAG_PROJECT_ROOT` 优先，否则 `Path(__file__).parents[2]` 推导；纯计算不做 IO。
2. `src/core/settings.py` — `load_settings` 默认配置路径改为惰性锚定（`path=None` 哨兵 → `resolve_project_root()/config/settings.yaml`）。
3. `tests/unit/test_project_root.py`（新建）— 解析器用例（默认推导 = 仓库根、env 覆盖、空串回退、CWD 无关）。
4. `tests/unit/test_config_loading.py` — 追加默认路径锚定用例 + 显式相对路径仍按 CWD 的 Out-of-Scope 锁定用例。
5. `tests/e2e/test_agentic_mcp.py` — server fixture 注入 `RAG_PROJECT_ROOT=<tmp workspace>`（原依赖 CWD 相对配置）。
6. `tests/e2e/test_mcp_client.py` — 同上（`server` fixture，cwd=tmp 处注释更新）。
7. `tests/e2e/test_markdown_vault_query.py` — 同上（`test_mcp_query_knowledge_hub_cites_note_path` 内子进程 env）。

## Order

先 `project_root.py`（无依赖）→ `settings.py` 接线 → 新增单测 → 改三个 e2e fixture（都在同一步跑测试前完成）。

## Risks

- **默认参数哨兵**：若把默认值写成 import 时求值的模块常量，测试内 `monkeypatch.setenv("RAG_PROJECT_ROOT")` 不生效（默认值已绑定）。故用 `path=None` 哨兵 + 函数内惰性解析；显式传入路径语义不变（仍按 CWD）。
- **e2e 子进程测试静默读真配置**：锚定后 cwd=tmp 的 MCP 子进程会读到仓库真实 `config/settings.yaml`（agentic enabled=true、真实 base_url）——`test_agentic_mcp.py` 会试图连真实 sidecar。三处 e2e fixture 必须注入 `RAG_PROJECT_ROOT`。
- **既有测试语义**：`main.py` 两例传显式绝对路径不受影响；`server.py` 读不到配置按禁用处理的契约不变（`load_settings()` 抛 `ConfigError` 路径不变，只是默认路径值变了）。
- 红线自查：不 import sirchmunk、无 stdout 输出、离线零网络。

## Verification

- 新增 `pytest tests/unit/test_project_root.py tests/unit/test_config_loading.py -q` 全绿。
- `pytest tests/e2e/test_agentic_mcp.py tests/e2e/test_mcp_client.py tests/e2e/test_markdown_vault_query.py -q`（离线，e2e MCP 子进程）全绿。
- 全量 `pytest tests -q` 全绿（基线 2609 passed / 11 skipped / 1 xfailed）。
- `python scripts/check_redlines.py` 通过。

## Deviations

（实现偏离时回填）
