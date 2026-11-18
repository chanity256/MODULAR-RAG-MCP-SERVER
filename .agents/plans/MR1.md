# Plan: [MR1] mode_router 配置节 + RETRIEVAL_AUTO 常量与 ModeRouter 协议

Status: implemented

## Files
1. `src/core/chat/retriever.py` — 新增 `RETRIEVAL_AUTO` 常量（不进 `RETRIEVAL_MODES`）、`RoutingDecision` 数据类、`ModeRouter` Protocol；更新 `__all__`。
2. `src/core/settings.py` — 新增 `ModeRouterSettings` dataclass + `_build_mode_router` + 跨节校验（`_cross_validate`）；`chat.default_retrieval` 允许 `auto`；`Settings` / `load_settings` / `__all__` 接线。
3. `config/settings.yaml` — 新增 `mode_router` 示例节（`enabled: false`，风格对齐 agentic 节）。
4. `tests/unit/test_mode_router_config.py` — 新增：配置组离线单测（§4 配置组全部用例）。

## Order
先 retriever.py（零依赖、纯新增），再 settings.py（含校验），再 settings.yaml（示例值与 settings.py 默认一致），最后测试。

## Risks
- `CHAT_RETRIEVAL_MODES` / `RETRIEVAL_MODES` 内容不得变（drift：多处枚举登记）——只新增独立常量，不动既有元组。
- `chat.default_retrieval` 原校验是「必须在 CHAT_RETRIEVAL_MODES 内」；放宽为「在三档内或 == auto」，cross 校验（auto 需 mode_router.enabled）放在 load_settings，避免 `_build_chat` 依赖其他节。
- 未解析的 `${...}` 占位符视为缺失：新增 `_is_unresolved_env` 帮助函数，仅对 account_id / api_token 生效。
- 凭据校验只在 `enabled=true` 时要求（零加载语义，对齐 agentic allowed_roots 先例）。
- 既有测试语义：`test_chat_settings.py` / `test_config_loading.py` 不改即须通过。

## Verification
- `pytest tests/unit/test_mode_router_config.py tests/unit/test_chat_settings.py tests/unit/test_config_loading.py -q` 全绿。
- CI 等价禁网命令跑全量：`NO_PROXY='*' pytest tests -q --disable-socket ...`。
- `python scripts/check_redlines.py` 0 违规。

## Deviations
（无）
