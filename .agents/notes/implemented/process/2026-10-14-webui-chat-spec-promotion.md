# Agent Note: WebUI 迁移 + Chat 合并规格立项（WEBUI_CHAT_SPEC / WC 系列）

Status: implemented

## Problem

`docs/analysis/SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md` 完成了 Chat 复用分析但含五处与源码不符的判断（最关键：本项目主 RAG 路径没有 LLM 生成步骤，`ResponseBuilder` 只渲染引用；LLM 层不具备流式能力）。同时 backlog `dashboard-api-migration.md`（P1–P6）尚未立项。两条线共享 API 层、鉴权与任务模型，需要合并立项并把分析勘误固化进规格。

## Decision

- 新建 `docs/specs/WEBUI_CHAT_SPEC.md`（WC 系列，WC0–WC10，与 auto-coder `sync_spec.py --prefix wc` 兼容）。
- 四项用户拍板决策写入规格第 3 章：D1 前端 React/Next（**推翻 backlog 的 Vue 3 选型**，直接移植 sirchmunk `web/`，Apache-2.0）；D2 传输用 WebSocket（任务事件同信封，backlog P4 的 SSE 修订；主项目→sidecar 的 SSE 不变）；D3 合并规格分阶段；D4 检索 hybrid 默认 + 可选 agentic FAST/DEEP。
- 规格 §1.2 固化分析文档五处勘误（Next 16/React 19 而非 14、`relevance_score: 0.92` 硬编码不抄、三步法触发条件不对称等）；§4.6 流式默认关（SX5.3 线程泄漏）；D6 降级默认 error 帧（守引用可溯源）。
- 索引与状态同步：MOC（specs/ 区新表、backlog 行注已立项）、根 README specs 行、backlog/README 候选表、`dashboard-api-migration.md` 状态头（Vue/SSE 两处修订点写明）、分析文档头部加「后续+勘误」行、AGENTS.md 按需选读加一行。

### 同日审核修订

逐条复核规格引用与设计后修订（WC0.1 备注已登记）：

- **D8 检索层分工**：既有 `SearchService`（S4.1）返回 MCP Markdown，不适合作 AnswerGenerator 输入，故新增 `QueryService` 返回 `RetrievalResult`，两者并存；ChatEngine 只依赖注入的 `ChatRetriever` 协议，适配器放应用服务层——`AgenticSearchTool` 在 `mcp_server` 层，core 直接 import 会反向依赖。
- **D7 修正**：`method` 是 stage 级字段，trace 无此字段，Query Traces 页也无来源过滤；trace 级标识改用 `metadata.routes="chat"`（沿用 agentic 先例），WC7.4 补页面筛选。`AgenticSearchTool.__call__` 自建并落盘 trace，WC7.3 补 `run(arguments, *, trace=)` 注入入口保证每轮一条。
- **D9 独立 token**：Web API token 进浏览器（localStorage + WS URL），改用 `RAG_API_TOKEN` 不复用 sidecar 凭据；`${ENV}` 缺失时保留原值（`settings.py:365-370`），未解析占位符按空处理。
- **重试窗口止于首个 `stream` 帧**：上游整轮重试依赖假流式，真流式下首帧后重试会重复内容。
- **Query Playground 补入 WC9.4**：backlog 提案早于 S4.1，只列六页，实为七页。
- 行号与事实修正：`chat_sessions` 缓存位置、chat WS 的 `?token=` 行号、SessionStore 列为重新设计而非对齐上游等。

## Alternatives considered

- **Chat 独立规格、迁移留 backlog**：被否——两线共享 `src/api/` 骨架与任务模型，拆开会产生交叉引用同一批文件的活跃规格。
- **Streamlit Chat 页先行**：被否（用户选择 React/Next）——最小改动但假流式/交互限制正是要解决的原始问题。
- **SSE 而非 WS**：被否（用户选择 WS 对齐上游）——SX5 的 SSE 仅用于主项目→sidecar 服务间调用，对外客户端统一 WS。
- **分析文档正文改写勘误**：被否——分析文档是历史判断记录，勘误进规格 §1.2 并在头部链接，保持「分析 → 规格勘误」的可追溯链（对齐 SX 规格勘误冻结规格的先例）。

## Consequences

- **收益**：Vue 3 前端工程从零搭建的成本消掉（移植上游 `web/`）；假流式、硬编码评分等上游反模式在「不抄」清单显式化；Chat 降级默认不可溯源答案（D6）堵住「幻觉但看起来像 RAG」的回归口子。
- **代价**：backlog 出现「提案正文与规格冲突时以规格为准」的局部例外（Vue/SSE 两处），靠两个状态头显式声明化解；WC0–WC10 期数多，WC5–WC8（Chat）与 WC1–WC4（迁移）交替推进时任务表归靠 auto-coder 顺序执行；第 8 章已列明跨线依赖（WC6.4←WC1.6、WC7←WC2.1、WC8←WC3.1–3.2），按表序单线执行即满足。

## Verification

- `sync_spec.py` 章节解析：`## N.` 标题 8 章齐备，第 8 章「排期与进度」命中 schedule 关键词。
- `rg` 断链检查：旧路径（specs/ 当前为空表述、backlog 未立项状态）已全量替换。
- 关联决策：[SX 规格冻结与遗留台账](2026-10-14-sx-spec-freeze-followups-ledger.md)（specs/ 语义先例）、[路线图主线拆分](2026-10-07-roadmap-migration-split-to-backlog.md)（本文所立项的 P1–P6 来源）。
