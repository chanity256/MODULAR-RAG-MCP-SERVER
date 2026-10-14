# Sirchmunk WebUI / Chat 复用分析

> 状态：分析文档，非可执行排期｜分析日期：2026-10-14｜参考基线：sirchmunk `b314e11`（与 [SIRCHMUNK_EXTENSION_SPEC_frozen](../frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md) 源码实证基线一致，本文 file:line 均相对 `sirchmunk/src/sirchmunk/` 与 `sirchmunk/web/`）
>
> **后续**：已立项为 [specs/WEBUI_CHAT_SPEC.md](../specs/WEBUI_CHAT_SPEC.md)（WC 系列），其 §1.2 含对本文的五处勘误（主链路无 LLM 生成步骤、LLM 层无流式能力、前端实为 Next 16/React 19、`relevance_score` 硬编码、三步法触发条件不对称），以该规格为准。
>
> 关联文档：[dashboard-api-migration.md](../backlog/dashboard-api-migration.md)（Dashboard 前后端分离迁移提案，P1–P6）、[MICROSERVICES_ANALYSIS.md](MICROSERVICES_ANALYSIS.md)（架构演进方向）

---

## 1. 背景与目的

本项目当前的 Web 层是 Streamlit Dashboard（`src/observability/dashboard/`，约 2.3k 行），定位为本地运维/调试控制台：单用户、与核心管线同进程直调、无认证。评估中的演进方向是：

1. 按 backlog `dashboard-api-migration.md` 迁移为 FastAPI + 前端工程（不含 Chat）；
2. 在此之上增加 **Chat 功能**（多轮对话 + RAG），这是 backlog 提案未覆盖的新能力。

上游 sirchmunk 自带一套完整的 WebUI + Chat 实现（FastAPI + WebSocket 后端、Next.js 14 静态导出前端、单端口托管），本文对其逐层盘点：哪些可直接移植、哪些只取设计、哪些明确不抄，并标注与本项目红线（进程隔离、stdout 纪律）的衔接方式。

结论先行：**多轮对话、消息协议、FastAPI 边界、前端交互四类缺口 sirchmunk 均有可参考的实现；唯一不能照搬的是其假流式（主 RAG 路径需自行做真流式改造）。**

## 2. Sirchmunk WebUI 架构总览

```text
Next.js 14 静态导出（web/ → build 产物）
  页面：chat（首页）/ knowledge / history / monitor / settings / graph
  渲染：ReactMarkdown + KaTeX + Mermaid；Tailwind CSS
  状态：GlobalContext + useQuestionReducer
        │
        │ HTTP / WebSocket（Bearer token）
        ▼
FastAPI（src/sirchmunk/api/main.py，239 行）
  ├─ auth 中间件 + CORS + SecurityHeaders + 健康检查
  ├─ 路由：chat / search / knowledge / settings / history / monitor / files
  └─ 静态托管 SPA（work_path/.cache/web_static）+ SPA 路由回退（单端口模式）
        │
        ▼
直接 import sirchmunk.search.AgenticSearch   ← 本项目不可照抄此层（红线）
```

关键差异点：sirchmunk 的 API 层与搜索引擎**同进程直连**；本项目的对应通道是 `src/libs/agentic_search/http_backend.py`（HTTP 调 sidecar），复用时只能搬模式、不能搬这条 import。

## 3. Chat 实现流程（`api/chat.py`，1,621 行）

每轮对话：

```text
WS /api/v1/chat（鉴权 + 连接数上限 100）
 → 会话管理：内存 chat_sessions cache + DuckDB HistoryStorage 持久化
 → _build_chat_history()          按 max_turns=10 / max_tokens≈32k 预算从新到旧裁剪
 → _filter_relevant_history()     一次轻量 LLM 调用判断话题漂移，漂移则丢弃历史
 → _rewrite_query_with_context()  一次轻量 LLM 调用把带指代问句改写为自包含检索 query
 → 按 feature flags 路由 4 模式：纯聊天 / RAG / Web 搜索(mock) / RAG+Web
 → _run_rag_search()              AgenticSearch + WS 实时日志回调（检索过程可见）
 → 错误分类（_classify_error，覆盖 openai 异常族）→ 瞬态错误整管线重试 1 次
   → 最终失败降级到纯聊天模式
 → stream 帧逐词推送（假流式，见 §5）→ sources 帧（rag + references）→ result 帧
 → 会话与消息写回持久层
```

多轮三步法（预算裁剪 → 话题相关性过滤 → 查询改写，`chat.py:137-238`）是整份代码中对本项目价值最高的部分：它正是本项目 `src/core/query_engine` 目前完全缺失的能力，且只依赖 LLM 调用、不依赖 sidecar 内部结构——用主 venv 的 `src/libs/llm/` 即可等价重写。

## 4. 可复用资产清单

### 4.1 后端（重写为本项目代码，非 import）

| 资产 | 位置 | 对本项目的作用 | 复用方式 |
|------|------|----------------|----------|
| 多轮三步法：历史裁剪 / 话题过滤 / 查询改写 | `chat.py:137-238` | 补齐 Chat 最大功能缺口；只依赖 LLM 抽象 | 等价重写进主 venv（新 chat 模块或 query_engine 前置层） |
| WS/SSE 消息协议：`session`/`log`/`status`/`stream`/`sources`/`search_complete`/`error`/`result` 帧 + `task_id` 分组 + `is_streaming` 标记 + 0.5s 日志去重窗口 | `chat.py:389-470` | 项目已有 SX5 的 SSE 帧解析（`search_stream`），协议可统一成一套 | 取帧定义与语义，传输层按 §6 决策 |
| 错误分类 + 瞬态重试 + 降级链（RAG 失败 → 纯聊天，附用户可读文案） | `chat.py:50-76, 900-940` | 当前 `SearchService` 无降级概念 | 直接移植思路与异常映射表 |
| FastAPI 骨架：auth 中间件、CORS、健康检查、SPA 静态托管 + 路由回退、路由组合 | `api/main.py` | 与 backlog P2（只读接口先行）完全对齐，可作为 P2 的实现参照 | backlog 立项后照此实现 |
| 安全模式：`verify_ws_token`、路径白名单/严格校验、限流器、审计日志 | `api/security.py`（313 行） | 本项目已有 PathGuard（agentic 请求层），WebUI 层还需会话/文件类防护，二者互补 | 模式对照，接口命名对齐本项目风格 |
| 会话存储模型：`chat_sessions` + `chat_messages` 两表，内存 DuckDB + 定期写回 | `api/components/history_storage.py` | Chat 会话持久化的现成 schema | **只用 schema，不抄实现**：本项目已有三处 SQLite，且 SX5.3 实测过 DuckDB 线程问题，改用 SQLite 更稳 |

### 4.2 前端（React/Next.js——先过 §6 决策点）

| 资产 | 位置 | 说明 |
|------|------|------|
| Chat 主页面：ReactMarkdown + KaTeX 数学渲染 + Mermaid 图表 + sources 折叠面板 + 会话侧栏 + 搜索建议 | `web/app/page.tsx`（1,432 行） | 交互设计完整，是 Chat UI 的现成参考稿 |
| API client：base URL 三态（环境变量/同源/自动探测）、auth header、ws URL 构造 | `web/lib/api.ts`（85 行） | 极简，任何技术栈都能直接搬 |
| 会话状态：`GlobalContext` + `useQuestionReducer` | `web/context/`、`web/hooks/` | 流式消息、任务分组的 reducer 设计 |
| 辅助组件：`FileBrowser` / `FileUpload` / `CollectionBrowser` / `ClusterGraph` / `Mermaid` | `web/components/` | 对应本项目 Dashboard 的数据浏览器/上传/知识簇可视化职能 |

### 4.3 运维形态

- **单端口部署**：前端构建产物由 FastAPI 挂静态目录托管，SPA 404 回退到对应 `.html`（`main.py` 尾部）——backlog 的 Vue 方案同样适用，省掉独立前端服务。

## 5. 明确不抄的清单

1. **假流式**：`chat.py:1259-1269` 拿到完整答案后 `response.split()` 逐词推送 + `sleep(0.05)` 模拟打字效果。Chat 体验的流式要求应通过真改造满足：主 RAG 路径的 LLM 调用与 Response Builder 改为 generator 式（这是 backlog 未覆盖、立项时需新列的功能项）。**检索过程日志的实时流是真的**（经 log 回调），这部分值得保留。
2. **Web 搜索模式**：`_perform_web_search` 返回硬编码 mock 数据，无可复用实现。本项目若不做 Web 搜索，Mode 3/4 直接裁掉。
3. **Tkinter 文件选择器**（`chat.py:263-320` 起）：依赖本地桌面环境（还专门处理了 macOS Cocoa 初始化陷阱），WebUI 场景应改浏览器上传，跳过。
4. **DuckDB 会话存储实现**：其「内存 + 写回」是为规避多进程文件锁；本项目单实例 + SQLite 更简单，且规避 SX5.3 已证的 DuckDB 线程开销（每连接约 10 线程）。
5. **同进程直连搜索引擎**：`chat.py` 直接 `import sirchmunk.search.AgenticSearch`，违反本项目进程隔离红线。Chat 中的 sidecar 检索必须走 `src/libs/agentic_search/http_backend.py` HTTP 通道（与 SX5 相同）。

## 6. 立项前需拍板的两个决策

| 决策 | 选项 A | 选项 B | 影响 |
|------|--------|--------|------|
| 前端技术栈 | **Vue 3**（守 backlog `dashboard-api-migration.md` 既定方案） | **React/Next 14**（直接复用 sirchmunk 前端代码与交互） | 选 React 意味着修订 backlog 的技术选型；选 Vue 则 sirchmunk 前端只作交互参考稿，Chat UI 需重写 |
| Chat 传输通道 | **SSE**（项目已有 SX5 基建：`search_stream`、Dashboard `run_stream`） | **WebSocket**（sirchmunk 方案，双向、多消息类型更自然） | Chat 的多消息类型（log/sources/result）用 SSE 的 event 类型同样可表达；守 SX5 可少维护一套通道 |

建议：两决策与 backlog P1–P6 合并，形成一份 WebUI + Chat 的独立规格提案（走 `docs/backlog/` 提案流程），Chat 相关功能项（多轮三步法、会话存储、真流式、sources 呈现契约、多轮评估）与迁移项（P1–P6）分阶段解耦，迁移收口后再启动 Chat。

## 7. 功能改动映射（立项规格的候选任务来源）

| 功能缺口（上一轮评估结论） | sirchmunk 参考实现 | 备注 |
|---------------------------|-------------------|------|
| 多轮会话管理 | `chat.py` 三步法 + `history_storage.py` | 主 venv 重写，LLM 抽象用 `src/libs/llm/` |
| 主链路真流式 | 无（只有假流式 + 真检索日志流） | 需自研：LLM stream + Response Builder 改造 |
| 引用面向人的呈现 | `sources` 帧结构 + 前端折叠面板 | MCP tool 的 `structuredContent` 是给 Agent 的，需另定 UI 契约 |
| 应用服务边界 / API 层 | `api/main.py` 骨架 | 对齐 backlog P1/P2 |
| 会话持久化 | `history_storage.py` schema | 换 SQLite 实现 |
| 多轮质量评估 | 无 | golden 集为单轮设计，需新方法（可入 backlog 候选池） |
