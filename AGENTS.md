# AGENTS.md — Modular RAG MCP Server

## 开发文档参考（开始编码任务前必读）

**编写代码之前，先阅读 [docs/frozen/](docs/frozen/) 下的已冻结规格**：DEV_SPEC_frozen.md 是主规格（架构设计、模块契约、测试方案的权威来源）；SIRCHMUNK_INTEGRATION_SPEC_frozen.md 是 sidecar 整合的完整设计与收口记录。改动某模块前先查对应 spec 中该模块的契约、验收标准与既有测试语义。规格中标注的链接路径均相对仓库根。

其余文档**按需选读**，不必预先通读：

- `docs/frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md` — SX 系列 Sirchmunk 能力补齐（已收口冻结：telemetry 消费、参数透传、compile/复用实验裁决、SSE 流式；改动 agentic_search / sidecar 脚本前先读，遗留事项见 `docs/backlog/sirchmunk-extension-followups.md`）。
- `docs/frozen/INGESTION_ROBUSTNESS_SPEC_frozen.md` — IR 系列摄取健壮性 + Markdown 摄取（IR0–IR7 已收口；改动摄取链路前先读其 7.1 实现偏差）。
- `docs/specs/WEBUI_CHAT_SPEC.md` — WC 系列 WebUI 前后端迁移 + Chat（已立项开发中：FastAPI + Next 静态导出、多轮对话 + RAG 有据回答、真流式；改动 `src/api/` / `src/core/chat/` / `frontend/` / Dashboard 服务层前先读，注意 D1 前端选型与 §1.2 对分析文档的勘误）。
- `docs/analysis/` — 架构分析与演进方向（微服务分析、优化路线图）。
- `docs/reference/USAGE_GUIDE.md` — 使用速查（启动、配置、sidecar 用法）。
- `docs/backlog/` — 候选功能与待开发规格（OBSIDIAN V 系列的 M3/M4 可选增强在此待排期，其 M1/M2 已由 IR7 交付；新提案约定见其 README）。

**[docs/MOC.md](docs/MOC.md) 是 docs/ 目录的索引文档（MOC, Map of Content）**：在 docs/ 下增删文档后，必须同步更新 MOC.md 的对应条目；根目录 README 的「开发文档」导航表同步维护。

## 常用命令

```bash
source .venv/bin/activate            # 主项目 venv

# 测试（全量离线，零网络，默认必须全绿）
pytest tests -q                      # ~1706 passed / 3 skipped
pytest tests/unit/test_xxx.py -q     # 单文件
pytest tests/unit/test_xxx.py::test_name   # 单用例

# 门控的真实集成测试（默认跳过，仅显式开启才触网）
RAG_LLM_INTEGRATION=1 pytest ...
RAG_E2E_INTEGRATION=1 pytest ...
RAG_AGENTIC_INTEGRATION=1 pytest tests/e2e/test_agentic_mcp.py -v   # 需 sidecar 已启动

# CLI
python scripts/ingest.py --path data/documents/ --collection default   # 摄取
python scripts/query.py --query "..." --verbose                        # 查询
python scripts/evaluate.py                                             # 评估
python scripts/start_dashboard.py                                      # Dashboard (localhost:8501)

# MCP Server（stdio）
PYTHONPATH=src python -m mcp_server.server
```

测试的 `pytest.ini_options` 在 `pyproject.toml`（`pythonpath=["src"]`，markers: unit/integration/e2e）。

## 架构（模块化单体，可插拔）

分层依赖自上而下，见 DEV_SPEC 5.1 架构图：

- **`src/mcp_server/`** — MCP 协议层：stdio JSON-RPC server（`server.py`）+ tools（`query_knowledge_hub` / `list_collections` / `get_document_summary`，`agentic_search` 仅 `agentic_search.enabled` 时注册）。Tool 层做参数校验（越权 → `-32602`）、懒初始化、trace 记录。
- **`src/core/`** — 业务核心：`settings.py`（dataclass 配置 + 校验，配置缺失走默认、非法值 fail-fast）、`query_engine/`（查询预处理 → Dense + BM25 双路召回 → RRF 融合 → 可选 Rerank → Response Builder）、`types.py`（Document/Chunk/ChunkRecord 契约）。
- **`src/ingestion/`** — 摄取管线：Loader → Splitter → Transform → Embed → Upsert，含 DocumentManager（跨 4 存储协调删除）与 SHA256 增量跳过。
- **`src/libs/`** — 可插拔抽象层：每类组件一个 `Base*` 抽象 + 工厂函数（`llm/embedding/splitter/loader/vector_store/reranker/agentic_search`），由 `config/settings.yaml` 配置驱动装配。切换后端 = 改配置，不改业务代码。
- **`src/observability/`** — TraceContext（JSON Lines 写 `logs/traces.jsonl`，`method`/`provider` 字段驱动 Dashboard 动态渲染）+ Streamlit Dashboard（`pages/` 七页面 + `services/` 业务封装；页面层禁止直接 import Pipeline/核心组件）。

存储：Chroma（嵌入 `data/db/chroma/`，dense 向量 + chunk 原文 + metadata）、BM25 JSON 索引、图片 SQLite（`data/db/image_index.db`）、摄取历史 SQLite（SHA256 去重）。

## 关键纪律

- **离线确定性测试**：单元/集成测试零网络、零真实模型，外部依赖一律注入 fake（`httpx.MockTransport`、fake LLM/embedding 后端）；真实模型/真实 sidecar 冒烟必须走上述环境变量门控。
- **Sirchmunk sidecar 进程隔离红线**：主 venv 永不 `import sirchmunk`，只经 `src/libs/agentic_search/http_backend.py` HTTP 调用独立进程（默认 `enabled: false`）。设计原因与源码实证见 SIRCHMUNK_INTEGRATION_SPEC 第 4 章。
- **双登记约束**：pipeline 支持的扩展名在 `src/ingestion/pipeline.py` 与 `scripts/ingest.py` 两处登记，由 drift guard 单测锁死，新增格式必须同步。
- **stdout 纪律**：MCP server 的 stdout 只输出合法 JSON-RPC，日志一律走 stderr；e2e 有逐行断言。
- **规格与排期**：已收口规格（DEV_SPEC 的 A–I、SIRCHMUNK 的 S 系列、IR 的 IR0–IR7）任务表不在其中新增任务；全新功能走 `docs/backlog/` 提案 → 立项后在 `docs/specs/` 建独立规格或经明确指令重开既有规格（格式对齐 SIRCHMUNK/OBSIDIAN 先例，任务编号避免冲突）。
