# Modular RAG MCP Server 后续优化路线图

## 1. 文档目的

本文记录项目后续可能的优化方向，重点讨论将现有 Streamlit Dashboard 逐步迁移为 **Vue 3 + Vite + FastAPI** 前后端分离架构，同时补充 RAG、MCP、部署、安全、可观测性和测试方面的演进建议。

这些内容是候选路线，而不是必须一次完成的需求。实施时应继续遵循“小步迁移、保持主链路可用、每个阶段独立验收”的原则。

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

第一阶段 API 可以围绕现有能力设计：

- `GET /api/overview`
- `GET /api/collections`
- `GET /api/documents`
- `GET /api/documents/{document_id}`
- `DELETE /api/documents/{document_id}`
- `GET /api/images/{image_id}`
- `POST /api/ingestions`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/events`
- `GET /api/traces`
- `GET /api/traces/{trace_id}`
- `POST /api/evaluations`
- `GET /api/evaluations/history`

接口名称和资源结构应在实现前通过 OpenAPI 模型进一步确认。不要简单地为每个现有 Python 方法创建一个 HTTP endpoint。

## 5. 推荐迁移阶段

### 阶段 1：稳定应用服务边界

目标：确保 Streamlit 页面与未来 FastAPI 路由可以复用同一套业务能力。

- 识别仍写在页面中的业务编排；
- 将上传保存、Ingestion 执行、Evaluation 执行等逻辑移入明确的应用服务；
- 为应用服务补充输入、输出和异常契约；
- 保持 Streamlit 行为不变。

验收标准：

- 六个 Streamlit 页面继续通过现有冒烟测试；
- 页面不再直接创建核心 Pipeline；
- 应用服务可在无 Streamlit 环境下独立测试。

### 阶段 2：新增 FastAPI API

目标：形成可被独立客户端调用的后端接口。

- 增加 API 入口和版本前缀；
- 使用 Pydantic 定义请求与响应；
- 提供统一错误格式；
- 实现文档、Trace、配置等只读接口；
- 再实现上传、删除、摄取、评估等写接口；
- 生成并校验 OpenAPI 文档。

验收标准：

- API 集成测试覆盖主要成功和失败路径；
- API 不直接依赖 Streamlit；
- MCP Server 的 stdio 行为和测试保持不变。

### 阶段 3：搭建 Vue 3 Dashboard

目标：先完成只读页面，验证技术栈和接口设计。

建议迁移顺序：

1. 系统总览；
2. Ingestion Trace；
3. Query Trace；
4. 数据浏览器；
5. Ingestion 管理；
6. 评估面板。

前三个页面以读取和可视化为主，风险较低；后三个页面涉及文件上传、写操作和长任务，应在 API 契约稳定后迁移。

验收标准：

- 页面具备加载、空数据、错误和重试状态；
- 关键筛选条件可通过 URL 或前端状态恢复；
- 图表与现有 Trace 数据含义一致；
- 前端单元测试覆盖关键组件和数据转换。

### 阶段 4：长任务与实时进度

目标：避免长时间操作绑定单个 HTTP 请求。

- Ingestion 和 Evaluation 改为任务提交模式；
- API 返回 `task_id`；
- 后端持久化任务状态；
- 前端通过 SSE 获取阶段、进度和错误；
- 页面刷新后可以恢复任务状态。

初期可以采用进程内任务队列，只有在确实需要多实例部署时再引入 Redis、Celery、RQ 等外部组件。

### 阶段 5：双轨验收与 Streamlit 退役

目标：在确认 Vue Dashboard 覆盖现有能力后再移除旧 UI。

- 一段时间内同时保留 Streamlit 和 Vue；
- 对比六个页面的数据和操作结果；
- 为 Vue 主流程增加 E2E 测试；
- 更新启动脚本、README 和部署说明；
- 明确无用户依赖旧界面后再删除 Streamlit 页面和依赖。

不建议在 FastAPI 与 Vue 页面尚未达到功能等价前直接删除 Streamlit。

## 6. 其他可选优化方向

### 6.1 RAG 检索质量

- 建立可版本化的 Golden Test Set；
- 持续记录 Recall@K、MRR、Hit Rate、回答忠实度；
- 对 Chunk 大小、重叠、Top-K、RRF 和 Reranker 参数进行可重复实验；
- 增加查询改写、元数据过滤和置信度拒答；
- 针对表格、标题层级和跨页内容优化结构化 Chunking；
- 增加文档版本与时效性过滤，减少新旧知识混用。

原则：先通过评估数据证明瓶颈，再增加更复杂的检索链路。

### 6.2 数据摄取

- 扩展 DOCX、Markdown、HTML、PPTX、XLSX 等 Loader；
- 增强增量更新、删除检测和失败重试；
- 为摄取任务增加幂等键和可恢复状态；
- 保存解析器版本、Chunk 策略版本和 Embedding 模型版本；
- 对失败文档提供可诊断的阶段信息。

### 6.3 MCP 能力

- 评估迁移至官方 MCP SDK，减少自维护协议细节；
- 在保留 stdio 的基础上按需求增加 Streamable HTTP transport；
- 增加更细粒度的 Agentic RAG tools，例如文档预览、关键词检索和事实核查；
- 为 Tool schema、错误响应和多模态内容增加契约测试；
- 保持 MCP 接口与 Dashboard REST API 职责分离。

### 6.4 安全与权限

- API 默认只监听本地地址；
- 远程部署时启用 HTTPS、认证和授权；
- 对上传类型、文件大小、路径和文件名进行严格校验；
- 防止目录穿越和任意文件读取；
- API Key 仅保存在服务端，不进入前端构建产物；
- 对删除、重新摄取、运行评估等写操作记录审计日志；
- 多用户场景再引入 Collection 或 Namespace 级权限。

### 6.5 部署与运维

- 提供后端 Dockerfile 和健康检查；
- 前端生成静态资源，可由 Nginx 或 CDN 托管；
- 开发环境通过 Vite proxy 转发 API；
- 生产环境明确数据卷、配置、日志和模型缓存位置；
- 增加备份与恢复文档；
- 只有出现明确扩展需求时才拆分微服务。

### 6.6 可观测性

- 为 API 请求补充 request ID，并与现有 trace ID 关联；
- 统一结构化日志字段；
- 增加错误率、任务耗时、检索耗时和 Provider 调用指标；
- 明确敏感数据脱敏策略；
- 需要跨进程或生产环境追踪时再评估 OpenTelemetry。

### 6.7 测试与工程质量

- 保留 Python Unit / Integration / E2E 分层；
- 增加 FastAPI API 契约测试；
- 增加 TypeScript 类型检查、ESLint 和前端单元测试；
- 使用 Playwright 覆盖上传、查询 Trace、运行评估等关键流程；
- CI 分别执行 Python 与前端检查；
- 对 OpenAPI schema 进行版本变更检查，避免前后端契约静默破坏。

## 7. 优先级建议

### 近期：低风险、高收益

1. 固化 Dashboard 应用服务边界；
2. 增加只读 FastAPI API；
3. 用 Vue 实现系统总览和 Trace 页面；
4. 补充 API 契约测试。

### 中期：完成前后端分离

1. 迁移数据浏览、Ingestion 和 Evaluation；
2. 实现任务状态与 SSE；
3. 增加认证、操作审计和前端 E2E；
4. 完成双轨功能等价验证。

### 长期：按真实需求选择

1. Streamlit 退役；
2. 容器化和远程部署；
3. 多用户与权限隔离；
4. 多格式深度解析；
5. Agentic RAG 与 MCP HTTP transport；
6. 分布式任务队列和完整遥测。

## 8. 暂不建议立即实施的事项

- 为了“架构先进”直接拆分多个微服务；
- 在单机任务足够时提前引入 Redis、Celery 和消息队列；
- 同时重写 Dashboard、RAG Core 和 MCP Server；
- 让 Vue 前端直接访问 ChromaDB、日志文件或服务端密钥；
- 让 Dashboard 通过 MCP stdio 代替正常的应用 API；
- 在新 Dashboard 验收前删除 Streamlit。

## 9. 总体验收目标

完成主要迁移后，应达到以下状态：

- Vue Dashboard 与 Python 后端可以独立开发、测试和部署；
- FastAPI 提供稳定、文档化、可测试的应用接口；
- 现有 RAG、Ingestion、Evaluation 和 MCP 能力得到复用，而不是重复实现；
- 长任务支持进度查询，页面刷新不会丢失状态；
- MCP Client 与 Web Dashboard 两条调用链互不干扰；
- 项目仍可在本地以较低成本运行；
- Streamlit 是否退役由功能等价验证决定，而不是由技术选型决定。

## 10. 决策原则

每项优化开始前应回答三个问题：

1. 它解决了哪个已经观察到的问题？
2. 是否有更简单、改动更小的方案？
3. 如何通过测试、指标或用户流程确认它确实有效？

如果暂时无法回答，应将该方向保留在候选列表中，而不是立即进入实现。
