# Agent Note: --sync 注入 pipeline 未注入 settings 时回退 pipeline._settings 读取过滤配置

Status: proposed

## Problem

`scripts/ingest.py` 的 `_main_sync` 只在**未注入 pipeline** 时加载 `Settings`，因此「注入 pipeline 且不注入 settings」的测试装配路径下，`--sync` / `--prune` 不应用 `ingestion.source_filters` 过滤（普通模式则会从 `pipeline._settings` 回退取用，行为正确）。生产 CLI（真实调用、无注入）配置照常加载，过滤生效——不构成产品缺陷，但每个 sync 相关测试都必须记得显式传 `settings=pipeline._settings`（既有先例 `test_sync_skips_filtered_files`），漏掉会静默退化为「全受理」且无报警。

## Proposal

`_main_sync` 取配置时增加回退：注入 pipeline 而未注入 settings 时，从 `pipeline._settings` 读取 `source_filters`（与普通模式同口径）。改动预计为取值处一行级调整 + 参数化回归用例。

## Alternatives considered

- **保持现状（测试显式注入 settings）**：最强论据是零改动、测试依赖显式。被否为最终方案：依赖人记，IR7.5 期间已实际踩到一次；静默全受理比报错更危险。
- **把过滤参数改为 `_is_filtered` 调用方必填**：最强论据是类型层面杜绝遗漏。被否：签名扩散到 `collect_and_diff` / `_main_sync` / `main` 多处，收益不成比例。

## Acceptance criteria

- 注入 pipeline、不注入 settings 时，`--sync` / `--prune` 应用配置默认排除（`.obsidian/**` 等）。
- 既有 `tests/unit/test_ingest_sync.py` 全绿；新增参数化用例覆盖「注入 pipeline 无 settings」的回退路径。
- 生产路径行为逐字节不变（`pytest tests -q` 零回归）。

## Risks

- `_main_sync` 的配置装配时序在 IR7.4 刚重排过（注入 fake pipeline 且无 settings 时保持 IR7.4 前「全受理」基线是有意为之），回退会改变这一测试契约——需同步更新该先例注释与用例。
- 若注入的 fake pipeline 无 `_settings` 属性，回退需容错（getattr 默认 None → 维持全受理），否则测试基建反而变脆。
