# 项目使用指南（含 Sirchmunk Agentic 检索）

> 本文档是速查手册，覆盖：① 主项目（Indexed RAG）基本用法；② Sirchmunk sidecar 的启动与 `agentic_search` 工具用法。
> 详细设计见 [README.md](README.md)、[SIRCHMUNK_INTEGRATION_SPEC.md](SIRCHMUNK_INTEGRATION_SPEC.md)。

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

- **主链路（Indexed RAG）**：必须先 `ingest`，仅支持 PDF，低延迟、确定性高。
- **Agentic（Sirchmunk）**：不建索引、指定路径直接搜，支持 MD/代码/JSON/YAML/CSV/HTML/PDF/DOCX/XLSX/PPTX/EPUB，每次查询消耗 2~N 次 LLM 调用。
- **进程边界**：主项目 venv 永远不 import sirchmunk，只通过 HTTP 调用。

---

## 2. 主项目基本用法

前置：`source .venv/bin/activate`，在仓库根目录执行。

### 2.1 摄取（Ingest）

把 PDF 放入 `data/documents/`，然后：

```bash
python scripts/ingest.py --path data/documents/ --collection default
python scripts/ingest.py --path data/documents/ --collection default --force   # 内容变更后强制重摄
python scripts/ingest.py --path data/documents/ -v                             # 查看逐阶段进度
```

产物：Chroma 向量库（`data/db/chroma/`）、BM25 索引（`data/db/bm25/`）、图片索引与 SHA256 增量记录（`data/db/`）。

### 2.2 查询

```bash
python scripts/query.py --query "如何配置 Azure？" --verbose
python scripts/query.py --query "bm25 scoring" --collection default --top-k 5
python scripts/query.py --query "部署密钥" --no-rerank   # 跳过精排
```

### 2.3 启动 MCP Server（stdio）

```bash
PYTHONPATH=src python -m mcp_server.server
```

默认暴露 3 个工具：`query_knowledge_hub`、`list_collections`、`get_document_summary`。开启 `agentic_search.enabled` 后多出第 4 个工具 `agentic_search`。

### 2.4 MCP Client 配置

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

### 2.5 Dashboard 与评估

```bash
python scripts/start_dashboard.py          # http://localhost:8501，六页面管理平台

python scripts/evaluate.py                 # 默认 golden set + composite 后端
python scripts/evaluate.py --evaluator custom --top-k 5   # 纯离线指标
```

---

## 3. Sirchmunk 是什么、什么时候用

Sirchmunk 是 modelscope 开源的「**无索引 Agentic 检索**」引擎：用 `rg`/`rga` 做词法召回，用 LLM 做查询分析、多路证据融合与 ReAct 式迭代探索，最后输出带出处的答案。

**适合的场景**（与主链路互补）：

| 场景 | 原因 |
|------|------|
| 新文件/未摄取的文件立即检索 | 无需 ingest，指定路径即可搜 |
| 非 PDF 格式（MD/Word/Excel/代码/配置） | 主链路仅支持 PDF，sirchmunk 覆盖多格式 |
| 精确标识符（错误码/函数名/配置键） | grep 精确匹配天然强项 |
| 跨文件多跳问题 | ReAct 迭代探索弥补单轮 Top-K 上限 |

**代价**：FAST 模式约 2–5s，DEEP 模式数十秒；每次查询 2~N 次 LLM 调用（有 Token 成本）；结果非确定性。

**当前状态**：主项目侧适配（S1）已完成并合入；sidecar 已在 2026-10-05 实际跑通并完成旁路对比，**闸门 G0 未通过**（FAST 干净口径命中 1/6、延迟 77–256s/条），故 `agentic_search` 保持 `enabled: false` 默认关闭，S2/S3 不启动。结论与数据见 `SIRCHMUNK_INTEGRATION_SPEC.md` 第 7 章 S0 执行记录。

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
| `LLM_API_KEY` | OpenAI 兼容端点的 Key（sirchmunk **不支持**原生 Azure，用 ZAI/DeepSeek/OpenAI 等兼容端点） |
| `LLM_BASE_URL` / `LLM_MODEL_NAME` | 模板默认 ZAI `glm-5.3-flash`，可按需改 |
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

每次使用 agentic_search 前（顺序执行）：

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
