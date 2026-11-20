# Plan: [RA2] agentic 白名单与预建按项目根

Status: implemented

## Files

1. `src/core/settings.py` — `_build_agentic_search` 内把相对 `allowed_roots` 按项目根锚定为绝对路径（D3-a 口径：Settings 字段交付即绝对；绝对路径原样保留、`~` 先 expanduser）；`resolve_project_root` 提升为模块级 import（RA1 的函数内惰性 import 一并简化）。
2. `src/libs/agentic_search/local_roots.py` — `ensure_local_roots` 默认基准 `Path.cwd()/data` → `resolve_project_root()/data`；docstring 同步。
3. `tests/unit/test_agentic_local_roots.py` — 改写 4 处锁定 CWD 语义的用例（已获用户批准，见 ROOT_ANCHOR_SPEC §4）：`test_default_base_is_cwd_data` → 项目根基准 + 新增「CWD 不新建 data/」锁定；3 个入口用例注入 `RAG_PROJECT_ROOT`（start_api 需显式 `--config` 指向 fixture 配置，原依赖真实仓库配置的相对根按 CWD 解析的偶然行为）。
4. `tests/unit/test_agentic_config.py` — 新增 3 个锚定用例（相对根锚定 / 绝对根原样 / `~` 展开）。

## Order

settings.py 锚定 → local_roots.py 基准 → 测试改写与新增 → 跑测。

## Risks

- **入口测试的偶然依赖**：`test_start_api_main_creates_uploads_root` 原先 `main([])` 读真实仓库配置、靠相对根按 CWD 解析才在 tmp 建目录；锚定后该路径不再成立，改用 `--config` + `RAG_PROJECT_ROOT` 的显式注入，语义与真实使用一致。
- **pytest cwd = 仓库根的等价性**：多数从仓库根加载配置的既有测试（如 `test_chat_auto_mode` 的相对根）锚定前后解析到同一路径，不受影响；chdir 到 tmp 的用例靠全量测试兜底排查。
- **PathGuard 契约不变**：相对输入按 CWD 解析（`test_agentic_path_guard.py:69` 锁定）不触碰；Settings 交付绝对根后 PathGuard 收到的恒为绝对路径。
- **denied_substrings 不变**：绝对化后的路径文本仍含 `data/db` 等子串，denied 过滤语义不变。
- 红线：不 import sirchmunk、stdout 无输出、离线确定性。

## Verification

- `pytest tests/unit/test_agentic_local_roots.py tests/unit/test_agentic_config.py tests/unit/test_agentic_tool.py tests/unit/test_agentic_path_guard.py -q` 全绿。
- 全量 `pytest tests -o addopts="" -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1` 全绿（基线 2952 passed / 23 skipped / 1 xfailed）。
- `python scripts/check_redlines.py` OK。

## Deviations

（实现偏离时回填）
