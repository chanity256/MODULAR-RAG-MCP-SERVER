# 项目使用指南（含 Sirchmunk Agentic 检索）

> 本文档是速查手册，覆盖：① 主项目（Indexed RAG）基本用法；② Sirchmunk sidecar 的启动与 `agentic_search` 工具用法；③ Web UI（FastAPI + Next）与 Chat（第 10 章，Streamlit 已退役，见 [10.9](#109-streamlit-dashboardlegacy)）。
> 详细设计见 [README.md](../../README.md)、[03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md)。

---

## 目录

1. [架构一句话](#1-架构一句话)
2. [主项目基本用法](#2-主项目基本用法)
3. [Sirchmunk 是什么、什么时候用](#3-sirchmunk-是什么什么时候用)
4. [Sirchmunk 首次搭建（一次性）](#4-sirchmunk-首次搭建一次性)
5. [日常启动与关闭](#5-日常启动与关闭)
6. [agentic_search MCP 工具用法](#6-agentic_search-mcp-工具用法)
7. [旁路对比与测试](#7-旁路对比与测试)
8. [常见问题排查](#8-常见问题排查)
9. [安全边界（必读）](#9-安全边界必读)
10. [Web UI 与 Chat](#10-web-ui-与-chat)

---

## 1. 架构一句话

两套检索引擎互补，不互相替换：

```
MCP Client（Copilot / Claude）
    │ stdio JSON-RPC
    ▼
Modular RAG MCP Server（主 venv，本仓库）
    ├─ query_knowledge_hub   → Dense + BM25 + RRF + Rerank（需先摄取，毫秒级）
    ├─ agentic_search        → HTTP 调 Sirchmunk sidecar（无需摄取，秒级~数十秒）
    ├─ list_collections / get_document_summary
    ▼
Sirchmunk sidecar（独立 venv + 独立进程，127.0.0.1:8584 + Bearer）
    └─ rg / rga 词法召回 + LLM 迭代探索，直接搜活文件
```

- **主链路（Indexed RAG）**：必须先 `ingest`，支持 PDF / Markdown / DOCX/PPTX/XLSX/HTML（后四者需装可选依赖），低延迟、确定性高。
- **Agentic（Sirchmunk）**：不建索引、指定路径直接搜，支持 MD/代码/JSON/YAML/CSV/HTML/PDF/DOCX/XLSX/PPTX/EPUB，每次查询消耗 2~N 次 LLM 调用。
- **进程边界**：主项目 venv 永远不 import sirchmunk，只通过 HTTP 调用。

---

## 2. 主项目基本用法

前置：`source .venv/bin/activate`，在仓库根目录执行。

### 2.1 目录结构速览

树形总览（格式对齐 [DEV_SPEC 5.2](../frozen/01_DEV_SPEC_frozen.md)）：

```
Modular-RAG-MCP-Server/
│
├── AGENTS.md                 # AI 编码代理项目纪律（模块地图 + 关键红线，改动前必读）
├── README.md                 # 项目入口：功能总览、快速开始、开发文档导航
├── main.py                   # 最小启动入口（加载配置自检；真正的 MCP 入口在 src/mcp_server/server.py）
├── pyproject.toml            # 依赖与 pytest 配置（pythonpath / markers；可选组 ingest-formats）
├── requirements.txt          # pip 依赖清单
├── board.html                # 本地生成的笔记看板页面（agent-notes-board 产物，非核心功能）
│
├── config/                   # 配置文件目录
│   ├── settings.yaml         # 主配置：LLM / Embedding / Splitter / Rerank / 检索 / ingestion / agentic 全部在此
│   └── prompts/              # Prompt 模板（chunk_refinement / image_captioning / rerank）
│
├── src/                      # 源代码主目录（分层依赖自上而下，见 DEV_SPEC 5.1）
│   ├── mcp_server/           # MCP 协议层：stdio JSON-RPC server + tools
│   ├── api/                  # Web API：FastAPI（鉴权 / 只读与写路由 / 任务 / Chat WS / 静态托管，见第 10 章）
│   ├── core/                 # 业务核心：settings / query_engine / chat（多轮对话）/ types 契约
│   ├── ingestion/            # 摄取管线：Loader → Split → Transform → Embed → Upsert + DocumentManager
│   ├── libs/                 # 可插拔抽象层：各组件 Base* + 工厂（loader / embedding / vector_store / agentic_search 等）
│   └── observability/        # TraceContext（JSON Lines 落盘）+ dashboard/services（API 层共享服务）
│
├── frontend/                 # Web UI：Next 静态导出（构建产物 frontend/out/ 由 start_api.py --serve-ui 托管）
│
├── scripts/                  # CLI 脚本
│   ├── ingest.py             # 摄取（含 --sync / --prune / --dry-run / --retry-failed，见 2.2）
│   ├── query.py              # 查询（--verbose / --agentic，见 2.3）
│   ├── evaluate.py           # RAG 评估（golden set + composite 指标）
│   ├── start_api.py          # Web UI / API 启动器（主界面，见第 10 章）
│   └── sirchmunk/            # sidecar 环境与旁路对比工具（setup_venv / start_sidecar / preflight / compare）
│
├── tests/                    # 离线测试套件（unit / integration / e2e；fixtures/ 为测试夹具与金标准集）
│
├── data/                     # 本地数据（不入库 git）
│   ├── documents/            # 待摄取语料目录
│   ├── db/                   # 存储：chroma/（向量）、bm25/（稀疏索引）、image_index.db、ingestion_history.db、sirchmunk/
│   └── images/               # 摄取抽取的图片文件（按 doc_hash 分目录）
│
├── logs/                     # 日志（见 2.8）：traces/ 按日 trace、app/ 按日应用日志 + sidecar 日志与对比报告
└── cache/                    # 运行时缓存（captions / embeddings / processing，可安全清除重建）
```

### 2.2 摄取（Ingest）

把文档放入 `data/documents/`，然后：

```bash
python scripts/ingest.py --path data/documents/ --collection default
python scripts/ingest.py --path data/documents/ --collection default --force   # 跳过增量判定强制重摄
python scripts/ingest.py --path data/documents/ -v                             # 查看逐阶段进度

# 同步 / 重试（--sync / --prune / --retry-failed 三者互斥）
python scripts/ingest.py --path data/documents/ --collection default --sync            # 摄取新增/修改 + 清理已删除
python scripts/ingest.py --path data/documents/ --collection default --prune           # 只清理已删除，不摄取
python scripts/ingest.py --path data/documents/ --collection default --sync --dry-run  # 只打印计划，不写存储
python scripts/ingest.py --collection default --retry-failed                           # 重跑历史中失败的文件（--path 可选）
```

- **格式**：`.pdf` 开箱即用；`.md` / `.markdown` 走 MarkdownLoader（Obsidian 语法感知：frontmatter / tags / wikilinks / 笔记嵌入；`.obsidian/`、`.trash/`、`templates/` 目录默认排除，`ingestion.source_filters` 可定制）；`.docx` / `.pptx` / `.xlsx` / `.html` 需 `pip install '.[ingest-formats]'`（markitdown，可选依赖）。
- **增量判定**：按 `(source_path, collection)` + 文件哈希；内容变更后重摄取会先清理旧版本残留。
- **组合约束**：`--dry-run` 必须配合 `--sync` / `--prune`；`--force` 不能与 `--sync` / `--prune` 同用。
- **`--dry-run` 计划分类**：new / modified / unchanged / deleted / stale（stale = 历史记录的 embedding 版本与当前配置不同，按 `ingestion.version_policy.on_embedding_mismatch` 告警并跳过，不自动重建）。
- **退出码**：`0` 全部成功或跳过；`1` 有文件失败；`2` 用法或配置错误。
- **可选配置**：`ingestion.retry`（缺省不重试）、`ingestion.version_policy`（缺省 `auto`），样例见 `config/settings.yaml` 注释。

产物：Chroma 向量库（`data/db/chroma/`）、BM25 索引（`data/db/bm25/`）、图片索引与摄取历史（`data/db/`）。摄取历史表为 `ingestion_history_v2`，首次打开旧库时自动迁移，旧表改名 `ingestion_history_legacy_backup` 保留（需回滚时手工改回表名）。设计见 [04_INGESTION_ROBUSTNESS_SPEC_frozen.md](../frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md)。

### 2.3 查询

```bash
python scripts/query.py --query "如何配置 Azure？" --verbose
python scripts/query.py --query "bm25 scoring" --collection default --top-k 5
python scripts/query.py --query "部署密钥" --no-rerank   # 跳过精排
# 启用 agentic 第三路并打印中间结果（触发状态/证据文件/Token/耗时）；
# 需 agentic_search.enabled=true（见第 4 节），否则退出码 2
python scripts/query.py --query "ING-2044 错误码在哪定义" --agentic --verbose
```

### 2.4 启动 MCP Server（stdio）

```bash
PYTHONPATH=src python -m mcp_server.server
```

默认暴露 3 个工具：`query_knowledge_hub`、`list_collections`、`get_document_summary`。开启 `agentic_search.enabled` 后多出第 4 个工具 `agentic_search`。

### 2.5 MCP Client 配置

**GitHub Copilot（VS Code）** — `.vscode/mcp.json`：

```json
{
  "servers": {
    "modular-rag": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/Modular-RAG-MCP-Server",
      "env": { "PYTHONPATH": "/absolute/path/to/Modular-RAG-MCP-Server/src" }
    }
  }
}
```

Claude Desktop 同理，配置块为 `mcpServers`。

### 2.6 Web UI 与评估

主界面是 Web UI（FastAPI + Next 静态导出，含 Chat 与七个管理页面），完整说明见[第 10 章](#10-web-ui-与-chat)：

```bash
(cd frontend && npm ci && npm run build)   # 首次或前端有改动时
python scripts/start_api.py --serve-ui     # http://127.0.0.1:8600（api.enabled 默认 false，见 10.1）
```

```bash
python scripts/evaluate.py                 # 默认 golden set + composite 后端
python scripts/evaluate.py --evaluator custom --top-k 5   # 纯离线指标
python scripts/evaluate.py --retrieval-mode hybrid+agentic --agentic-trigger always   # agentic A/B（需 sidecar 启用）
```

### 2.7 Query Playground「在线检索」页（S4）

Web UI「检索」分组 → Query Playground：三模式在线查询，不写代码、不敲 CLI 也能检索。Deep 模式走同步检索、**没有实时进度日志**（有意规避 SX5.3 线程泄漏，见 [10.6](#106-agentic_progress_stream-与-sidecar-线程泄漏警告)）。

- **模式选择**：Normal / Fast / Deep 三模式，默认 Normal；Fast 需 `agentic_search.enabled: true`，Deep 另需 `agentic_search.allow_deep: true`（门控拒绝时返回统一错误，见 10.5）。
- **表单**：查询文本 + Top-K；collection 输入仅在 Normal 模式显示。
- **结果**：Markdown 答案 + 引用表（来源/页码/分数）；Fast/Deep 额外展示 Agentic 答案、模式、Token、LLM 调用次数、耗时与证据文件。
- **trace 联动**：每次检索自动写入 trace，到「Query Traces」页可查看各阶段耗时。

### 2.8 日志与追踪

日志分两层，都按**本地日期**切分，默认保留 30 天（设计见 [LOGGING_SPEC_frozen](../frozen/07_LOGGING_SPEC_frozen.md)）：

| 层 | 位置 | 内容 | 谁读 |
|---|---|---|---|
| Trace 日志 | `logs/traces/traces-YYYY-MM-DD.jsonl` | 每次查询（default / agentic / chat）与摄取的分阶段耗时、method、provider | Web UI / Dashboard 追踪页 |
| 应用运行日志 | `logs/app/<component>-YYYY-MM-DD.log` | 进程启动、WARNING / ERROR、降级重试、异常堆栈、uvicorn 服务日志 | 人工排障 |

`component` 为 `api`（`start_api.py`）、`mcp`（MCP server）、`ingest` / `query` / `evaluate`（CLI）。应用日志与 stderr 内容相同；uvicorn 访问日志只走 stderr，不落盘。MCP server 的 stdout 仍只输出 JSON-RPC。

```yaml
observability:
  log_file: ./logs/traces   # 目录 = 按日切分；以 .jsonl 结尾 = 旧的单文件模式
  app_log_dir: ./logs/app
  retention_days: 30        # 0 = 不清理
  logging:
    log_level: INFO         # 应用日志级别：DEBUG | INFO | WARNING | ERROR | CRITICAL
```

- **清理**：进程启动时与跨天切换文件时，删除 `logs/traces/` 和 `logs/app/` 下超出保留期的日文件（只匹配 `<前缀>-YYYY-MM-DD` 命名，其他文件不动）。
- **常用查看**：`tail -f logs/app/api-$(date +%F).log`；`grep -h '"routes": "chat"' logs/traces/traces-*.jsonl`。
- **`RAG_LOG_ROOT`**：设置后，配置里的相对日志路径相对它解析。测试套件自动把它指向临时目录，所以跑 `pytest` 不会写仓库 `logs/`；临时手动试验也可以 `RAG_LOG_ROOT=/tmp/x python scripts/query.py ...`。
- **旧的单文件迁移**：升级前的 `logs/traces.jsonl` 不再被读取。先 `python scripts/migrate_traces.py --dry-run` 看统计，再去掉 `--dry-run` 执行：按日拆分到 `logs/traces/`，剔除测试产生的记录，原文件改名为 `traces.jsonl.migrated` 保留。

---

## 3. Sirchmunk 是什么、什么时候用

Sirchmunk 是 modelscope 开源的「**无索引 Agentic 检索**」引擎：用 `rg`/`rga` 做词法召回，用 LLM 做查询分析、多路证据融合与 ReAct 式迭代探索，最后输出带出处的答案。

**适合的场景**（与主链路互补）：

| 场景 | 原因 |
|------|------|
| 新文件/未摄取的文件立即检索 | 无需 ingest，指定路径即可搜 |
| 主链路未覆盖的格式（代码/配置等） | 主链路支持 PDF/Markdown 与 DOCX/PPTX/XLSX/HTML，代码与配置文件等 sirchmunk 覆盖更多格式且无需摄取 |
| 精确标识符（错误码/函数名/配置键） | grep 精确匹配天然强项 |
| 跨文件多跳问题 | ReAct 迭代探索弥补单轮 Top-K 上限 |

**代价**：FAST 模式约 2–5s，DEEP 模式数十秒；每次查询 2~N 次 LLM 调用（有 Token 成本）；结果非确定性。

**当前状态**：主项目侧适配（S1）已完成并合入；sidecar 已在 2026-10-05 实际跑通并完成旁路对比，**闸门 G0 未通过**，`agentic_search` 保持 `enabled: false` 默认关闭。G0 终裁后经显式指令重启：S2（HybridSearch 可选第三路 + `--agentic` CLI）与 S3（source 级 A/B 评估框架 + Dashboard agentic 展示）均已交付（2026-10-06），但**默认关闭口径不变**，真实 sidecar A/B 实测数据未执行（受 `RAG_AGENTIC_INTEGRATION` 门控与 G0 结论约束）。结论与数据见 `03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md` 第 7 章与 S3 阶段进度备注。

---

## 4. Sirchmunk 首次搭建（一次性）

### 4.1 预装 ripgrep 工具

```bash
brew install ripgrep ripgrep-all
```

必须手动预装 `rg` 和 `rga`，否则 sirchmunk 构造时会自行从 GitHub 下载（离线即失败）。

### 4.2 搭建独立 venv

```bash
git clone https://github.com/modelscope/sirchmunk.git ~/Desktop/Project/Job-Search/sirchmunk
bash scripts/sirchmunk/setup_venv.sh
```

脚本默认：仓库在 `~/Desktop/Project/Job-Search/sirchmunk`，venv 在本项目根的 `.venv-sirchmunk/`，Python 3.12（退回 3.11），并锁定上游 commit `b314e11`。两个路径都可以用参数覆盖：

```bash
bash scripts/sirchmunk/setup_venv.sh <sirchmunk仓库目录> <venv目录>
```

### 4.3 配置环境变量

```bash
cp scripts/sirchmunk/env.example .env-sirchmunk
```

编辑 `.env-sirchmunk`，至少填写：

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` | OpenAI 兼容端点的 Key（sirchmunk **不支持**原生 Azure，用 DeepSeek/DashScope/OpenAI 等兼容端点） |
| `LLM_BASE_URL` / `LLM_MODEL_NAME` | 模板默认 DeepSeek `https://api.deepseek.com` + `deepseek-v4-flash`（上游自动关闭思考）。**不要用 `glm-5.3-flash`**：它强制思考且 sidecar 无法关闭，单次 LLM 调用可达数十到上百秒 |
| `SIRCHMUNK_API_TOKEN` | 生成一个随机 token，**必须与主项目 `agentic_search.api_token` 一致** |
| `SIRCHMUNK_WORK_PATH` | 默认 `./data/db/sirchmunk`，每进程独占，勿与其他进程共享 |
| `SIRCHMUNK_ENABLE_CLUSTER_REUSE` | 保持 `false`，可跳过 embedding 模型下载与常驻内存（torch 已随 sidecar venv 安装）。**不要删掉这一行**：上游对未设置按开启处理。需要开启时改为 `true`，再跑 `preflight.sh` 预下载模型。**开启前须知**（SX4.2 实测）：只有 FAST 原题重问能命中（约 30 ms、0 token），换个说法就达不到 0.85 阈值；只有 FAST 会保存知识簇，DEEP 只能复用 FAST 存下的簇；上游不检查文件是否已修改，文件改动后 FAST 和 DEEP 都会返回旧答案，需清空 `work_path/.cache/knowledge/` 才会失效。语料经常变动时保持关闭 |
| `LENS_HARD_TOKEN_BUDGET` | 保持 `true`，客户端断连后防止成本泄漏 |

### 4.4 主项目配置

编辑 `config/settings.yaml` 的 `agentic_search` 节：

```yaml
agentic_search:
  enabled: true                     # 开启后 tools/list 才会出现 agentic_search
  backend: sirchmunk_http
  base_url: http://127.0.0.1:8584   # 与 sidecar 一致
  api_token: ${SIRCHMUNK_API_TOKEN} # 从环境变量读取，需在启动 MCP server 前 export
  mode: FAST                        # FAST | DEEP
  allow_deep: false                 # true 才允许工具选 DEEP（数十秒级）
  timeout_s: 30                     # 须低于 MCP 客户端工具超时
  allowed_roots:                    # 检索白名单（enabled 时必须非空）
    - ./data/documents
  denied_substrings:                # 白名单内仍强制排除
    - .env
    - .git
    - data/db
```

---

## 5. 日常启动与关闭

**Web UI 路径**：`start_api.py` 默认**不托管** sidecar；启用 agentic 时先手动起 sidecar（下方分步第 1 步），再 `python scripts/start_api.py --serve-ui`。要一条命令全搞定，加 `--manage-sidecar`（WS0）：`agentic_search.enabled=true` 时自动拉起 sidecar → 等健康 → 启动 API，退出时回收由它拉起的 sidecar（已在外部运行的只复用、不接管；agentic 关闭时只打提示不报错）。

手动分步启动（等效于上面的自动编排）：

```bash
# 1. 启动 sidecar（前台运行；建议放独立终端或 tmux）
bash scripts/sirchmunk/start_sidecar.sh

# 2. 健康检查（rg / rga / /api/v1/search/status 三连）
bash scripts/sirchmunk/preflight.sh

# 3. 启动主项目 MCP server（需 SIRCHMUNK_API_TOKEN 已 export）
export SIRCHMUNK_API_TOKEN=<你的token>
PYTHONPATH=src python -m mcp_server.server
```

- `start_sidecar.sh` 可用参数覆盖：`bash scripts/sirchmunk/start_sidecar.sh [venv目录] [env文件]`，也支持 `SIRCHMUNK_HOST` / `SIRCHMUNK_PORT` 环境变量。
- sidecar 只监听 `127.0.0.1`，带 Bearer 鉴权（无 token 请求返回 401）。
- 不用 agentic 检索时，保持 `agentic_search.enabled: false` 即可，sidecar 不需要启动，主项目零加载、零影响。

---

## 6. agentic_search MCP 工具用法

在 Copilot / Claude 中让 AI 调用 `agentic_search` 工具，参数如下：

| 参数 | 必填 | 说明 |
|------|------|------|
| `query` | 是 | 非空字符串。避免 "hi"/"test" 这类打招呼措辞——会被 sidecar 短路成闲聊，跳过检索 |
| `mode` | 否 | `FAST`（默认，2–5s）或 `DEEP`（数十秒，受 `allow_deep` 门控，未开则报 -32602） |
| `paths` | 否 | 绝对路径列表，缺省用 `allowed_roots`；传入值必须全在白名单内，越权直接返回 -32602 且**不发请求** |
| `top_k` | 否 | 正整数，返回证据条数上限 |

**返回内容**：带出处的答案（Markdown 引用 + structuredContent.citations）与证据片段。注意：证据只有字符偏移（`char_start`/`char_end`），**没有页码**（`page` 恒为空）。

**对话示例**：

> 帮我用 agentic_search 在 `data/documents` 里查一下「ING-2044 错误码在哪个文件定义的」，mode 用 FAST。

**sidecar 不可用时**：工具返回 `isError: true` 的友好提示（如连接拒绝），主 server 与其他工具不受影响。

---

## 7. 旁路对比与测试

### 7.1 Golden 问题旁路对比（真实 LLM 调用，产生费用）

```bash
python scripts/sirchmunk/compare.py \
  --base-url http://127.0.0.1:8584 \
  --token "$SIRCHMUNK_API_TOKEN" \
  --repo-root .
```

用 `tests/fixtures/agentic/golden_questions.json` 逐条直调 sidecar，输出命中文件、耗时与 Token 用量表（`--fixture` 换题集，`--json-out` 另存含 telemetry 的逐题 JSON）。

需要可复现的多轮对照（如 SX0.4 基线、SX3.2 编译实验）时，用 `baseline_round.sh` 每轮冷启动：清空知识簇与 spec 缓存 → 启动 sidecar → 两个 golden fixture 各跑一遍 → 停机。sidecar 须先停掉；输出目录放仓库外；整轮期间别让电脑睡眠（`caffeinate -dims`）。

```bash
caffeinate -dims bash scripts/sirchmunk/baseline_round.sh DEEP 1 /tmp/rounds
python scripts/sirchmunk/aggregate_rounds.py /tmp/rounds merged.json summary.md
```

### 7.3 知识编译（compile）：当前不推荐

`scripts/sirchmunk/compile.sh` 可以把 `README.md docs` 预编译成文档树与知识簇（停机护栏、路径护栏见脚本头注释）。SX3.2 实测（2026-10-14，见 `scripts/sirchmunk/sx32_compiled_results.md`）：

- FAST 在文档题上命中略升（11/18 → 13/18）、token 降约 68%，但 `.py` 题从 2/12 降到 0/12；
- DEEP 在文档题上从 14/18 降到 7/18，`.py` 题从 9/12 降到 7/12。

结论：**默认不编译**。手动编译过的话，想恢复原行为需在停机状态下移走 `SIRCHMUNK_WORK_PATH/.cache/compile` 并清空 `.cache/knowledge`，再重启 sidecar。

### 7.2 测试套件

```bash
pytest -q                                        # 全量离线（零网络，默认全绿）；看计数用 pytest tests -o addopts="" -q -p no:cacheprovider
RAG_AGENTIC_INTEGRATION=1 pytest tests/e2e/test_agentic_mcp.py -v   # 真实 sidecar 冒烟（需先启动 sidecar）
```

---

## 8. 常见问题排查

| 现象 | 原因与处理 |
|------|-----------|
| `tools/list` 里没有 `agentic_search` | `agentic_search.enabled` 未设为 `true`，检查 `config/settings.yaml` |
| 工具返回连接拒绝 / isError | sidecar 没启动或端口不对；跑 `preflight.sh` 检查 |
| 无 token 请求 401 | 请求未带 Bearer，或主项目 `SIRCHMUNK_API_TOKEN` 与 sidecar `.env-sirchmunk` 中不一致 |
| `-32602` 参数错误 | `paths` 越出 `allowed_roots`、命中 `denied_substrings`、`mode: DEEP` 但 `allow_deep: false`、或 `top_k` 非正整数 |
| preflight 报缺 `rga` | `brew install ripgrep-all`（缺 rg 同理装 `ripgrep`） |
| setup_venv 依赖解析失败 | kreuzberg 依赖预发布版本，脚本已自动加 `--pre` 重试；仍失败检查 Python 版本为 3.11/3.12 |
| compile 每个文件都报 `OutputFormat has no attribute 'PLAIN'` | sidecar venv 装到了 kreuzberg 5.x（`--pre` 回退会拉到预发布版），上游仍用 4.x 枚举名。执行 `.venv-sirchmunk/bin/pip install "kreuzberg>=4.0.0,<5"`；新版 `setup_venv.sh` 已自动锁定 |
| Token 成本异常 | 确认 `LENS_HARD_TOKEN_BUDGET=true`、`max_token_budget` / `max_loops` 未被调大；客户端断连不会取消 sidecar 执行 |
| 知识簇缓存损坏 | 多进程共享了 `SIRCHMUNK_WORK_PATH`；该目录必须每进程独占 |
| 追踪页看不到升级前的历史记录 | 旧的 `logs/traces.jsonl` 未迁移；执行 `python scripts/migrate_traces.py`（见 2.8） |

---

## 9. 安全边界（必读）

- **sirchmunk 自身的 `paths` 参数不是安全边界**：其 ReAct 的 `file_read` 可读 LLM 请求的任意绝对路径，上游 `/api/v1/search` 不做白名单校验。
- **本项目用 PathGuard 做两层防护**：
  1. 调用前：校验 `paths`，realpath 归一、拒绝符号链接逃逸、白名单外直接 `-32602`，fail-closed 不发请求；
  2. 调用后：过滤证据中落在白名单外的条目（防注入诱导泄露）。
- **信任模型**：语料内容不可信（prompt injection 可操纵 Agent 读文件），agentic 答案按不可信输入处理。
- **部署要求**：sidecar 只监听 `127.0.0.1` 并始终开启 Bearer；重度使用 DEEP 模式建议在受限用户/容器内运行。

---

## 10. Web UI 与 Chat

Web UI 是主界面：`src/api/`（FastAPI）+ `frontend/`（Next 静态导出，单端口托管）。Chat（多轮对话 + RAG 有据回答）为首页，另有七个管理页面（Overview / Ingestion Traces / Query Traces / Data Browser / Ingestion Manager / Evaluation / Query Playground），与旧 Streamlit 七页逐项对照见 [WEBUI_PARITY_AUDIT.md](../analysis/WEBUI_PARITY_AUDIT.md)（Streamlit 已于 2026-11-13 退役，对照表保留作历史记录）。设计与决策见 [06_WEBUI_CHAT_SPEC_frozen.md](../frozen/06_WEBUI_CHAT_SPEC_frozen.md)。

### 10.1 快速开始

前置：Node 22（`frontend/.nvmrc`，`engines >= 20.9`）、已 `source .venv/bin/activate`。

```bash
# 1. 构建前端（产物 frontend/out/，不入库；前端有改动需重新构建）
cd frontend && npm ci && npm run build && cd ..

# 2. 启动（单端口托管 API + UI）：http://127.0.0.1:8600
python scripts/start_api.py --serve-ui
```

注意：

- **`api.enabled` 默认 `false`**，此时 `start_api.py` 以退出码 2 拒绝启动。二选一：在 `config/settings.yaml` 设 `api.enabled: true`，或命令行追加 `--force`。
- `--serve-ui` 覆盖 `api.serve_ui`；`frontend/out/index.html` 不存在时启动即 fail-fast（提示先构建）。
- 其它覆盖：`--host` / `--port` / `--config`。
- `start_api.py` 默认**不托管 sidecar**；用 Fast/Deep（Playground）或 `agentic_fast` / `agentic_deep`（Chat）需先手动起 sidecar（第 5 节），或加 `--manage-sidecar` 让启动器自动托管（拉起 / 等健康 / 退出回收）。
- Chat 复用 `settings.yaml` 的 `llm:` 配置，hybrid 检索要求已摄取数据（第 2.2 节或 Ingestion Manager 页）。

**前端开发模式**（热更新，前后端分开启动）：

```bash
# 终端 1：后端（allowed_origins 默认已含 http://localhost:3000）
python scripts/start_api.py --force

# 终端 2：前端 dev server http://localhost:3000
cd frontend && NEXT_PUBLIC_API_BASE=http://localhost:8600 npm run dev
```

`NEXT_PUBLIC_API_BASE` 留空 = 同源（单端口托管形态，构建产物默认如此）；见 `frontend/.env.example`。该变量在构建期内联，改值后需重新 `npm run build`。

### 10.2 鉴权：`RAG_API_TOKEN`

- Web API 的 token 来自环境变量 **`RAG_API_TOKEN`**（`api.api_token: ${RAG_API_TOKEN}`），**与 `SIRCHMUNK_API_TOKEN` 完全独立**：前者会进入浏览器（localStorage + WS URL 的 `?token=`，会出现在 access log），复用将把 sidecar 凭据暴露给浏览器。两者各自轮换，勿设成同值。
- **loopback 且无 token**：允许启动、免鉴权（仅本机访问）。
- **非 loopback（如 `--host 0.0.0.0`）且无 token**：启动即 fail-fast（`ConfigError`）。远程访问必须先 `export RAG_API_TOKEN=<随机串>`。
- **占位符视为空**：环境变量缺失时配置里的字面量 `${RAG_API_TOKEN}` 会被保留，`normalize_token` 把这种 `^\$\{.+\}$` 形态、空串、纯空白一律按「未配置」处理，避免公开已知字面量成为 token。
- 配置了 token 后：HTTP 校验 `Authorization: Bearer <token>`（常数时间比较）；WS 经 `?token=` 查询参数或 header。`GET /health` 免鉴权。
- 前端：遇 401 时错误区会出现 token 输入框（填 `RAG_API_TOKEN` 的值，存入 localStorage 键 `rag_api_token`），保存后自动重试。

### 10.3 配置参考（`config/settings.yaml`）

两节均为可选节：缺失走默认、非法值启动即报错。

```yaml
api:
  enabled: false                 # 只影响 scripts/start_api.py（可用 --force 绕过）
  host: 127.0.0.1
  port: 8600
  api_token: ${RAG_API_TOKEN}    # 未解析占位符 = 无 token；host 非 loopback 时必须有真实 token
  allowed_origins: ["http://localhost:3000"]   # CORS 白名单，同时用于 Chat WS 的 Origin 校验
  serve_ui: false                # true 时托管 static_dir（等价命令行 --serve-ui）
  static_dir: ./frontend/out
  tasks_db: ./data/db/tasks.db   # 后台任务（摄取/评估）状态持久化；重启时遗留的 running 任务标为 failed/interrupted
  max_upload_mb: 100             # 单个上传文件上限，超出 413
  deep_progress_stream: false    # WS /search/ws（Playground Deep 进度日志）开关；有线程泄漏风险（10.6）

chat:
  enabled: true                  # false 时 WS 握手后以 error{UNAVAILABLE} 拒绝
  history_max_turns: 10          # 多轮历史双预算裁剪：条数
  history_max_tokens: 32000      # 与 token 估算（len/3.5）
  relevance_filter: true         # 话题漂移过滤（一次轻量 LLM 调用；失败保留历史）；省调用的逃生门
  query_rewrite: true            # RAG 检索前的指代消解改写（失败用原句）
  default_retrieval: hybrid      # hybrid | agentic_fast | agentic_deep
  max_context_chunks: 8          # 送入答案生成的 chunk 上限
  session_db: ./data/db/chat_sessions.db
  max_connections: 100           # 并发 Chat WS 连接上限，超限 error{RATE_LIMITED}
  pipeline_max_retries: 1        # 瞬态错误重试次数，仅在首个 stream 帧之前生效
  fallback_to_chat_only: false   # true 才在检索失败时降级纯聊天（grounded:false）
  agentic_progress_stream: false # true 走 sidecar 流式端点并转 search_log 帧，有线程泄漏风险（10.6）
```

### 10.4 Chat 使用

**检索模式**（每轮可选，界面上的模式选择器读 `GET /api/v1/chat/capabilities`，只列当前可用的）：

| 模式 | 路径 | 前提 |
|------|------|------|
| `hybrid`（默认） | Dense + BM25 + RRF + Rerank 取上下文，LLM 流式生成带 `[n]` 引用的答案 | 已摄取数据 + `llm` 可用 |
| `agentic_fast` | 经 `AgenticSearchTool`（PathGuard / 参数校验 / trace 原样生效）调 sidecar FAST，**sidecar 答案整块透传**，不再经本项目 LLM | `agentic_search.enabled: true` + sidecar 运行 |
| `agentic_deep` | 同上，DEEP 模式（数十秒） | 另需 `agentic_search.allow_deep: true`，否则 `error{DEEP_DISABLED}` |

**降级与 `grounded`**：

- 检索失败默认直接发 `error` 帧（引用必须可溯源）。仅 `chat.fallback_to_chat_only: true` 时降级为纯聊天：先发 `search_error` 帧，再生成答案，且终帧 `result.grounded: false`。`DEEP_DISABLED` 不降级。
- `grounded: false` = 本答案**没有检索证据与引用支撑**（纯聊天/降级路径），界面在结果区顶部显著提示；`true` = 答案基于检索结果（含空检索短路给出的固定「未命中」文案）。

**会话**：首轮不带 `session_id` 则服务端新建并下发 `session` 帧；用户消息到达即落库，助手消息在终帧落库（取消/中断时保存已生成部分，`metadata.cancelled: true`）。数据在 `chat.session_db`，重启不丢。每轮写**恰好一条** `query` trace（`metadata.routes="chat"`，含 `chat_history` / `chat_rewrite` / `chat_generation` 三个阶段），在 Query Traces 页选来源 `chat` 查看。

**WS 帧协议**（`WS /api/v1/chat`，一帧一个 JSON 对象；上行未知字段被拒绝而非忽略）：

| 方向 | type | 载荷 |
|------|------|------|
| ↑ | `user_message` | `{message, session_id?, retrieval?, collection?, top_k?}` |
| ↑ | `cancel` | 无；轮进行中到达则终止并以 `result{cancelled:true}` 收尾，已生成部分落库 |
| ↓ | `session` | `{session_id}`（新会话时） |
| ↓ | `status` | `{stage, message?}`，stage: `rewriting` / `retrieving` / `generating` / `retrying` |
| ↓ | `search_log` | `{task_id, level, message, timestamp?}`，仅 agentic 且 `agentic_progress_stream` 开启 |
| ↓ | `search_complete` / `search_error` | `{message}` |
| ↓ | `stream` | `{content}`，真流式增量（agentic 为一整块） |
| ↓ | `sources` | `{citations: [...]}`，真实 score |
| ↓ | `result` | 终帧 `{content, session_id, message_id, grounded, rewritten_query?, usage?, trace_id, cancelled?}` |
| ↓ | `error` | 终帧 `{code, message}` |

**错误码**：`AUTH`（token 缺失/错误，或 LLM 401/403）、`RATE_LIMITED`（连接超限或上游 429）、`UPSTREAM`（上游/sidecar 失败）、`DEEP_DISABLED`、`INTERNAL`；协议层另有 `BAD_REQUEST`（帧非法、会话不存在、未启用的检索模式、Origin 不允许、首帧 30s 超时、轮进行中收到 `cancel` 以外的帧）与 `UNAVAILABLE`（`chat.enabled=false`）。协议类拒绝在握手后先发 `error` 帧再以 1008 关闭，浏览器能看到原因。

**连接模型与 Origin**：沿用「**每条消息一个连接**」——前端发消息前关闭旧连接再新建，会话连续性靠 `session_id`；断开即取消。握手校验 `Origin`（须在 `api.allowed_origins` 内或与 `Host` 同源；无 `Origin` 的非浏览器客户端放行），防止无 token 的 loopback 部署被任意网页连上读取知识库。瞬态错误（连接/超时/429/5xx）重试 `pipeline_max_retries` 次并发 `status{retrying}`，**首个 `stream` 帧之后不再重试**（避免重复内容），直接发 `error` 并保存已生成部分。

### 10.5 REST 路由索引

前缀 `/api/v1`，均需鉴权（配置了 token 时），统一错误体 `{"success": false, "error": {code, message, details}}`。交互式文档：`/docs`（OpenAPI）。

| 类别 | 路由 |
|------|------|
| 健康（免鉴权，不带前缀） | `GET /health` |
| 只读 | `GET /overview`；`GET /collections`、`GET /collections/{name}`；`GET /documents`（`collection` / `keyword` / `limit<=500` / `offset`）、`GET /documents/{source_path}?collection=`；`GET /images`、`GET /images/{image_id}`（图片字节）；`GET /traces`（`trace_type` / `keyword` / `routes` / `limit<=500` / `offset`）、`GET /traces/{trace_id}` |
| 写（后台任务，202 + `task_id`） | `POST /uploads`（multipart 字段 `files`，201）；`POST /ingestions`（`{files, collection, force}`，只接受已上传文件名；同文件同集合重复提交 409）；`POST /evaluations`（`{backend, test_set_path?, top_k?, filters?}`；同时只允许一个，409）；`DELETE /documents/{source_path}?collection=`（同步，Chroma + BM25 + 图片 + 摄取历史） |
| 任务 | `GET /tasks`（`kind` / `status` / `limit<=200` / `offset`）、`GET /tasks/{task_id}`（断线恢复轮询）；**`WS /tasks/{task_id}/events`**：先回放当前快照再推送 `{type:"task_event", task_id, status, detail, timestamp, version}`，终态后服务端关闭 |
| 检索（Query Playground） | `POST /search`（`{mode: normal\|fast\|deep, query, top_k?, collection?}`，被门控拒绝 403 `AGENTIC_DISABLED` / `DEEP_DISABLED`；检索自身失败 200 + `is_error`）；`GET /search/capabilities`（含 `progress_stream`，反映 `api.deep_progress_stream`）；**`WS /search/ws`**（仅 `api.deep_progress_stream: true` 时可用，见 10.6） |
| Chat | `WS /chat`；`GET /chat/capabilities`；`GET /chat/sessions`、`GET /chat/sessions/{session_id}`、`DELETE /chat/sessions/{session_id}` |

任务状态机 `pending → running → succeeded / failed`，状态持久化在 `api.tasks_db`；进程重启时遗留的 `running` 任务标为 `failed`（`interrupted`）。

### 10.6 `agentic_progress_stream` 与 sidecar 线程泄漏警告

sirchmunk sidecar 的**流式端点**每次请求新建 `KnowledgeStorage` 且不关闭，每次稳定泄漏约 10 个线程（9 个 DuckDB 工作线程 + 1 个 parquet 同步线程），空闲后不回收（SX5.3 实测，上游缺陷，按「不改上游」红线不修；见 [sirchmunk-extension-followups_backlog.md](../backlog/sirchmunk-extension-followups_backlog.md) #1）。因此：

- `chat.agentic_progress_stream` **默认 `false`**：Chat 的 agentic 模式走同步 `search()`，不受影响，代价是没有 `search_log` 进度帧。
- 设为 `true` 才改走流式并把 sidecar 日志转 `search_log` 帧；**大量使用后需重启 sidecar**。
- Web 的 Query Playground Deep 默认同样走同步检索（无进度日志）；`api.deep_progress_stream: true` 时改走 **`WS /api/v1/search/ws`**（WEBUI_SIDECAR_SPEC WS2/WS3）：上行 `{mode, query, top_k?, collection?}` 或 `{"type":"cancel"}`，下行 `search_log` 进度帧（与 Chat 同一 0.5s 去重）→ `search_result`（形态同 `POST /search`）后关闭；被门控拒绝（agentic 关闭 / Deep 未放行）发 `error{AGENTIC_DISABLED|DEEP_DISABLED}`，握手阶段拒绝发 `error{UNAVAILABLE|AUTH|BAD_REQUEST}`。**同一泄漏**：Deep 流式命中上表缺陷，默认关闭，上游修复并验证回落后再开启。

### 10.7 测试与 Playwright e2e

```bash
pytest tests -o addopts="" -q -p no:cacheprovider      # 全量离线（含 tests/e2e_web 假后端启动冒烟）
pytest tests/e2e_web -q                                 # 假后端冒烟：UI 托管 + 种子只读路由 + 一轮脚本化 chat；frontend/out 未构建则 skip

# 前端静态检查与单测（无需浏览器）
cd frontend && npm run typecheck && npm run lint && npm test

# 浏览器端到端（opt-in，不属默认套件；首次需装浏览器）
cd frontend && npx playwright install chromium
cd frontend && npm run build && npm run e2e
```

Playwright 流程跑在 `tests/e2e_web/fake_api_server.py` 上：真实 FastAPI 应用 + 真实静态导出，所有后端依赖用 fake，**不需要网络、模型或 sidecar**；它自己用 `.venv/bin/python` 起两个服务（普通 + 带 token 的鉴权流），可用 `PYTHON` / `E2E_PORT` / `E2E_AUTH_PORT` 覆盖。覆盖 Chat 主流程、Overview/Traces、Data Browser、Ingestion Manager、Evaluation、Playground 与鉴权。

### 10.8 门控环境变量（默认跳过，显式开启才触网）

| 变量 | 用途 |
|------|------|
| `RAG_LLM_INTEGRATION=1` | 真实 LLM 相关集成测试（`tests/integration/test_chunk_refiner_llm.py` 等） |
| `RAG_E2E_INTEGRATION=1` | 真实模型端到端 |
| `RAG_AGENTIC_INTEGRATION=1` | 真实 sidecar 的 `agentic_search` MCP 冒烟（需 sidecar 已启动） |
| `RAG_CHAT_INTEGRATION=1` | 真实 LLM 多轮 Chat 冒烟（`tests/e2e/test_chat_real_integration.py`）：流式端到端、指代消解、话题漂移、会话重启恢复；需 LLM/embedding 可用且本地索引非空，会话库与 trace 落 tmp。运行：`RAG_CHAT_INTEGRATION=1 pytest tests/e2e/test_chat_real_integration.py -v`。若 settings 里的 chat LLM 不可用，可用 `RAG_CHAT_LLM_PROVIDER` / `RAG_CHAT_LLM_MODEL` / `RAG_CHAT_LLM_BASE_URL` / `RAG_CHAT_LLM_API_KEY` 覆盖（如 DeepSeek 的 OpenAI 兼容端点） |
| `RAG_AGENTIC_INTEGRATION=1`（Chat） | agentic 路由经真实 sidecar（`tests/e2e/test_chat_agentic_real_integration.py`）：FAST 透传、DEEP 门控拒绝、`allow_deep` 开启时的 DEEP、`search_log` 进度帧；需 sidecar 已启动，且运行 pytest 的 shell 必须导出与 sidecar 相同的 `SIRCHMUNK_API_TOKEN`（缺失时 sidecar 把 401 包成 HTTP 500 INTERNAL_ERROR，易误判为 sidecar 故障）。运行：`RAG_AGENTIC_INTEGRATION=1 pytest tests/e2e/test_chat_agentic_real_integration.py -v` |

### 10.9 Streamlit Dashboard（legacy）

状态：**已退役（2026-11-13）**。Streamlit 页面层（`app.py` + `pages/` 七页面）、`scripts/start_dashboard.py` 与页面级测试（`test_dashboard_smoke.py` / `test_query_traces_source.py` 等）已按 [STREAMLIT_RETIREMENT_SPEC.md](../specs/STREAMLIT_RETIREMENT_SPEC.md)（SR 系列）物理删除；`streamlit>=1.36` 运行时依赖已从 `requirements.txt` / `pyproject.toml` 移除（仅页面使用的 `pandas` 未显式声明、随 streamlit 传递安装，退役后自然消失，无需单独处理）。退役后：Web UI（第 10 章）为主界面；`observability/dashboard/services/` 保留为 `src/api/` 共享服务层；sidecar 托管唯一入口为 `start_api.py --manage-sidecar`。MCP stdio server 与 Streamlit **完全无依赖关系**（`src/mcp_server/` 不 import dashboard）。

**原删除条件核销**（对应 `dashboard-api-migration_backlog_frozen.md` P6 退役门槛）：

1. [WEBUI_PARITY_AUDIT.md](../analysis/WEBUI_PARITY_AUDIT.md) 的缺口已补齐或明确放弃：#5 翻页/#2 趋势图/#3 预检已由 WP 规格交付，Deep 进度日志与 sidecar 托管编排（原「Streamlit 独有逻辑」）已由 [09_WEBUI_SIDECAR_SPEC_frozen.md](../frozen/09_WEBUI_SIDECAR_SPEC_frozen.md)（WS 系列）补齐；其余遗留见 webui-parity-followups 台账（已晋升为 [STREAMLIT_RETIREMENT_SPEC.md](../specs/STREAMLIT_RETIREMENT_SPEC.md)）——已满足；
2. WC10.3 门控冒烟通过（已于 2026-10-15 通过），WEBUI_CHAT_SPEC 已冻结——已满足；
3. Python、API、前端测试全部通过——已满足（SR6 全量离线回归，计数见该规格）；
4. Web UI 已稳定使用一段时间，使用者确认不再依赖 Streamlit（2026-11-13）——已满足。
