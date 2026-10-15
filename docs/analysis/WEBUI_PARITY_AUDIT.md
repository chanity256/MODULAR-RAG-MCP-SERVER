# WebUI 双轨功能等价核验（WEBUI_PARITY_AUDIT）

> **性质**：WEBUI_CHAT_SPEC **WC10.1**（backlog `dashboard-api-migration.md` P6 退役门槛的第一条）的核验记录，非可执行排期。
> **日期**：2026-10-14　**核验对象**：Streamlit 七页面（`src/observability/dashboard/pages/*.py`）↔ Next 页面（`frontend/app/*`、`frontend/components/*`、`frontend/lib/*`）↔ 其背后的 API 路由（`src/api/routes/*`）。
> **方法**：逐页通读两侧实现与 API 路由后逐项对照，不凭印象；每条结论可在列出的文件中复核。
> **结论一句话**：七页面的**数据含义与操作主流程全部等价**，另有多处改进；发现 **1 项 Medium、4 项 Low** 缺口与 **1 项运维提示**；其中 L2（Chat 阶段面板）已在核验中直接修复，其余均不阻塞「双轨并存」。Medium 项（Deep 进度日志）是**有意取舍**，需用户拍板后才能谈 Streamlit 删除。遗留项另记入 [backlog/webui-parity-followups.md](../backlog/webui-parity-followups.md)。

## 1. 状态图例

| 标记 | 含义 |
|------|------|
| ✓ | 等价：同一数据、同一操作、同一结果 |
| improved | Next 侧能力超出 Streamlit |
| gap | Next 侧缺失或弱于 Streamlit，见第 3 章（含严重度） |
| fixed | 核验中发现并已修复（见第 3 章） |

## 2. 逐页对照矩阵

### 2.1 系统总览（`overview.py` ↔ `app/overview` + `GET /api/v1/overview`）

| 功能 | Streamlit | Next | 状态 |
|------|-----------|------|------|
| 组件配置卡片（name / headline / extras，None 值过滤） | `ConfigService.component_cards()` | 同一服务，API 序列化为 `components` | ✓ |
| 数据资产：文档 / Chunk / 图片总数 | 3 个 metric | 4 个 MetricCard（多 Collections） | improved |
| 各集合统计表 | `st.table` | DataTable | ✓ |
| 统计读取失败时只告警、其余照常展示 | `st.warning(f"…{exc}")` | `warnings[]` 横幅 | ✓（文案为通用「collection stats unavailable」，不带异常详情，见 L5） |
| 最近一次 Ingestion / Query trace（trace_id、started_at、耗时） | 是 | 是；Query 另带来源徽章 | improved |
| 空库引导文案 | 是 | 是 | ✓ |
| 手动刷新 / 自动刷新（10s） | 无 | 有 | improved |

### 2.2 数据浏览器（`data_browser.py` ↔ `app/data-browser` + `/documents`、`/collections`、`/images`）

| 功能 | Streamlit | Next | 状态 |
|------|-----------|------|------|
| 集合筛选 | selectbox | select（来自 `GET /collections`） | ✓ |
| 关键词搜索 source_path / title（大小写不敏感） | text_input | 搜索框，服务端同口径匹配 | ✓ |
| 文档计数 | 「共 N 篇」 | 分页器 `total` | ✓ |
| 文档元信息（Source / Collection / Chunks / Images / Ingested at） | expander 内列表 | 详情面板 `<dl>` | ✓ |
| 文档 Hash | 无 | 展示前 12 位 | improved |
| Chunk 列表：文本截断 800 字符 + 选定 metadata 字段（title/summary/tags/page/image_refs/collection） | 是 | 同键集合（`CHUNK_METADATA_KEYS`）、同 800 截断 | ✓ |
| 查看 Chunk 全文 | 不可（只显示前 800） | 「Show full text」 | improved |
| 关联图片（缺失文件提示） | `st.image`，缺失显示「文件缺失」 | `ImageThumb` 经带 token 的 `apiFetch` 取 blob，缺失显示「File missing」 | ✓ |
| 列表规模 | 全部文档平铺 expander | 每页 20 条分页 | improved |
| 删除文档 | 无（删除在 Ingestion 管理页） | 详情面板内带确认的删除 | improved |
| 加载 / 空 / 错误 / 重试状态 | 仅错误文案 | 全部具备（backlog P5 验收项） | improved |

### 2.3 Ingestion 管理（`ingestion_manager.py` ↔ `app/ingestion-manager` + `/uploads`、`/ingestions`、`/tasks`、`DELETE /documents/…`）

| 功能 | Streamlit | Next | 状态 |
|------|-----------|------|------|
| 选择目标集合 / 新建集合（默认名 default） | selectbox + 「新建集合」 | select + 「New collection…」（无集合时默认进入新建） | ✓ |
| 多文件上传 | `file_uploader` | 多选文件、可逐个移除、显示大小 | ✓ |
| 强制重摄取 `--force` | checkbox | checkbox | ✓ |
| 摄取执行 | 同步阻塞在当前 rerun，`IngestionService.ingest` | 后台任务（`POST /ingestions` 返回 202 + task_id） | improved |
| 进度（stage current/total） | `st.progress` | `TaskStatusView` 进度条 + WS 实时（断线自动轮询兜底） | ✓ |
| 刷新页面后进度恢复 | 否（rerun 丢失） | 是（`tasks.db` 持久化，历史任务可再次跟随） | improved |
| 结果摘要（files/chunks/images/failed/errors） | `st.success(f"…{summary}")` 原始文本 | MetricCard + 逐文件错误表 | improved |
| 全部文件失败时的失败态 | 提示「摄取完成：…failed=N」 | 任务置 failed（`files>0 且 failed>=files`） | improved |
| 同文件同集合重复提交 | 无保护 | 409 + 「Follow the active task」 | improved |
| 上传大小上限 | 无 | `api.max_upload_mb`，超限 413 | improved |
| 已摄入文档列表 + 删除 | 每行「删除」按钮，**无确认** | 分页列表 + 确认弹层，显示 chroma/bm25/images 删除数 | improved |
| 摄取只接受已上传文件名（不可指向任意服务器路径） | 不适用 | API 强制（防持 token 客户端读任意文件） | improved |

### 2.4 Ingestion 追踪（`ingestion_traces.py` ↔ `app/ingestion-traces` + `GET /traces?trace_type=ingestion`、`GET /traces/{id}`）

| 功能 | Streamlit | Next | 状态 |
|------|-----------|------|------|
| 选择一条摄取记录（label = source · 耗时） | selectbox | 可点击表格（Source / Collection / Status / Chunks / Images / Total / Started） | improved |
| 摘要：状态（skipped / success）、Chunks、Images、总耗时、collection、source | 4 个 metric + caption | 4 个 MetricCard + caption（含 trace_id） | ✓ |
| 阶段耗时瀑布图（load/split/transform/embed/store/upsert，无则退回全部阶段，0 耗时丢弃） | `bar_chart` | 横向条形（无图表依赖），口径由 API 与 Streamlit 对齐 | ✓ |
| 阶段详情（stage · method · elapsed + details JSON） | expander | `<details>` + method/provider 徽章 + 标量摘要 + JSON | improved |
| 关键词搜索（trace_id / source_path） | 无 | 有 | improved |
| 自动刷新 | 无 | 有 | improved |
| 可浏览的 trace 数量 | 全部 | 仅最新 500 条（API `limit<=500`，UI 无翻页） | gap（L3） |

### 2.5 Query 追踪（`query_traces.py` ↔ `app/query-traces` + `GET /traces?trace_type=query&routes=…`）

| 功能 | Streamlit | Next | 状态 |
|------|-----------|------|------|
| 关键词搜索（query / trace_id） | 是 | 是（服务端，另含 source_path） | ✓ |
| 来源筛选（default / chat / agentic / 日志中出现过的其它来源） | selectbox（KNOWN_SOURCES + 实际出现） | select（`routes` 服务端过滤；UI 标签 `hybrid` 对应服务端 `default`） | ✓ |
| 列表来源标识 | label 前缀 `[chat]` | Source 徽章列 | ✓ |
| Query / trace_id / 耗时头部 | 是 | 是（另含开始时间与来源徽章） | ✓ |
| 来源附注（`retrieval` / `session_id` / `mode`） | caption 展示 | 非默认来源时在头部渲染（`sourceMetadataNotes`） | fixed（L2） |
| 耗时瀑布图（含 agentic_search / chat_* 阶段） | `bar_chart` | 条形图；API 把已知检索阶段按序在前、其余阶段按记录顺序追加 | ✓ |
| Dense / Sparse 命中对照 | 表格 | 表格（Rank / Chunk / Score） | ✓ |
| Rerank 前后对比（rank 变化 ↑↓·、score、backend、fallback） | 表格 | 表格（▲▼•new）+ backend/fallback | ✓ |
| Agentic 工具阶段（模式 / Token / LLM 次数 / 证据文件数、失败告警、证据文件表） | 是 | 是（字段一一对应，失败显示 alert） | ✓ |
| Agentic 路由（hybrid/fusion 的 routes 计数与 trigger） | 是 | 是 | ✓ |
| 最终 / 融合结果 | 是 | 是 | ✓ |
| Chat 三阶段结构化面板（历史条数前后、是否改写、改写后查询、引用片段数、回答字数、method/grounded/cancelled/error/usage） | `_render_chat` 专用区块 | 核验时仅有通用 `StageList`（chat 阶段字段是**扁平记录**而非 `details`，只能看整条 JSON）；现已补 `ChatStagesView`（`chatSummary` 同时读扁平键与 `details`），无 chat 阶段时隐藏 | fixed（L2） |
| 阶段详情 | 按阶段 expander | `StageList`（徽章 + JSON） | improved |
| 可浏览的 trace 数量 | 全部 | 仅最新 500 条 | gap（L3） |

### 2.6 评估面板（`evaluation_panel.py` ↔ `app/evaluation` + `POST /evaluations`、`/tasks`）

| 功能 | Streamlit | Next | 状态 |
|------|-----------|------|------|
| 评估后端选择（composite / custom / ragas，默认 composite） | selectbox | select（同一元组顺序，API 用 Literal 校验） | ✓ |
| Golden test set 路径 | 预填 `default_test_set_path()` | 空输入 + 占位文案「Default: evaluation.golden_test_set」，留空由服务端取默认 | ✓（默认值不回显，见 L4） |
| Top-K（0 = 配置默认） | number_input | 同语义（0 / 空不发送，校验非负整数） | ✓ |
| 限定集合 | `filters={"collection": …}` | 同 | ✓ |
| golden set 不存在时的提示 | 提交前 `is_file()` 校验并提示 | 任务失败后显示错误信息 | gap（L4，后果等价、时机更晚） |
| 运行中状态 | spinner（阻塞页面） | 后台任务进度条，可离开页面 | improved |
| 同时多个评估 | 无保护 | 同一时刻只允许一个（409 + 「Follow the active run」） | improved |
| 最近一次聚合指标（evaluated / failed 计数、metric 卡片） | 是（取前 4 个 metric 列） | 全部 metric 卡片，标题含 total | ✓ |
| 逐 query 明细（query / hit_rate / mrr / first_expected / retrieved_top1 / error） | 固定两个指标列 | 指标列按报告动态生成 + top-1 hit/miss 徽章 + 「Only misses and errors」过滤 | improved |
| 历史记录 | 仅当前浏览器会话（`session_state`） | 持久化到 `tasks.db`，含状态、后端、Top-K/集合、各指标并排 | improved |
| 历史趋势折线图 | `line_chart`（前 3 个指标） | 无图表，仅历史表并排指标 | gap（L4） |

### 2.7 在线检索 / Query Playground（`query_playground.py` ↔ `app/query-playground` + `POST /search`、`GET /search/capabilities`）

| 功能 | Streamlit | Next | 状态 |
|------|-----------|------|------|
| 三模式按钮（Normal / Fast / Deep），默认 Normal | 是 | 是 | ✓ |
| 配置门控与不可用原因提示（Fast 需 enabled，Deep 另需 allow_deep） | 置灰 + help 提示 | 置灰 + title + 文字说明；服务端另以 403 `AGENTIC_DISABLED` / `DEEP_DISABLED` 兜底（请求不触达 sidecar） | improved |
| 查询文本 / Top-K / Collection（仅 Normal） | 是 | 是（Top-K 前后端限定 1–100，Streamlit 无上限） | ✓ |
| 空查询校验 | warning | 表单校验 | ✓ |
| Normal / Fast 检索结果：Markdown 答案 + 引用表（编号/来源/页码/分数） | 是 | 是（引用卡片，附正文片段） | ✓ |
| 检索失败的友好展示（`is_error`） | `st.error` | alert（200 + `is_error`，与 Streamlit 同口径） | ✓ |
| Agentic 详情（模式 / Token / LLM 次数 / 耗时 / agentic 答案 / 证据文件 / telemetry 表与零 LLM 路径说明） | 是 | 是（文案一致） | ✓ |
| 每次检索写入 trace、提示去 Query 追踪查看 | 是 | 是 | ✓ |
| 各模式结果保留以便对比 | 仅最近一次结果 | 每模式各保留最近一次，切换模式即对比 | improved |
| **Deep 模式实时进度日志**（SX5：`run_stream` + `st.status` 滚动最近 30 行 sidecar 日志） | 有 | **无**：`POST /search` 只走同步 `SearchService.run`，页面仅「Running Deep search…」加载态 | gap（M1） |

### 2.8 新增能力（Streamlit 无对应，列此以免误判为「多余」）

| 能力 | 位置 |
|------|------|
| Chat（多轮对话 + RAG 有据回答、真流式、会话持久化、citations 面板、检索模式选择） | `app/page.tsx`、`/api/v1/chat`（WS）、`/api/v1/chat/sessions` |
| 单端口托管 + SPA 回退、Bearer token / `?token=`、Origin 校验、CORS 白名单、安全响应头 | `src/api/` |
| 暗色主题、侧栏导航、统一错误 / 重试 / 空状态组件 | `frontend/components/dashboard/*` |

## 3. 缺口清单

| 编号 | 严重度 | 缺口 | 影响 | 处置建议 |
|------|--------|------|------|----------|
| **M1** | Medium | Query Playground 的 Deep 模式没有实时进度日志。Streamlit 用 `SearchService.run_stream` 展示 sidecar 日志；REST `POST /api/v1/search` 只暴露同步 `run` | Deep 动辄数十秒，用户只看到加载态，无法判断是否卡住。**但这是有意避开 SX5.3**：流式端点每请求泄漏约 10 个 sidecar 线程且不回收，Chat 侧同理默认 `agentic_progress_stream: false` | **需用户决策**（见第 4 章）：接受缺口并在文档注明（当前做法）；或为 `/search` 增加 WS/SSE 进度通道（沿用 Chat 的 `search_log` 帧并以开关门控）。在 sidecar 线程泄漏上游修复前，不建议默认开启 |
| L1 | Low | Streamlit 启动器 `start_dashboard.py` 内置 sidecar 托管编排（探活 → 拉起 → 等健康 → 随进程回收），`start_api.py` 没有 | 属于「Streamlit 独有的业务逻辑」（backlog P6 退役门槛第 4 条）。Web 路径下 sidecar 须手动 `bash scripts/sirchmunk/start_sidecar.sh`；不影响功能正确性，只影响便利性 | 删除 Streamlit 前需二选一：把编排抽到共享模块并给 `start_api.py` 加 `--manage-sidecar`；或在文档明确 sidecar 手动启动并接受 |
| L2 | Low | **已修复**：Query 追踪缺 Chat 阶段结构化面板与「来源附注」（`retrieval` / `session_id` / `mode`） | 修复前信息仍可见（展开整条 JSON），只是没有摘要与专用卡片 | 已在 `frontend/lib/traces.ts`（`chatSummary` / `sourceMetadataNotes`）、`components/dashboard/StageViews.tsx`（`ChatStagesView`）、`app/query-traces/page.tsx` 落地，vitest + Playwright（`e2e/dashboard.spec.ts`）+ fake server 种子数据（扁平 chat 阶段，对齐真实 `ChatEngine`）已覆盖 |
| L3 | Low | Ingestion / Query 追踪列表只能浏览最新 500 条（API `limit<=500`，UI 仅「Show more」前端截断） | trace 超过 500 条后更早的记录在 Web 端不可达，Streamlit 无此限制。可用关键词 / 来源过滤间接缩小，直接读 `logs/traces.jsonl` 不受影响 | 前端补 offset 翻页（API 已支持 `offset`） |
| L4 | Low | 评估页三处小差异：无历史趋势折线图；Golden set 默认路径不回显；路径不存在时错误出现在任务失败后而非提交前 | 功能无损，体验略弱 | 趋势图可由 `collectMetricKeys` 现成数据画；默认路径可在 capabilities 类接口回显（需改 API，非本轮范围） |
| L5 | Low | Overview 的统计读取失败告警不带异常详情 | 设计取舍（API 不向客户端泄露内部异常），排障看服务端日志 | 不处理 |
| 运维 | 提示 | `api.enabled` 默认 `false`，`python scripts/start_api.py --serve-ui` 在未配置时以退出码 2 拒绝启动；Web UI 另需先 `npm run build`（Streamlit 无构建步骤） | 新用户第一次启动会撞墙 | **已在 USAGE_GUIDE / 弃用提示中写明**（设 `api.enabled: true` 或加 `--force`）；是否把默认改为 `true` 是用户决策 |

### 3.1 附带观察（非等价缺口）

- 评估提交的 `test_set_path` 接受调用方给的任意服务器路径（经鉴权，但仅 loopback 无 token 部署下等同于本机任意进程可指定）；摄取则已被限制为「只接受已上传文件名」。若未来远程部署，建议收敛为 `evaluation` 目录白名单。当前单用户本机场景不构成问题，记录备查。
- Playground 的 Normal 模式仍走 `SearchService`（MCP tool + Markdown），未委托 `QueryService`；与 Chat 的 hybrid 检索链路相同、产物形态不同，WC9.4 已按 D8 决定保持现状。

## 4. 对 Streamlit 退役的结论与待决事项

**等价性结论**：按 backlog P6「六个页面（实为七）达到功能等价」逐项核对，除 M1 外无功能性缺失（L2 已修复）；M1 是在 SX5.3 线程泄漏背景下的有意取舍而非遗漏。

**当前处置（WC10.2，非破坏性）**：保留全部 Streamlit 代码与测试，`start_dashboard.py` 启动时向 stderr 输出弃用提示，文档把 Web UI 定为主界面、Streamlit 标注为 legacy（删除条件见 [USAGE_GUIDE 第 10.9 节](../reference/USAGE_GUIDE.md#109-streamlit-dashboardlegacy)）。

**需要用户决策**：

1. **M1**：接受 Deep 无实时进度（推荐，直到上游修复线程泄漏），还是投入做 `/search` 进度通道？
2. **L1**：删除 Streamlit 前是否把 sidecar 托管编排迁入 `start_api.py`？
3. **`api.enabled` 默认值**：是否改为 `true`，让 `start_api.py --serve-ui` 开箱即用？
4. **真正删除的时机**：满足 USAGE_GUIDE 10.9 列出的全部条件（含 WC10.3 门控冒烟通过、Web UI 稳定运行一段时间无回退需求）后，由用户显式下令；删除范围与保留项也已在该节写明（`observability/dashboard/services/` 是 API 层的共享服务，**必须保留**）。
