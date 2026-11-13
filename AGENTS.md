# AGENTS.md — Modular RAG MCP Server

## 开发文档参考（开始编码任务前必读）

**编写代码之前，先阅读 [docs/frozen/](docs/frozen/) 下的已冻结规格**：01_DEV_SPEC_frozen.md 是主规格（架构设计、模块契约、测试方案的权威来源）；03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md 是 sidecar 整合的完整设计与收口记录。改动某模块前先查对应 spec 中该模块的契约、验收标准与既有测试语义。规格中标注的链接路径均相对仓库根。

其余文档**按需选读**，不必预先通读：

- `docs/frozen/08_WEBUI_PARITY_SPEC_frozen.md` — WP 系列 WebUI 双轨遗留差异收口（WP0–WP2 已收口冻结：Ingestion / Query 追踪列表服务端 offset 翻页、评估历史内联 SVG 趋势图、评估提交路径 422 预检；改动 `frontend/` 的 Ingestion / Query Traces、Evaluation 页面或 `POST /evaluations` / `EvaluationService` 前先读，#3 展示细节接受项决议见 `docs/frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md`）。
- `docs/frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md` — SX 系列 Sirchmunk 能力补齐（已收口冻结：telemetry 消费、参数透传、compile/复用实验裁决、SSE 流式；改动 agentic_search / sidecar 脚本前先读，遗留事项见 `docs/backlog/sirchmunk-extension-followups_backlog.md`）。
- `docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md` — IR 系列摄取健壮性 + Markdown 摄取（IR0–IR7 已收口；改动摄取链路前先读其 7.1 实现偏差）。
- `docs/frozen/06_WEBUI_CHAT_SPEC_frozen.md` — WC 系列 WebUI 前后端迁移 + Chat（WC0–WC10 已收口冻结：FastAPI + Next 静态导出、多轮对话 + RAG 有据回答、真流式；Web UI 是主界面，Streamlit 已退役（见 `docs/frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md`）；改动 `src/api/` / `src/core/chat/` / `frontend/` / Dashboard 服务层前先读，注意 D1 前端选型与 §1.2 对分析文档的勘误；Web UI 使用与配置见 `docs/reference/USAGE_GUIDE.md` 第 10 章（Streamlit 退役记录见 10.9），七页等价核验见 `docs/analysis/WEBUI_PARITY_AUDIT.md`）。
- `docs/frozen/07_LOGGING_SPEC_frozen.md` — LG 系列日志分层与按日切分（LG0–LG7 已收口冻结：Trace 按日落盘 `logs/traces/`、应用运行日志 `logs/app/`、保留期清理、测试经 `RAG_LOG_ROOT` 隔离；改动 `core/trace/persist.py` / `observability/logger.py` / TraceService / 进程入口日志前先读，注意其 7.1 实现偏差）。
- `docs/frozen/09_WEBUI_SIDECAR_SPEC_frozen.md` — WS 系列 WebUI sidecar 补齐（已收口冻结：`scripts/sidecar_manager.py` 共享托管编排 + `start_api.py --manage-sidecar`、`core/chat/progress.py` 共享「sidecar 日志 → search_log 帧」转换、门控 `WS /api/v1/search/ws`（`api.deep_progress_stream` 默认关闭——SX5.3 上游线程泄漏规避）、Playground Deep 前端流式接入；改动 `scripts/start_api.py` / 搜索流式端点 / Playground Deep 前端前先读，承接自 webui-parity-followups 台账（已晋升为 `docs/frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md`）的 #4/#1）。
- `docs/analysis/` — 架构分析与演进方向（微服务分析、优化路线图）。
- `docs/reference/USAGE_GUIDE.md` — 使用速查（启动、配置、sidecar 用法）。
- `docs/backlog/` — 候选功能与待开发规格（OBSIDIAN V 系列的 M3/M4 可选增强在此待排期，其 M1/M2 已由 IR7 交付；新提案约定见其 README）。
- `docs/frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md` — SR 系列 Streamlit 退役（已收口冻结）：物理删除 Streamlit 页面层 / `start_dashboard.py` / `streamlit` 依赖 / Streamlit-only 配置与页面级测试，文档与技能镜像同步；`observability/dashboard/services/` 及其测试保留（API 层依赖），sidecar 托管唯一入口为 `start_api.py --manage-sidecar`；改动 `src/core/settings.py` 配置模型或 docs/ 链接前先读。
- `docs/specs/AI_NATIVE_SDLC_SPEC.md` — SD 系列 AI-Native SDLC 流程升级（**开发中**，SD1 已交付（含 `fork` 上真实 CI 与分支保护）：`ci.yml` / 禁网 / 红线脚本 / CODEOWNERS；SD2 review 规则已交付：`REVIEW.md` + `.factory/skills/review-guidelines/`；SD6 skill 单一来源已交付：`.factory/skills/` 为来源、`scripts/sync_skills.py` 生成 `.claude/` / `.github/` 镜像，auto-coder 含 plan 规则 / `Fix-Rounds` trailer / D1 门控的 PR 流程，QA 计划已清理（SD3.1），QA ↔ pytest 对照表与离线缺口补测已交付（SD3.2/SD3.3：`.factory/skills/qa-tester/QA_PYTEST_MAP.md`、`tests/e2e/test_qa_offline_gaps.py`）；SD5 过程度量已交付：`scripts/measure_process.py` + `docs/analysis/process-metrics.md` 1.0 基线；SD7 规格结构校验已交付：`tests/unit/test_spec_structure.py`（`docs/specs/` 与编号 ≥10 的冻结规格须有任务表验收标准列、测试方案、In/Out of Scope，新规格照此写）；D1/D2 已终裁、D6 baseline PR 已合入；自动 review（SD2.3）暂不接入）：前置决策 D1–D6、CI 确定性门（禁网 pytest、`scripts/check_redlines.py`、CODEOWNERS，承接已存档的 ci-pipeline 提案）、AI PR review、QA 对照与 agent evals、定时闭环、过程度量、skill 单一来源 + per-task plan + PR 化；改动 `.github/`、skill 镜像、`.factory/hooks*`、`evals/` 或执行 SD 任务前先读。不改 `src/` 业务行为。

**[docs/MOC.md](docs/MOC.md) 是 docs/ 目录的索引文档（MOC, Map of Content）**：在 docs/ 下增删文档后，必须同步更新 MOC.md 的对应条目；根目录 README 的「开发文档」导航表同步维护。

## 常用命令

```bash
source .venv/bin/activate            # 主项目 venv

# 测试（全量离线，零网络，默认必须全绿）
pytest tests -q                      # 看计数用 pytest tests -o addopts="" -q；当前 2516 passed / 11 skipped / 1 xfailed（干净 clone 未 build frontend/out 时 2513 / 14 / 1；xfail 为 QA N-05 已知缺陷）
pytest tests/unit/test_xxx.py -q     # 单文件
pytest tests/unit/test_xxx.py::test_name   # 单用例
pytest tests/e2e_web -q              # Web UI 假后端启动冒烟（frontend/out 未构建则 skip）

# CI 等价命令（.github/workflows/ci.yml 逐字同源；pytest-socket 禁网只放行 loopback，NO_PROXY 防经本地系统代理绕出）
NO_PROXY='*' pytest tests -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1
python scripts/check_redlines.py     # 红线静态检查（sirchmunk import / llm_fallback / MCP stdout）
python scripts/sync_skills.py        # skill 镜像同步：改 .factory/skills/ 后运行（--check 只校验；drift guard 见 test_skill_mirrors.py）
python scripts/measure_process.py    # 过程度量：离线读 git 历史出基线（--json；--rev 指定统计终点），结果维护在 docs/analysis/process-metrics.md

# 门控的真实集成测试（默认跳过，仅显式开启才触网）
RAG_LLM_INTEGRATION=1 pytest ...
RAG_E2E_INTEGRATION=1 pytest ...
RAG_AGENTIC_INTEGRATION=1 pytest tests/e2e/test_agentic_mcp.py -v   # 需 sidecar 已启动

# CLI
python scripts/ingest.py --path data/documents/ --collection default   # 摄取
python scripts/query.py --query "..." --verbose                        # 查询
python scripts/evaluate.py                                             # 评估
python scripts/start_api.py --serve-ui                                 # Web UI（主界面，127.0.0.1:8600；api.enabled 默认 false，需改配置或加 --force）

# Web UI 前端（Node 22）
(cd frontend && npm ci && npm run build)                               # 构建 frontend/out/，start_api.py --serve-ui 托管
(cd frontend && npm run typecheck && npm run lint && npm test)         # 前端静态检查 + vitest
(cd frontend && npx playwright install chromium && npm run e2e)        # Playwright e2e（opt-in，离线假后端，需先 build）

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
- **`src/observability/`** — TraceContext（JSON Lines 按日写 `logs/traces/traces-YYYY-MM-DD.jsonl`，`method`/`provider` 字段驱动 Web UI 动态渲染）+ `dashboard/services/`（`src/api/` 共享服务层；服务层禁止直接 import Pipeline/核心组件）。

存储：Chroma（嵌入 `data/db/chroma/`，dense 向量 + chunk 原文 + metadata）、BM25 JSON 索引、图片 SQLite（`data/db/image_index.db`）、摄取历史 SQLite（SHA256 去重）。

## 关键纪律

- **离线确定性测试**：单元/集成测试零网络、零真实模型，外部依赖一律注入 fake（`httpx.MockTransport`、fake LLM/embedding 后端）；真实模型/真实 sidecar 冒烟必须走上述环境变量门控。
- **Sirchmunk sidecar 进程隔离红线**：主 venv 永不 `import sirchmunk`，只经 `src/libs/agentic_search/http_backend.py` HTTP 调用独立进程（默认 `enabled: false`）。设计原因与源码实证见 SIRCHMUNK_INTEGRATION_SPEC 第 4 章。
- **双登记约束**：pipeline 支持的扩展名在 `src/ingestion/pipeline.py` 与 `scripts/ingest.py` 两处登记，由 drift guard 单测锁死，新增格式必须同步。
- **stdout 纪律**：MCP server 的 stdout 只输出合法 JSON-RPC，日志一律走 stderr；e2e 有逐行断言。
- **提交流程（D1）**：`fork`（chanity256）的 `main` 受分支保护：必须经 PR，`python` / `frontend` 两个 CI job 必须通过，管理员同样受限；新工作从 `fork/main` 开 `feat/<series>-<task>` 分支，推送用 SSH（`git@github.com:chanity256/MODULAR-RAG-MCP-SERVER.git`，`gh` token 无 `workflow` scope，HTTPS 推不了 workflow 改动），以 merge commit 合入；`origin`（上游）只读不推。CODEOWNERS 只起提示作用（唯一 owner 无法自审，见 SD 规格 §3.1）。
- **skill 单一来源**：共享 skill（auto-coder / docs-governance / qa-tester / resume-writer / skill-creator）只改 `.factory/skills/`，再运行 `python scripts/sync_skills.py` 生成 `.claude/skills/` 与 `.github/skills/` 镜像，手改镜像会被 `test_skill_mirrors.py` 拦下；QA 计划与进度只在 `.factory/skills/qa-tester/` 保留一份；`review-guidelines` 只在 `.factory/`。复杂任务的 plan 放 `.agents/plans/`（格式见其 README）。

- **docs/ 自动守卫**：Droid 会话由项目级 hook（`.factory/hooks.json`）在每轮结束时检查 docs/ 改动：结构漂移（坏链、MOC 漏登记，规则见 `tests/unit/test_docs_links.py`）必须修，语义同步按映射表提醒；修复流程见 [docs/MOC.md](docs/MOC.md)「命名与流转规范」。
- **规格与排期**：已收口规格（DEV_SPEC 的 A–I、SIRCHMUNK 的 S 系列、IR 的 IR0–IR7、WC 的 WC0–WC10、LG 的 LG0–LG7、SX 的 SX0–SX5、WP 的 WP0–WP2、WS 的 WS0–WS4、SR 的 SR0–SR6）任务表不在其中新增任务；全新功能走 `docs/backlog/` 提案 → 立项后在 `docs/specs/` 建独立规格或经明确指令重开既有规格（格式对齐 SIRCHMUNK/OBSIDIAN 先例，任务编号避免冲突）。
