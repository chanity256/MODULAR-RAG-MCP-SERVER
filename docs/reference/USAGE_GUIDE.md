# 项目使用指南（含 Sirchmunk Agentic 检索）

> 本文档是速查手册，覆盖：① 主项目（Indexed RAG）基本用法；② Sirchmunk sidecar 的启动与 `agentic_search` 工具用法。
> 详细设计见 [README.md](../../README.md)、[SIRCHMUNK_INTEGRATION_SPEC.md](../frozen/SIRCHMUNK_INTEGRATION_SPEC.md)。

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

- **主链路（Indexed RAG）**：必须先 `ingest`，支持 PDF（DOCX/PPTX/XLSX/HTML 需装可选依赖），低延迟、确定性高。
- **Agentic（Sirchmunk）**：不建索引、指定路径直接搜，支持 MD/代码/JSON/YAML/CSV/HTML/PDF/DOCX/XLSX/PPTX/EPUB，每次查询消耗 2~N 次 LLM 调用。
- **进程边界**：主项目 venv 永远不 import sirchmunk，只通过 HTTP 调用。

---

## 2. 主项目基本用法

前置：`source .venv/bin/activate`，在仓库根目录执行。

### 2.1 目录结构速览

树形总览（格式对齐 [DEV_SPEC 5.2](../frozen/DEV_SPEC.md)）：

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
│   ├── core/                 # 业务核心：settings / query_engine / types 契约
│   ├── ingestion/            # 摄取管线：Loader → Split → Transform → Embed → Upsert + DocumentManager
│   ├── libs/                 # 可插拔抽象层：各组件 Base* + 工厂（loader / embedding / vector_store / agentic_search 等）
│   └── observability/        # TraceContext（JSON Lines 落盘）+ Streamlit Dashboard（七页面）
│
├── scripts/                  # CLI 脚本
│   ├── ingest.py             # 摄取（含 --sync / --prune / --dry-run / --retry-failed，见 2.2）
│   ├── query.py              # 查询（--verbose / --agentic，见 2.3）
│   ├── evaluate.py           # RAG 评估（golden set + composite 指标）
│   ├── start_dashboard.py    # Dashboard 启动器（自动托管 sidecar，见第 5 节）
│   └── sirchmunk/            # sidecar 环境与旁路对比工具（setup_venv / start_sidecar / preflight / compare）
│
├── tests/                    # 离线测试套件（unit / integration / e2e；fixtures/ 为测试夹具与金标准集）
│
├── data/                     # 本地数据（不入库 git）
│   ├── documents/            # 待摄取语料目录
│   ├── db/                   # 存储：chroma/（向量）、bm25/（稀疏索引）、image_index.db、ingestion_history.db、sirchmunk/
│   └── images/               # 摄取抽取的图片文件（按 doc_hash 分目录）
│
├── logs/                     # 运行日志：traces.jsonl（全链路 trace 落盘）+ sidecar 日志与对比报告
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

- **格式**：`.pdf` 开箱即用；`.docx` / `.pptx` / `.xlsx` / `.html` 需 `pip install '.[ingest-formats]'`（markitdown，可选依赖）；`.md` 不支持。
- **增量判定**：按 `(source_path, collection)` + 文件哈希；内容变更后重摄取会先清理旧版本残留。
- **组合约束**：`--dry-run` 必须配合 `--sync` / `--prune`；`--force` 不能与 `--sync` / `--prune` 同用。
- **`--dry-run` 计划分类**：new / modified / unchanged / deleted / stale（stale = 历史记录的 embedding 版本与当前配置不同，按 `ingestion.version_policy.on_embedding_mismatch` 告警并跳过，不自动重建）。
- **退出码**：`0` 全部成功或跳过；`1` 有文件失败；`2` 用法或配置错误。
- **可选配置**：`ingestion.retry`（缺省不重试）、`ingestion.version_policy`（缺省 `auto`），样例见 `config/settings.yaml` 注释。

产物：Chroma 向量库（`data/db/chroma/`）、BM25 索引（`data/db/bm25/`）、图片索引与摄取历史（`data/db/`）。摄取历史表为 `ingestion_history_v2`，首次打开旧库时自动迁移，旧表改名 `ingestion_history_legacy_backup` 保留（需回滚时手工改回表名）。设计见 [INGESTION_ROBUSTNESS_SPEC.md](../frozen/INGESTION_ROBUSTNESS_SPEC.md)。

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

### 2.6 Dashboard 与评估

```bash
python scripts/start_dashboard.py          # http://localhost:8501，七页面管理平台

python scripts/evaluate.py                 # 默认 golden set + composite 后端
python scripts/evaluate.py --evaluator custom --top-k 5   # 纯离线指标
python scripts/evaluate.py --retrieval-mode hybrid+agentic --agentic-trigger always   # agentic A/B（需 sidecar 启用）
```

### 2.7 Dashboard「在线检索」页（S4）

Dashboard 导航「检索」分组 → 「在线检索」：三模式在线查询，不写代码、不敲 CLI 也能检索。

- **模式选择**：Normal / Fast / Deep 三个按钮，默认选中 Normal（primary 高亮），选好后再点「检索」。
- **配置门控**（严格遵守 settings）：
  - Normal：始终可用；
  - Fast：需 `agentic_search.enabled: true`，否则按钮置灰，悬停提示开启方法并启动 sidecar（`scripts/sirchmunk/`）；
  - Deep：另需 `agentic_search.allow_deep: true`，否则置灰并提示开启 `allow_deep`。
  - 默认配置（`enabled: false`）下仅 Normal 可用。
- **表单**：查询文本 + Top-K；collection 输入仅在 Normal 模式显示。Deep 模式检索时会提示「可能需要数十秒」。
- **结果**：Markdown 答案 + 引用表（来源/页码/分数）；Fast/Deep 额外展示 Agentic 答案、模式、Token、LLM 调用次数、耗时与证据文件。
- **trace 联动**：每次检索自动写入 trace，到「Query 追踪」页可查看各阶段耗时（agentic 阶段由 S3.2 渲染）。
- **失败友好**：sidecar 不可用或参数非法时以页面错误提示展示，不崩溃。

---

## 3. Sirchmunk 是什么、什么时候用

Sirchmunk 是 modelscope 开源的「**无索引 Agentic 检索**」引擎：用 `rg`/`rga` 做词法召回，用 LLM 做查询分析、多路证据融合与 ReAct 式迭代探索，最后输出带出处的答案。

**适合的场景**（与主链路互补）：

| 场景 | 原因 |
|------|------|
| 新文件/未摄取的文件立即检索 | 无需 ingest，指定路径即可搜 |
| 主链路未覆盖的格式（MD/代码/配置等） | 主链路仅支持 PDF 与 DOCX/PPTX/XLSX/HTML，sirchmunk 覆盖更多格式且无需摄取 |
| 精确标识符（错误码/函数名/配置键） | grep 精确匹配天然强项 |
| 跨文件多跳问题 | ReAct 迭代探索弥补单轮 Top-K 上限 |

**代价**：FAST 模式约 2–5s，DEEP 模式数十秒；每次查询 2~N 次 LLM 调用（有 Token 成本）；结果非确定性。

**当前状态**：主项目侧适配（S1）已完成并合入；sidecar 已在 2026-10-05 实际跑通并完成旁路对比，**闸门 G0 未通过**，`agentic_search` 保持 `enabled: false` 默认关闭。G0 终裁后经显式指令重启：S2（HybridSearch 可选第三路 + `--agentic` CLI）与 S3（source 级 A/B 评估框架 + Dashboard agentic 展示）均已交付（2026-10-06），但**默认关闭口径不变**，真实 sidecar A/B 实测数据未执行（受 `RAG_AGENTIC_INTEGRATION` 门控与 G0 结论约束）。结论与数据见 `SIRCHMUNK_INTEGRATION_SPEC.md` 第 7 章与 S3 阶段进度备注。

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
| `SIRCHMUNK_ENABLE_CLUSTER_REUSE` | 保持 `false`，可跳过 torch + embedding 模型下载 |
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

**推荐：一条命令全搞定**（Dashboard 启动器自动托管 sidecar）：

```bash
python scripts/start_dashboard.py
# agentic_search.enabled=true 时：自动拉起 sidecar → 等健康 → 启动 Dashboard；
# sidecar 已在运行则直接复用；Dashboard 退出（Ctrl+C / kill）时自动停掉由它拉起的 sidecar。
# --no-sidecar：完全不托管 sidecar（只跑 Dashboard）。
```

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

用 `tests/fixtures/agentic/golden_questions.json` 逐条直调 sidecar，输出命中文件、耗时与 Token 用量表。

### 7.2 测试套件

```bash
pytest -q                                        # 全量离线（零网络，默认全绿）
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
| Token 成本异常 | 确认 `LENS_HARD_TOKEN_BUDGET=true`、`max_token_budget` / `max_loops` 未被调大；客户端断连不会取消 sidecar 执行 |
| 知识簇缓存损坏 | 多进程共享了 `SIRCHMUNK_WORK_PATH`；该目录必须每进程独占 |

---

## 9. 安全边界（必读）

- **sirchmunk 自身的 `paths` 参数不是安全边界**：其 ReAct 的 `file_read` 可读 LLM 请求的任意绝对路径，上游 `/api/v1/search` 不做白名单校验。
- **本项目用 PathGuard 做两层防护**：
  1. 调用前：校验 `paths`，realpath 归一、拒绝符号链接逃逸、白名单外直接 `-32602`，fail-closed 不发请求；
  2. 调用后：过滤证据中落在白名单外的条目（防注入诱导泄露）。
- **信任模型**：语料内容不可信（prompt injection 可操纵 Agent 读文件），agentic 答案按不可信输入处理。
- **部署要求**：sidecar 只监听 `127.0.0.1` 并始终开启 Bearer；重度使用 DEEP 模式建议在受限用户/容器内运行。
