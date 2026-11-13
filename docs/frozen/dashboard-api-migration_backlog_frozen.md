# Dashboard 前后端分离迁移提案（Dashboard API Migration）

> **起源**：从 [FUTURE_OPTIMIZATION_ROADMAP.md](../reference/FUTURE_OPTIMIZATION_ROADMAP.md) 原第 1–5 章拆分而来（2026-10-07）。
> **状态**：**已立项**——P1–P6 并入 [frozen/06_WEBUI_CHAT_SPEC_frozen.md](../frozen/06_WEBUI_CHAT_SPEC_frozen.md)（WC 系列，与 Chat 合并规格，2026-10-14）；其第 3 章 D1 **推翻本文的前端选型**（Vue 3 → React/Next 静态导出，直接移植 sirchmunk `web/`），P4 的 SSE 任务事件改为 WebSocket。本文正文保留作决策依据，P1 拆解与验收命令由 WC1 沿用。
> **原则**：小步迁移、保持主链路可用、每个阶段独立验收。

<!-- Note: 为何从路线图拆分、章节编号为何保持 6–10 — 见 .agents/notes/implemented/process/2026-10-07-roadmap-migration-split-to-backlog.md -->

## 1. 决策三问（对齐路线图第 10 章）

1. **它解决了哪个已经观察到的问题？**——Streamlit Dashboard 的五条现实限制（见第 2 章）：UI 与后端进程耦合、交互能力有限、能力无法被其他客户端复用、认证与并发难扩展、长任务状态不稳定。
2. **是否有更简单、改动更小的方案？**——已通过 `ConfigService` / `DataService` / `TraceService` 完成部分业务封装（路线图近期建议中的 P1 即沿此继续）；但 HTTP API 与多端复用、长任务状态模型等目标在单进程 Streamlit 内无法达成，拆分是必要的。
3. **如何确认它确实有效？**——第 5 章 P1–P6 每个实施包都有独立验收标准与回归命令；最终以 P6 双轨功能等价验证决定 Streamlit 是否退役，而不是由技术选型决定。

## 2. 当前架构与主要限制

当前 Dashboard 使用 Streamlit 实现。页面代码同时承担：

- UI 渲染与交互；
- 页面状态和筛选逻辑；
- Python Service 调用；
- 文件上传与本地写入；
- Ingestion、Evaluation 等同步任务编排；
- 错误和进度展示。

项目已经通过 `ConfigService`、`DataService`、`TraceService` 等模块完成了部分业务封装，但 Dashboard 与这些服务仍运行在同一个 Python 进程中，没有独立 HTTP API。

这种模式适合本地演示和快速开发，但存在以下限制：

1. 前端交互、视觉设计和复杂图表能力有限；
2. UI 与后端生命周期耦合，难以独立开发和部署；
3. 浏览器、移动端或其他客户端无法复用 Dashboard 能力；
4. 远程访问、认证、权限控制和并发任务管理不易扩展；
5. 长时间任务依赖 Streamlit rerun 和当前请求，任务状态不够稳定。

## 3. 目标架构

建议将项目逐步拆分为三个清晰边界：

```text
Vue 3 + Vite Dashboard
        │
        │ HTTPS / JSON / SSE
        ▼
FastAPI Application API
        │
        ├── Dashboard Services
        ├── Ingestion / Evaluation orchestration
        └── Authentication / task status
        │
        ▼
Existing RAG Core / ChromaDB / BM25 / Trace / MCP Server
```

### 3.1 Vue 3 前端

负责：

- 页面布局、路由和组件；
- 表单校验与交互状态；
- 图表和数据可视化；
- API 请求与错误提示；
- 上传进度、任务进度和结果展示；
- 用户会话和前端权限状态。

建议技术栈：

- Vue 3 + TypeScript + Vite；
- Vue Router；
- Pinia；
- Axios 或原生 Fetch；
- Element Plus 或 Naive UI；
- ECharts；
- Vitest + Vue Test Utils；
- Playwright（关键用户流程）。

### 3.2 FastAPI 应用接口层

负责：

- 将现有 Python Service 暴露为稳定的 HTTP API；
- 请求参数与响应模型校验；
- 文件上传和图片访问；
- Ingestion、Evaluation 等长任务管理；
- SSE 或 WebSocket 进度推送；
- 认证、授权、审计和统一错误处理。

FastAPI 应保持为薄适配层，不在路由函数中重新实现检索、摄取或评估逻辑。

### 3.3 现有核心模块

以下能力原则上继续保留并复用：

- Ingestion Pipeline；
- DocumentManager；
- Hybrid Search、RRF 和 Reranker；
- Embedding、LLM、Vector Store Provider；
- Trace Collector 和持久化；
- Evaluation；
- MCP tools 和 stdio transport。

MCP Server 与 Dashboard API 服务于不同调用场景：MCP 面向 Agent/AI Host，REST API 面向 Web UI。二者应共享应用服务，而不是由 Vue 前端直接调用 MCP stdio。

## 4. Dashboard 迁移范围

| 当前 Streamlit 页面 | Vue 页面 | 后端接口方向 |
| --- | --- | --- |
| 系统总览 | Overview | 配置摘要、资产统计、健康状态、最新 Trace |
| 数据浏览器 | Data Browser | Collection、文档、Chunk、图片查询 |
| Ingestion 管理 | Ingestion | 文件上传、任务创建、进度、删除文档 |
| Ingestion 追踪 | Ingestion Traces | Trace 列表、详情、阶段耗时 |
| Query 追踪 | Query Traces | 查询筛选、召回详情、Rerank 对比 |
| 评估面板 | Evaluation | 启动评估、任务状态、指标和历史趋势 |

候选 API 可以围绕现有能力设计，并按实施包分批交付。

P2 先提供只读接口：

- `GET /api/overview`
- `GET /api/collections`
- `GET /api/documents`
- `GET /api/documents/{document_id}`
- `GET /api/images/{image_id}`
- `GET /api/traces`
- `GET /api/traces/{trace_id}`

P4 再提供写操作和任务接口：

- `DELETE /api/documents/{document_id}`
- `POST /api/ingestions`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/events`
- `POST /api/evaluations`
- `GET /api/evaluations/history`

接口名称和资源结构应在实现前通过 OpenAPI 模型进一步确认。不要简单地为每个现有 Python 方法创建一个 HTTP endpoint。

## 5. 推荐迁移阶段

为避免同时重写 UI、API 和长任务机制，迁移工作拆分为六个可独立验收的实施包。依赖顺序如下：

```text
P1 服务边界整理
        ↓
P2 FastAPI 只读接口
        ↓
P3 Vue 基础框架与只读页面
        ↓
P4 写接口与后台任务
        ↓
P5 完整页面迁移
        ↓
P6 双轨验收与 Streamlit 退役
```

### P1：稳定应用服务边界

目标：确保 Streamlit 页面与未来 FastAPI 路由可以复用同一套业务能力。

- 识别仍写在页面中的业务编排；
- 将上传保存、Ingestion 执行、Evaluation 执行等逻辑移入明确的应用服务；
- 为应用服务补充输入、输出和异常契约；
- 保持 Streamlit 行为不变。

重点范围：

- `src/observability/dashboard/pages/ingestion_manager.py`；
- `src/observability/dashboard/pages/evaluation_panel.py`；
- `src/observability/dashboard/services/`；
- `tests/unit/` 中对应的服务测试；
- `tests/e2e/test_dashboard_smoke.py`。

验收标准：

- 六个 Streamlit 页面继续通过现有冒烟测试；
- 页面不再直接创建核心 Pipeline；
- 应用服务可在无 Streamlit 环境下独立测试。

#### P1 任务拆解

**P1.1：锁定现有行为基线**

- 先运行 `tests/unit/test_dashboard_services.py`、`tests/unit/test_evaluation_panel.py` 和 `tests/e2e/test_dashboard_smoke.py`；
- 记录文件上传、逐文件失败降级、进度回调、评估器选择和评估历史展示的现有行为；
- 本阶段只重构职责边界，不改变用户可见行为、配置项和持久化格式。

验收命令：

```bash
pytest -q tests/unit/test_dashboard_services.py tests/unit/test_evaluation_panel.py tests/e2e/test_dashboard_smoke.py
```

**P1.2：提取 IngestionService**

新增 `src/observability/dashboard/services/ingestion_service.py`，集中负责：

- 将上传文件安全地保存到指定目录，保留现有 basename 清理行为；
- 按 `collection` 和 `force` 参数调用 `IngestionPipeline`；
- 聚合总文件数、Chunk 数、图片数和失败数；
- 接收框架无关的进度回调；
- 支持注入 Pipeline 或 runner，使单元测试不访问真实 Provider 和持久化目录。

新增 `tests/unit/test_ingestion_service.py`，至少覆盖：

- 多文件保存及文件名清理；
- Pipeline 参数透传；
- 多文件结果聚合；
- 单个文件失败不会阻断其他文件；
- 进度回调透传；
- 上传目录不存在时自动创建。

**P1.3：简化 Ingestion 页面**

修改 `src/observability/dashboard/pages/ingestion_manager.py`：

- 删除页面内 `_default_run_ingest`；
- 将文件保存和摄取执行委托给 `IngestionService`；
- 将 Streamlit UploadedFile 转换为框架无关的文件名与 bytes 后再传入服务；
- 页面只保留输入读取、Streamlit 进度组件适配和结果展示；
- 保留当前 `data_service`、runner 和临时目录注入能力，避免破坏 Dashboard 冒烟测试。

修改 `src/observability/dashboard/services/__init__.py`，导出新增服务。若外部代码需要从 `observability.dashboard` 导入，则同步更新 `src/observability/dashboard/__init__.py`。

回归命令：

```bash
pytest -q tests/unit/test_ingestion_service.py tests/e2e/test_dashboard_smoke.py -k ingestion
```

**P1.4：提取 EvaluationService**

新增 `src/observability/dashboard/services/evaluation_service.py`，迁移并封装：

- `evaluator_for_backend` 的 custom、ragas、composite 路由；
- Golden Test Set 相对路径解析；
- `HybridSearch`、Evaluator 和 `EvalRunner` 的构建；
- `test_set_path`、`top_k` 和 `filters` 参数校验及透传；
- `EvalReport` 返回，不包含任何 Streamlit session state 操作。

新增 `tests/unit/test_evaluation_service.py`，覆盖：

- 三种评估后端选择；
- 未知后端错误；
- 相对路径和绝对路径解析；
- Top-K 与 Collection filter 透传；
- runner 异常继续向调用方传播，不在服务层转换为 UI 文案。

**P1.5：简化 Evaluation 页面**

修改 `src/observability/dashboard/pages/evaluation_panel.py`：

- 删除评估器工厂和默认 EvalRunner 构建逻辑；
- 通过 `EvaluationService` 执行评估；
- 保留表单、spinner、错误展示和 session state 历史记录；
- 将原 `tests/unit/test_evaluation_panel.py` 中的纯业务测试迁移到 `tests/unit/test_evaluation_service.py`；
- 页面渲染继续由 Dashboard 冒烟测试覆盖。

回归命令：

```bash
pytest -q tests/unit/test_evaluation_service.py tests/e2e/test_dashboard_smoke.py -k evaluation
```

**P1.6：P1 全量验收**

- 确认 `streamlit` 只出现在页面层，不出现在新增服务；
- 确认页面不再直接导入 `IngestionPipeline`、`HybridSearch` 或 `EvalRunner`；
- 运行 Dashboard 服务、页面冒烟和完整测试；
- 将 Ingestion 与 Evaluation 两次职责迁移分为独立提交，便于回滚和审查。

验收命令：

```bash
! rg -n "import streamlit" src/observability/dashboard/services
! rg -n "IngestionPipeline|HybridSearch|EvalRunner" \
  src/observability/dashboard/pages
pytest -q tests/unit/test_dashboard_services.py \
  tests/unit/test_ingestion_service.py \
  tests/unit/test_evaluation_service.py \
  tests/e2e/test_dashboard_smoke.py
pytest -q
```

### P2：新增 FastAPI 只读接口

目标：优先形成可被独立客户端调用的低风险只读接口。

- 增加 API 入口和版本前缀；
- 使用 Pydantic 定义请求与响应；
- 提供统一错误格式；
- 实现健康检查、配置摘要、文档、Collection、图片和 Trace 接口；
- 生成并校验 OpenAPI 文档。

建议新增：

- `src/api/app.py`：FastAPI 应用入口；
- `src/api/routes/`：按资源划分路由；
- `src/api/schemas/`：Pydantic 请求与响应模型；
- `src/api/errors.py`：统一异常映射；
- `tests/integration/api/`：HTTP 接口集成测试。

验收标准：

- API 集成测试覆盖正常、空数据、非法参数和资源不存在；
- API 不直接依赖 Streamlit；
- MCP Server 的 stdio 行为和测试保持不变。

### P3：搭建 Vue 3 Dashboard 与只读页面

目标：先完成只读页面，验证技术栈和接口设计。

- 建立独立 `frontend/` 工程；
- 配置 Vue 3、TypeScript、Vite、Vue Router 和 Pinia；
- 封装 API Client 和统一错误处理；
- 建立通用布局、导航、加载、空数据和错误状态组件；
- 依次迁移系统总览、Ingestion Trace 和 Query Trace。

验收标准：

- 三个页面的数据含义与 Streamlit 一致；
- 关键筛选条件可通过 URL 或前端状态恢复；
- 图表正确表达现有 Trace 数据；
- 前端构建、类型检查和单元测试通过。

### P4：写接口与后台任务

目标：支持文件、摄取和评估等写操作，并避免长时间任务绑定单个 HTTP 请求。

- 增加文件上传和文档删除接口；
- 增加 Ingestion 与 Evaluation 任务提交接口；
- API 创建任务后返回 `task_id`；
- 使用 `pending → running → succeeded / failed` 状态模型；
- 持久化任务状态，使页面刷新后可以恢复；
- 通过 SSE 推送任务阶段、进度、结果和错误；
- 对写操作增加参数校验和冲突处理。

验收标准：

- 上传、删除、摄取和评估接口具备集成测试；
- 请求断开不会导致任务状态丢失；
- 同一个 `task_id` 可以查询最终结果；
- SSE 中断后可以通过任务查询接口恢复状态。

初期可以采用进程内任务执行器和轻量持久化，只有在确实需要多实例部署时再引入 Redis、Celery、RQ 等外部组件。

### P5：完成剩余页面迁移

目标：在写接口和任务模型稳定后覆盖全部 Streamlit Dashboard 能力。

建议迁移顺序：

1. 数据浏览器；
2. Ingestion 管理；
3. 评估面板。

- 数据浏览器支持 Collection 筛选、文档详情、Chunk 和图片预览；
- Ingestion 管理支持上传、删除、任务进度和结果展示；
- 评估面板支持参数选择、运行状态、指标明细和历史趋势；
- 使用 Playwright 覆盖关键用户流程。

验收标准：

- Vue Dashboard 覆盖全部六个现有页面；
- 上传、删除、摄取和评估主流程 E2E 测试通过；
- 所有页面具备加载、空数据、错误和重试状态；
- 写操作具有明确的确认、成功和失败反馈。

### P6：双轨验收与 Streamlit 退役

目标：在确认 Vue Dashboard 覆盖现有能力后再移除旧 UI。

- 一段时间内同时保留 Streamlit 和 Vue；
- 对比六个页面的数据和操作结果；
- 核对 API 与 MCP 两条调用链互不影响；
- 为 Vue 主流程增加 E2E 测试；
- 更新启动脚本、README 和部署说明；
- 明确无用户依赖旧界面后再删除 Streamlit 页面和依赖。

退役门槛：

- 六个页面达到功能等价；
- Python、API 和前端测试全部通过；
- 本地启动和部署流程已有替代方案；
- Streamlit 不再承担独有的业务逻辑；
- 删除 Streamlit 后不影响 MCP stdio 服务。

不建议在 FastAPI 与 Vue 页面尚未达到功能等价前直接删除 Streamlit。
