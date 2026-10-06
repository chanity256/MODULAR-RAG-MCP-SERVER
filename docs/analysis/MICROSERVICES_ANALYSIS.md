# Microservices 架构分析与演进建议

> 版本：0.1 — 基于当前代码盘点的架构判断
>
> 基线：`clean-start` 分支阶段 A–I 完成状态（PDF Ingestion / Hybrid Search / MCP Server / Dashboard / Evaluation / E2E 均已落地）

---

## 目录

- [1. Microservices 是什么](#1-microservices-是什么)
- [2. 当前项目的架构判断：模块化单体](#2-当前项目的架构判断模块化单体)
- [3. 项目中与微服务相关的设计点](#3-项目中与微服务相关的设计点)
- [4. 演进与优化方向](#4-演进与优化方向)
- [5. 一句话总结](#5-一句话总结)

---

## 1. Microservices 是什么

微服务（Microservices）是一种架构风格：把一个应用拆成多个**小而独立的服务**，每个服务：

- **独立进程运行**，独立部署、独立扩缩容
- 通过**网络协议**（HTTP / gRPC / 消息队列）通信，而非进程内函数调用
- 每个服务**自己持有数据**（Database per Service），不共享数据库
- 单个服务故障不拖垮其他服务（故障隔离）

对立面是**单体架构**（Monolith）：所有模块跑在一个进程里，通过函数调用协作。

两者的本质区别不在代码怎么组织，而在**部署单元**：

| 维度 | 单体 | 微服务 |
|------|------|--------|
| 部署单元 | 1 个进程 | N 个独立进程 |
| 模块通信 | 进程内函数调用 | 网络调用（RPC / 消息） |
| 数据存储 | 通常共享一个库 | 每服务私有存储 |
| 扩容 | 整体复制 | 按服务精准扩容 |
| 运维复杂度 | 低 | 高（需要服务发现、网关、链路追踪） |

---

## 2. 当前项目的架构判断：模块化单体

**结论：本项目是一个设计良好的模块化单体（Modular Monolith），不是微服务，但到处埋着微服务的"接缝"。**

依据（基于当前代码盘点）：

- `src/` 下各模块（`ingestion` / `core` / `observability` / `libs`）通过**抽象接口 + 工厂**在**同一进程内**组装，模块间是函数调用，没有网络边界。
- Chroma 以**嵌入式库**方式运行（`data/db/chroma`），BM25 稀疏检索同样在进程内。数据与计算同进程，是典型单体特征。
- 全部行为由 `config/settings.yaml` 驱动，配置加载后进程内完成组件装配（配置驱动的依赖注入）。

**核心洞察**：本项目把"**逻辑分离**"做得非常到位（每个环节都有抽象接口、可插拔替换），微服务问的是"**物理分离**"。抽象接口就是未来的切分线——想拆的时候沿着 `BaseEmbedding`、`BaseVectorStore`、`BaseLLM` 这些缝切即可，无需重写。这正是好架构的标志：单体优先（Monolith First），按需再拆。

---

## 3. 项目中与微服务相关的设计点

| 项目设计 | 架构属性 | 说明 |
|---------|---------|------|
| `src/` 各模块接口 + 工厂，同进程组装 | 单体（逻辑拆分） | 拆分前的必要准备，已完成 |
| LLM / Embedding / Reranker 多 provider（Azure / OpenAI / Ollama） | **真正的微服务调用方** | 远程模型服务就是别人拆好的微服务；`BaseLLM` 等抽象层相当于防腐层（Anti-Corruption Layer） |
| MCP Server 通过 stdio 暴露 tools | **服务边界** | 但走 stdio 而非网络：一个客户端独占一个进程，无法多客户端共享 |
| Streamlit Dashboard 与 MCP Server 两个进程，靠 `logs/traces.jsonl` 文件共享数据 | 已是"两个部署单元" | 但用文件通信而非 API，多实例场景失效 |
| 配置预留 `vector_store.backend: qdrant \| pinecone` | 服务化预留位 | 一旦切换，向量存储即成为独立服务 |
| ingestion 链路的降级模式（chunk_refiner 失败自动回退规则模式） | 微服务韧性模式（Fallback） | 思路可推广到对外部 provider 的熔断降级 |

---

## 4. 演进与优化方向

按投入产出比排序：

### 4.1 MCP 传输层从 stdio 升级到 streamable-http（最优先）

**现状**：stdio 传输意味着一个客户端独占一个进程，无法远程访问、无法多客户端共享实例。

**收益**：
- 多个 MCP 客户端（Copilot / Claude / 自研 Agent）共享一个服务实例
- 服务可远程部署，是容器化、服务化的前提

**这是最"微服务"的一步。**

### 4.2 Ingestion 与查询链路拆离

**现状**：重 PDF 解析与查询服务在同一进程，摄取大文件会拖慢查询响应。

**方向**：引入任务队列（轻量起步即可），把 ingestion 变成后台 worker 服务，查询服务无阻塞。

**注意取舍**：`OBSIDIAN_RAG_SPEC.md` 的 M3 明确把"进程内零后台服务"当卖点（对标 Analogy 的全离线方案）。因此这是**按需取舍项**而非必选项——单机离线场景保留零后台；多用户/服务化场景再拆。

### 4.3 向量库独立成服务

**现状**：嵌入式 Chroma，且 **MCP Server 与 Dashboard 两个进程同时访问同一个 `data/db/chroma` 目录**，存在锁冲突与数据一致性隐患。

**方向**：切换到独立部署的 Qdrant / Milvus。抽象层（`BaseVectorStore`）已就绪，只差实现。

**额外收益**：独立扩容（向量检索往往是最先需要垂直扩容的环节）、消除双进程并发访问隐患。

### 4.4 Dashboard 与 Server 之间改用 API 通信

**现状**：靠 `logs/traces.jsonl` 文件共享，单机可行，多实例部署即失效。

**方向**：
- 短期：Server 暴露 traces 查询 API
- 长期：接入 OpenTelemetry，把可观测性升级为微服务标准做法（统一 tracing / metrics / logging）

### 4.5 服务治理补齐（网络化之后）

一旦服务走出 localhost，需要补：

- **鉴权**：API key / Token
- **限流**：保护后端 LLM provider 配额
- **健康检查**：liveness / readiness probe
- **熔断降级**：把 ingestion 已有的 Fallback 模式推广到对外部 LLM provider 的调用链

### 4.6 容器化编排

docker-compose 一键拉起：

```
MCP Server（streamable-http） | Dashboard | Qdrant | Ollama
```

每个组件一个容器，天然是一组微服务的小型演练场，同时保留 `ollama + chroma` 的全离线单机形态作为降级配置。

---

## 5. 一句话总结

本项目的可插拔架构已经完成了"拆微服务"最难的准备工作（接口隔离、配置驱动装配），剩下的只是按需做物理拆分。对当前体量，**保持单体 + 预留切分能力是正确选择**；若要在简历或面试中讲微服务演进，4.1（HTTP 传输）、4.2（ingestion 拆离）、4.3（向量库服务化）是三个最可落地、最能讲出深度的演进点。
