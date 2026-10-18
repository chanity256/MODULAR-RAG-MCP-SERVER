<!-- 规格定位：收口 WebUI 双轨核验（WC10.1）审计 L3/L4 中已决策可做的三项（追踪列表翻页、评估趋势图、评估提交路径预检）。独立增量规格，不改动 WC/LG 既有契约。来源为 backlog 拆分移交，#1（Deep 进度日志）与 #4（sidecar 托管编排）仍留 backlog。 -->

# WebUI 双轨遗留差异收口 规格 (WEBUI_PARITY)

> **📌 冻结记录**
>
> - 冻结时间：2026-10-18
> - 完成概要：WP0–WP2 三项全部收口（追踪列表服务端 offset 翻页、评估历史内联 SVG 趋势图、评估提交路径 422 预检）；审计 L3 关闭、L4 仅剩已接受的「默认路径回显」。实测 pytest 2470 passed / 11 skipped、vitest 250 passed、Playwright 24 passed。实现偏差见 5 章进度备注（翻页复用 `Pagination`/`pageInfo`/`clampOffset`，钳制走渲染期调整模式）。
> - 遗留：见 [backlog/webui-parity-followups_backlog.md](../backlog/webui-parity-followups_backlog.md)（#1 Deep 进度日志、#4 sidecar 托管编排）

> 版本：0.2 — 由 [backlog/webui-parity-followups_backlog.md](../backlog/webui-parity-followups_backlog.md) 拆分移交晋升（#5、#2、#3 的 422 预检三项承接为本规格；#1、#4 及 #3 其余部分留在 backlog）。v0.2 为立项审核修订：纠正审计编号（L3/L4）、WP0 覆盖两个追踪页、WP2 预检经服务层注入并对齐既有路径解析与错误信封、补齐测试落点
>
> 起源与决策依据：[analysis/WEBUI_PARITY_AUDIT.md](../analysis/WEBUI_PARITY_AUDIT.md)（WC10.1 缺口清单 L3：追踪列表 500 条上限；L4：评估页三处小差异中的趋势图与路径提交前校验）+ 上述 backlog 台账的决策三问（原文见 git 历史）
>
> 基线：WC0–WC10 已收口冻结（[06_WEBUI_CHAT_SPEC_frozen.md](06_WEBUI_CHAT_SPEC_frozen.md)）；Web UI 已是主界面，Streamlit 为 legacy；任务表数据已持久化于 `tasks.db`
>
> 日期：2026-10-18

## 目录

- 项目概述
- 核心目标与范围
- 技术选型与模块设计
- 测试方案
- 任务排期与进度

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 要解决什么问题？ | WC10.1 审计确认的两项 Low 缺口：**L3** Ingestion / Query 两个追踪列表只能浏览最新 500 条；**L4** 评估页无历史趋势折线图、golden set 路径不存在时要到任务失败才报错 |
| 定位是什么？ | 既有 Web 页面与 API 的小幅增强：WP0、WP1 纯前端（API 已支持 `offset`，评估数据已在 `tasks.db`）；WP2 为 `POST /evaluations` 增加一处前置校验。不替换既有链路 |
| 实施范围（已决策） | WP0–WP2 全部为承诺任务；无门控任务 |
| 主要代价 | 改动量小；无新增依赖、无新端点、无响应 schema 变更。WP2 给 `EvaluationService` 增加一个只读方法，测试替身同步补齐 |

### 1.2 背景与现状

- **追踪列表**：`GET /api/v1/traces` 已支持 `limit<=500` + `offset`（`src/api/routes/traces.py`），`lib/queries.ts` 的 `fetchTraces` 也已透传 `offset`。但 `app/query-traces/page.tsx` 与 `app/ingestion-traces/page.tsx` 都一次拉满 `PAGE_SIZE = 500`、只做客户端「Show more」切片，超出 500 条的 trace 在 Web 端不可达。Query 页另有客户端 `filterBySource` 兜底（注释称覆盖「忽略 `routes` 参数的服务端」）。
- **评估历史**：`EvalHistory` 拉取最近 20 个 `evaluation` 任务，表格并排列出聚合指标（`collectMetricKeys` 按首次出现顺序取列），无跨运行趋势可视化；Streamlit legacy 有前 3 个指标的 `st.line_chart`（x=run）。
- **评估提交**：`POST /api/v1/evaluations` 不检查 `test_set_path` 是否存在，路径错误时任务照常受理、异步失败。`EvaluationService.run` 对相对路径会经 `resolve_test_set_path` 回退到仓库根解析。`EvaluationForm` 已把提交失败的 `ApiError` 经 `ErrorNotice` 渲染。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| P0 | 追踪列表 offset 翻页 | 承诺（WP0） | Ingestion / Query 两个追踪页改为服务端 offset 分页，可浏览全部 trace |
| P1 | 评估历史趋势折线图 | 承诺（WP1） | `EvalHistory` 增加内联 SVG 折线图，展示最近 10 次成功运行的前 3 个指标 |
| P2 | 评估提交路径预检 | 承诺（WP2） | `POST /evaluations` 对显式提供且解析后不存在的 `test_set_path` 返回 422，表单即时显示错误 |

### 2.2 Out of Scope（明确不做）

- **#1 Playground Deep 实时进度日志**：`run_stream` 命中 SX5.3 sidecar 流式线程泄漏（上游缺陷），随 [sirchmunk-extension-followups_backlog.md](../backlog/sirchmunk-extension-followups_backlog.md) #1 处置。
- **#4 sidecar 托管编排迁移**：「`start_api.py --manage-sidecar`」与「文档声明手动启动」二选一未决，留在 backlog。
- **#3 其余展示差异**（L4 的默认路径回显、按钮配色、空命中标题）：核验已裁定「结果等价，接受」；默认路径回显需改 API，审计已标为非本轮范围。
- **默认 golden set 路径的预检**：见 3.3 理由。
- **不引入图表库**，不改 `/api/v1/traces` 的 `limit` 上限，不动 Streamlit legacy 页面层与退役门槛。

## 3. 技术选型与模块设计

```
WP0  ingestion-traces / query-traces ── fetchTraces({limit: 50, offset}) ──▶ GET /api/v1/traces   (API 零改动)
                                     └─ lib 纯函数：页窗口计算 + offset 越界钳制
WP1  EvalHistory ── 同一次 fetchTasks({kind:"evaluation", limit:20}) ──▶ lib 纯函数抽点 ──▶ 内联 SVG
WP2  EvaluationForm ──▶ POST /api/v1/evaluations ──▶ EvaluationService.test_set_exists(path) ──▶ 422 | 409 | 202
```

### 3.1 WP0 追踪列表翻页

1. **服务端分页，页大小 50**（常量，两页共用）：每页一次请求，`total` 取服务端返回值。否决「继续追加拉取」：500 条一次进 DOM 正是现状问题。
2. **页窗口与钳制抽成纯函数**：放 `lib/` 下（如 `lib/pagination.ts`），输入 `offset / limit / total`，输出 `from / to / hasPrev / hasNext` 与钳制后的 offset。`lib/data.ts` 已有 Data Browser 的 offset 越界钳制（`data.test.ts` 有用例），优先复用或提炼，不另写一份。
3. **状态规则**：关键词或来源筛选变化时 offset 归零；刷新后 `total` 变小导致 offset 越界时钳制到最后一页；换页后默认选中新一页首行（沿用现有 `?? items[0]`）。
4. **Query 页去掉客户端 `filterBySource` 兜底**：分页后在单页上再做客户端过滤，会让行数与服务端 `total` 对不上。本项目 API 支持 `routes`，兜底不再需要。`filterBySource` 函数及其单测保留在 `lib/traces.ts`。
5. **已知行为（接受）**：列表按最新在前，开启自动刷新时新 trace 进来会使当前页内容整体后移。不做游标分页。

### 3.2 WP1 趋势折线图

1. **内联 SVG，不引入 recharts/echarts**：三条折线以内的静态图，引库会拉高 bundle 并与静态导出耦合。
2. **数据口径**：复用 `EvalHistory` 已有的那次请求，不新增请求。只取 `status = succeeded` 且有 report 的运行，最近 10 次，按 `createdAt` 正序作 x 轴；指标取 `collectMetricKeys` 的前 3 个（对齐 Streamlit「前 3 个指标」）。y 轴范围 `[0, max(1, 最大值)]`。
3. **抽点为纯函数**（放 `lib/evaluation.ts`），不足 2 次成功运行时返回空并渲染一行说明文字，不画图。
4. **位置**：History 表格下方常显，不折叠；每个点带 `<title>`（时间 + 数值）作悬停提示。

### 3.3 WP2 提交路径预检

1. **经服务层校验，不在路由里直接调 `os.path`**：给 `EvaluationService` 增加 `test_set_exists(path) -> bool`，内部沿用 `run()` 的解析规则（相对路径先按原样、再按仓库根经 `resolve_test_set_path` 解析）。这样预检与实际执行的判定一致，测试也能经替身控制。
2. **只校验显式路径**：留空时使用 `evaluation.golden_test_set` 默认值。默认值缺失是服务端配置问题，请求方改不了，不适合用 422（语义是「请求本身有误」）；维持现状，即任务失败并在进度区报错。`tests/integration/test_api_write.py` 中「默认路径缺失 → 任务 failed」的既有语义不变。
3. **错误信封对齐既有 422**：`code = validation_error`，`details` 沿用 `RequestValidationError` 的列表形状，如 `[{"loc": ["body", "test_set_path"], "msg": "test set not found: <path>", "type": "path_not_found"}]`。
4. **判定顺序**：预检在获取 `_submit_lock` 与活跃任务检查之前执行。两种错误同时成立时返回 422，不受理任务、不产生任务记录。
5. **前端**：`EvaluationForm` 已渲染提交错误，无需新组件，只需确认 422 的 message 可读。
6. **安全边界**：预检会暴露「某路径是否存在」。API 默认只监听 127.0.0.1 且写接口需 token，与现有提交接口的信任边界相同，不额外处理。

## 4. 测试方案

- **离线确定性测试**：零网络、零真实模型。
  - 前端 vitest：WP0 页窗口与钳制纯函数（首页、末页、空列表、越界钳制）；`lib/queries.test.ts` 补一条 `offset` 出现在请求 URL 的用例（现有用例只在响应体里带 `offset`，没有断言请求参数）。WP1 抽点纯函数（只取 succeeded、最多 10 次、正序、前 3 指标、不足 2 次返回空）。
  - 后端 pytest：在 `tests/integration/test_api_write.py` 的评估分组下覆盖：显式路径不存在 → 422 且无任务被提交；显式路径存在 → 202；留空 → 不调用预检、202；预检与活跃任务同时成立 → 422。`FakeEvaluation` 增加 `test_set_exists`，默认返回 `True`，现有用例（含提交 `tests/fixtures/g.json` 期望 succeeded 的用例）语义不变。`EvaluationService.test_set_exists` 的解析规则在 `tests/unit/test_evaluation_service.py` 单测（tmp_path 构造存在/不存在、相对/绝对路径）。
- **前端 e2e（Playwright，离线假后端 `tests/e2e_web/fake_api_server.py`）**：
  - 假后端的 `FakeEvaluation` 同步增加 `test_set_exists`，否则 e2e_web 冒烟会因缺方法失败。
  - WP0：假后端只预置了少量 trace，不足一页。用 `page.route` 拦截 `/api/v1/traces` 返回 `total` 大于一页的桩数据，断言 Next / Prev 发出的 `offset` 与表格内容；不改共享假后端的种子数据，避免影响 `dashboard.spec.ts` 现有断言。两个追踪页各一条。
  - WP1：在 `evaluation.spec.ts` 中顺序完成两次评估后，断言 SVG 折线存在且点数为 2；只有 1 次时断言说明文字。
  - WP2：在 `evaluation.spec.ts` 中提交不存在的路径，断言表单错误出现且没有出现进度区。
- **真实依赖冒烟**：不触真实模型或 sidecar，不新增门控。
- **既有测试语义（不得破坏）**：
  - `pytest tests -q` 全绿（基线 2464 passed / 11 skipped），含 `tests/unit/test_docs_links.py` 与 `tests/e2e_web`；
  - `frontend` 的 `npm run typecheck && npm run lint && npm test` 全绿；
  - API 对外契约：`/api/v1/traces` 响应结构不变；`/api/v1/evaluations` 的 202 / 409 语义不变，仅新增 422 分支。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| WP0 | Ingestion / Query 追踪列表服务端 offset 翻页 | 两页均以 50 条/页服务端分页，显示 `from–to of total`，首页 Prev、末页 Next 禁用；筛选变化 offset 归零，越界钳制到末页；Query 页去掉客户端来源兜底过滤；vitest 覆盖页窗口与钳制及 `fetchTraces` 的 offset 透传；Playwright（`page.route` 桩）两页各断言一次翻页请求与内容 | 已完成（2026-10-18） |
| WP1 | 评估历史内联 SVG 趋势折线图 | History 表格下方展示最近 10 次成功运行（时间正序）前 3 个指标的 SVG 折线，无新依赖、无新请求；不足 2 次显示说明文字；vitest 覆盖抽点纯函数；Playwright 断言 2 次运行时点数为 2 | 已完成（2026-10-18） |
| WP2 | 评估提交 `test_set_path` 预检 | `EvaluationService.test_set_exists` 沿用 `run()` 的路径解析；显式且不存在 → 422（`validation_error`，`details` 为 loc 列表形状），不产生任务；留空行为不变；422 优先于 409；两个 `FakeEvaluation` 同步补方法；pytest 覆盖四个分支；Playwright 断言表单报错；e2e_web 冒烟不回归 | 已完成（2026-10-18） |

> 进度备注（2026-10-18）：WP0–WP2 全部交付。实测：pytest 2470 passed / 11 skipped（基线 2464 + 新增 6：`TestTestSetExists` 2 条、集成 4 分支 4 条）；前端 typecheck / lint / vitest 250 passed；Playwright 24 passed（WP0 翻页 ×2、WP1 图表、WP2 表单 422 各有专项）。实现偏差：越界钳制未用 effect 而走 React 渲染期调整模式（`react-hooks/set-state-in-effect` 禁止 effect 内 setState，渲染期守卫模式语义等价）；翻页控件复用 `components/documents/Pagination` 与 `lib/data.ts` 的 `pageInfo` / `clampOffset`，未新建 `lib/pagination.ts`。WP0 收口关闭审计 L3；L4 仅剩「默认路径回显」（已接受）。本规格已冻结（同日）；backlog 台账退役门槛中 #2/#5 视为「已补齐」。
