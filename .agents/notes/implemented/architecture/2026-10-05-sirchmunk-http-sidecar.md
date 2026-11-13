# Agent Note: Sirchmunk 以 HTTP sidecar 进程外接入，主 venv 永不 import

Status: implemented

## Problem

项目要补一条「无索引 Agentic 检索」路线（rg/rga 词法召回 + LLM 迭代探索），选定的上游是 sirchmunk（`0.2.1+main`，无 changelog）。对 commit `b314e11` 的源码审计发现它和本项目的两条硬约束冲突：

- **stdio MCP 通道只允许 JSON-RPC 行**。sirchmunk 的 DEEP 路径有无条件 `print(..., flush=True)`（`learnings/tree_indexer.py`、`learnings/compiler.py`），进程内调用会直接污染 stdout，客户端解析失败。
- **测试套件离线、确定性**。`AgenticSearch.__init__` 在缺 `rga` 时联网从 GitHub 下载二进制；`reuse_knowledge=True`（默认）构造即 `import torch` 并后台下载 embedding 模型。

另外，`AsyncOpenAI` 客户端和模块级 `asyncio.Semaphore` 绑定首个事件循环，在同步主循环里反复 `asyncio.run` 会报 "Event loop is closed"。不解决这些，接入要么破坏 MCP 通道，要么破坏离线测试。

## Decision

sirchmunk 装在独立 venv（`.venv-sirchmunk`，Python 3.11/3.12，锁定 `b314e11`），以 `sirchmunk serve` 常驻，只监听 `127.0.0.1:8584`，开启 Bearer 鉴权。本项目通过同步 httpx 调用 `POST /api/v1/search`（`response_format=context`）。

- 所有上游耦合收敛在 `src/libs/agentic_search/http_backend.py`（`SirchmunkHttpBackend`，工厂键 `sirchmunk_http`）。主 venv 不 import sirchmunk，`pyproject.toml` 主依赖零新增；httpx 经 chromadb 间接安装，并在 dev 组显式登记。
- 错误映射：`httpx.TimeoutException` → `AgenticSearchTimeoutError`；连接失败、HTTP 4xx/5xx、`success=false` → `AgenticSearchBackendError`；非 JSON 或结构畸形 → `AgenticSearchResponseError`。工具层把这几类转成 `isError: true`，不报 -32603。
- 响应解析是防御式的（`converter.parse_search_context`）：`snippets` 的 dict 与纯字符串两种形态都接受，畸形条目跳过不抛错，缺失的 telemetry 字段容忍。
- 超时由本项目控制（`agentic_search.timeout_s`，默认 30s），不依赖上游 OpenAI 客户端的 600s 默认值。
- sidecar 的 LLM 配置（`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME`）独立于 `settings.llm`：上游只支持 OpenAI 兼容端点，没有原生 Azure。
- 运维脚本在 `scripts/sirchmunk/`：`preflight.sh` 检查 rg、rga 和 `/api/v1/search/status`；`setup_venv.sh` 建 venv 并 checkout 锁定 commit；`start_sidecar.sh` 加载 `.env-sirchmunk` 启动服务。

## 对接契约

- 请求：`{"query", "paths": [绝对路径], "mode": "FAST"|"DEEP", "response_format": "context", "max_loops", "max_token_budget", "top_k_files"}`。本项目始终发送绝对路径，因为 `paths` 缺省时上游会退到 sidecar 进程的 CWD。
- 响应：`{"success": true, "data": {"type": "context", ...}}`，消费 `data.answer`、`data.cluster.evidences[]`、`data.total_llm_tokens`、`data.telemetry`。
- 契约形状由 `tests/fixtures/agentic/contract_response.json` 锁定；升级上游 commit 时必须重抓真实响应并替换。

## Alternatives considered

- **进程内 import（适配器内 `asyncio.run` 桥接）**：最强论据是零运维（不用起第二个进程）、延迟最低、能直接拿到 Python 对象。被否：无条件 `print` 会写进本进程 stdout；构造期的联网下载和 torch import 破坏离线测试；事件循环绑定让第二次 `asyncio.run` 不可靠。逐个 monkeypatch 这些副作用，等于对一个 1.3 万行、无 changelog 的 `search.py` 做长期补丁维护。
- **让用户在 MCP 客户端里直接挂官方 `sirchmunk_mcp`（零代码旁路）**：最强论据是本仓库一行不改，并且这种旁路本来就保留为 S0 对比手段。被否作为正式接入：官方 server 只在 import/构造期把 stdout 换成 devnull，搜索期间仍然泄漏，自己的 stdio 通道一样会坏；`paths` 没有白名单（见 [agentic_search 白名单](../feature/2026-10-05-agentic-search-path-guard.md)）；结果也进不了本项目的 trace、citations 和后续的 HybridSearch 融合。
- **每次查询起一个 `sirchmunk` CLI 子进程**：最强论据是不需要常驻服务，进程崩溃天然隔离。被否：每次冷启动都要付 import 和构造开销（torch、DuckDB、知识簇加载），秒级查询变成十秒级；多个子进程共享 `work_path` 没有文件锁，会互相覆盖 Parquet（last-writer-wins）。

## Consequences

- **收益**：stdout、事件循环和依赖三者都与主进程隔离；sidecar 崩溃或未启动时，只有 `agentic_search` 返回友好的 `isError`，主 server 和其他三个工具不受影响；离线测试用 `httpx.MockTransport` 和线程 HTTP stub 即可覆盖全链路。
- **代价**：要多维护一个进程和一个 venv（启动顺序、健康检查、token 同步）；每次调用多一跳本机 HTTP。
- **代价**：HTTP 断连不会取消服务端执行，客户端超时后 LLM 费用仍在消耗，只能靠 `max_token_budget`、`max_loops` 和 `LENS_HARD_TOKEN_BUDGET=true` 兜底。
- **已知上限**：sidecar 内部用信号量串行搜索，不适合并发查询。以下任一情况发生时必须重访本决定：上游提供无副作用的库 API；上游修掉 stdout 泄漏并提供可取消的请求；需要并发吞吐。

## Verification

- `tests/unit/test_agentic_backend.py`（11 个）用 `httpx.MockTransport` 覆盖 Bearer、超时、连接拒绝、5xx、非 JSON 和契约 fixture 解析。
- `tests/integration/test_agentic_http.py`（3 个）起线程 HTTP server 模拟 sidecar，走真实 httpx 链路。
- 真实 sidecar 冒烟由 `RAG_AGENTIC_INTEGRATION=1` 门控，默认跳过；`scripts/sirchmunk/compare.py` 的旁路对比（闸门 G0/G1）尚未在真实 sidecar 上运行。
