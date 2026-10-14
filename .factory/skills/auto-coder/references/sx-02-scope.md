## 2. 范围

### 2.1 In Scope

| 期 | 模块 | 优先级 | 一句话目标 |
|----|------|--------|-----------|
| SX0 | 规格勘误与基线冻结 | 承诺 | gap analysis v0.2、冻结规格勘误、golden 集补编译可覆盖题、基线固化 |
| SX1 | telemetry 解析 → trace + Dashboard | 承诺 | 白名单键解析，`record_stage` 打点，agentic 元数据面板 |
| SX2 | 请求参数透传 | 承诺 | `max_depth`/`enable_dir_scan`；`llm_fallback` 恒 `false` 显式锁定 |
| SX3 | 编译工具 + 编译收益实验 | 承诺 | `scripts/sirchmunk/compile.sh`；门控真实 LLM 实验（文档类题子集），正负结果均记录 |
| SX4 | 知识簇复用开启与评估 | 承诺 | env 模板 + preflight 预下载；重复/改写查询评估 + 非命中查询回归检查 |
| SX5 | SSE 流式（Dashboard DEEP） | 可选（门控：SX0.4 基线显示 DEEP 有实际使用价值；**与 GX 无关**） | `search_stream` 消费 `/api/v1/search/stream`，MCP 保持同步 |

### 2.2 Out of Scope（明确不做）

- **知识演化（evolver）**：HTTP/CLI 均不可达，开启须改上游源码（违反本项目与上游双方纪律）。
- **`llm_fallback`**：无文档时让 LLM 凭自身知识回答，与「引用必须可溯源」冲突；SX2 中恒发 `false`。
- **FILENAME_ONLY 模式**：沿用冻结规格 2.2 决定。
- **chat/knowledge/history/monitor/files 路由与 WebUI**：sirchmunk 自身产品层，Dashboard 已有对应职能。
- **compile 的 HTTP/SDK 集成**：只做 CLI 包装。
- **任何上游源码改动**。

---
