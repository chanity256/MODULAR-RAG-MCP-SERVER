# Agentic 检索覆盖 Web UI 上传目录提案（AR 系列，草案）

> 起源：会话内配置边界核查（2026-11-18，非文件来源）：核对「sidecar 只看 data/documents」这一说法时确认的真实边界，证据见下文第 1 节。
> 依据：[03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) S1.3（PathGuard 双层防线）、[06_WEBUI_CHAT_SPEC_frozen.md](../frozen/06_WEBUI_CHAT_SPEC_frozen.md) D4（Chat agentic 模式复用 `AgenticSearchTool`）。
> 状态：提案，未立项。任务前缀 `AR`（Agentic Roots）立项时需再核查，确认与 A–I / G / S / SX / IR / V / WC / LG / WP / WS / SR / CMC / ERD / KE / SD 均无冲突。
> 红线不变：主 venv 永不 `import sirchmunk`；白名单仍由本项目调用方执行，PathGuard fail-closed 语义不放宽。

## 决策三问

### 1. 它解决了哪个已经观察到的问题？

Web UI 上传的文件能进 RAG 索引，但 agentic 检索（MCP `agentic_search`、Chat `agentic_fast` / `agentic_deep`、Playground agentic）搜不到它们。同一份文档在 hybrid 模式下能答，切到 agentic 模式就答不出，用户看不出原因。

证据（基线 `c28b50f`）：

- `config/settings.yaml:104-105`：`agentic_search.allowed_roots` 只有 `./data/documents` 一项。
- `src/observability/dashboard/services/ingestion_service.py:36`：上传落盘目录 `DEFAULT_UPLOAD_DIR = Path("data/uploads")`，`src/api/deps.py:128` 构造 `IngestionService` 时不传 `upload_dir`，即恒为该目录。
- `src/mcp_server/tools/agentic_search.py:225-233`：不传 `paths` 时默认只搜 `allowed_roots`；显式传 `data/uploads/...` 会被 PathGuard 拒绝，返回 `-32602`。
- `src/libs/agentic_search/path_guard.py:102-130`：调用后过滤同样会剔除白名单外的证据。

核查中还发现三个与「把上传目录加入白名单」直接相关的连带问题，方案必须一并处理，否则会引入新缺陷：

1. **根目录不存在即整体失败**：`PathGuard.__init__` 对不存在的根抛 `allowed root does not exist`（`path_guard.py:56-58`，由 `tests/unit/test_agentic_path_guard.py:34` 锁定）。`data/uploads/` 只在第一次上传时由 `save_uploads` 创建，干净环境或从未上传过的机器上直接加这一项，会让整个 agentic 工具不可用，而不只是少搜一个目录。
2. **删除后文件仍在**：`DELETE /documents/{id}`（`src/api/routes/ingestion.py:164-182`）经 `DocumentManager.delete_document` 只清 Chroma / BM25 / 图片 / 摄取历史四个存储，不删上传目录里的源文件。上传目录进白名单后，Web UI 上「已删除」的文档仍会被 agentic 检索命中，并作为证据出现在回答里。
3. **相对路径按进程 CWD 解析**：`./data/documents` 与将要加入的 `./data/uploads` 都按启动进程的工作目录解析。从仓库根以外启动时根不存在，触发第 1 条的整体失败。这个问题现在就存在，加第二个相对根会让它更容易被踩到。

### 2. 是否有更简单、改动更小的方案？

| 方案 | 改动 | 问题 | 结论 |
|------|------|------|------|
| A. `allowed_roots` 追加 `./data/uploads`，并保证该目录在 PathGuard 构造前存在 | 配置一行 + 一处建目录 | 需要处理连带问题 1；连带问题 2 必须同时修 | **推荐** |
| B. 把上传目录改到 `data/documents/uploads/`（落在现有白名单内） | 改 `DEFAULT_UPLOAD_DIR` 一行 | `scripts/ingest.py --path data/documents/` 是文档中的常用摄取命令，会把 Web UI 上传到其他 collection 的文件再摄取进 `default`，造成跨 collection 串数据；已有上传文件的路径（即 `source_path` / doc_id）全部变化 | 否决 |
| C. 新增配置项，按 `api` 上传目录自动并入白名单 | settings 新字段 + 两个配置节耦合 | agentic 配置与 API 配置互相依赖；PathGuard 的构造点有三处（MCP 工具、`HybridSearch` 第三路、Search / Chat 服务复用工具），都要感知新字段 | 否决：比 A 多一层间接，收益只是少写一行配置 |
| D. 改 PathGuard，不存在的根跳过而非报错 | 改守卫语义 | 改变 S1.3 已冻结、已有测试锁定的 fail-closed 行为 | 否决 |

方案 A 中「保证目录存在」的落点留给立项时决策（D1）：

- **D1-a（倾向）**：在进程入口建目录。`scripts/start_api.py` 启动时、`src/mcp_server/server.py` 启动时，对 `agentic_search.enabled` 下的 `allowed_roots` 中位于项目 `data/` 下的根执行 `mkdir(parents=True, exist_ok=True)`。只建项目自己管理的数据目录，不替用户创建任意路径。
- **D1-b**：PathGuard 新增显式的 `optional_roots` 参数（存在才纳入，不存在跳过），`allowed_roots` 语义不变，配置新增 `agentic_search.optional_roots`。不碰既有测试，但多一个配置概念。

连带问题 2 的最小修法：删除文档时，若 `source_path` 落在上传目录内，且该 `source_path` 在其他 collection 中已无记录，就同时删除源文件；仍被其他 collection 引用时保留文件，并在响应中注明。

连带问题 3 不在本提案内根治（见 Out of Scope）；本提案只要求 D1 的建目录逻辑与 PathGuard 用同一个解析基准，并在 USAGE_GUIDE 写明「须从仓库根启动」。

### 3. 如何通过测试、指标或用户流程确认它确实有效？

离线确定性测试（零网络，fake 后端）：

- PathGuard：`allowed_roots` 含 `data/documents` 与 `data/uploads` 两个根时，上传目录内的路径通过调用前校验，上传目录内的证据通过调用后过滤；根外路径仍被拒绝；符号链接逃逸仍被拒绝。
- 入口建目录（D1-a）：在没有 `data/uploads/` 的临时项目根下启动 API app 与 MCP server，agentic 工具可正常构造 PathGuard，不报 `does not exist`。
- API 集成：`POST /uploads` → `POST /ingestions` → Chat `agentic_fast`（fake sidecar 返回上传目录内的证据）→ citations 保留该证据。
- 删除一致性：上传并摄取后 `DELETE /documents/{id}`，源文件被删除；同名文件同时属于另一个 collection 时，源文件保留。
- 既有测试语义不变：`test_agentic_path_guard.py` 中「根不存在即报错」的用例保持通过（方案 A 不改 PathGuard）。

门控真实冒烟（`RAG_AGENTIC_INTEGRATION=1`，需 sidecar 已启动）：经 Web UI 上传一份含唯一标记词的文档，分别用 Hybrid 与 Agentic (fast) 提问，两种模式都能给出带出处的回答；随后在 Web UI 删除该文档，Agentic (fast) 不再命中。

用户流程：上传 → 摄取 → 在 Chat 切换三种模式提问，结果一致可答；删除后三种模式都不再命中。

## 隐私注记

agentic 检索会把命中文件的内容经 sidecar 发给 sidecar 配置的 LLM（第三方服务）。上传目录进白名单后，所有经 Web UI 上传的文件都进入这个外发范围，而当前用户可能默认上传只进本地索引。立项时需确认：

- `config/settings.yaml` 是否默认加入 `./data/uploads`（本仓库当前 `agentic_search.enabled: true`，改动会立即生效）；
- USAGE_GUIDE 第 10 章与上传页面是否需要提示「上传文件可能被 agentic 模式发送给外部 LLM」。

## Out of Scope

- 相对 `allowed_roots` 改为按项目根而非 CWD 解析（连带问题 3 的根治）：影响所有现有根和 sidecar 路径约定，另立提案。
- agentic 检索按 collection 隔离：sidecar 按文件系统路径检索，不认识 collection；上传目录是扁平的，不同 collection 的上传文件在 agentic 模式下互相可见。如需隔离，应先改上传目录布局，另立提案。
- 上传同名文件跨 collection 互相覆盖（`save_uploads` 按文件名落盘）：既有行为，与本提案无关。
- 扩展到任意用户目录（如 Obsidian vault）：属于 [OBSIDIAN_RAG_SPEC_backlog.md](OBSIDIAN_RAG_SPEC_backlog.md) 的范围。
- 修改 sidecar 侧配置（`SIRCHMUNK_*`）：白名单由本项目执行，sidecar 无需改动。

## 建议分期（立项后在规格中细化）

| 编号 | 内容 | 验收标准 | 性质 |
|------|------|----------|------|
| AR0 | 决策 D1（入口建目录 / 可选根）与隐私默认值 | 规格第 1 章记录终裁 | 文档 |
| AR1 | `allowed_roots` 加入 `./data/uploads` + 按 D1 保证目录存在 | 无上传目录的干净环境下 API 与 MCP server 的 agentic 工具可用；PathGuard 双根单测通过；既有 PathGuard 用例不变 | 配置 + 小改 `scripts/` 或 `src/` |
| AR2 | 删除文档时同步删除上传目录中的源文件（无其他 collection 引用时） | 删除一致性离线测试通过；被其他 collection 引用的文件保留 | 小改 `src/` |
| AR3 | 文档同步：USAGE_GUIDE agentic 与 Web UI 章节、README 5.1 `allowed_roots` 示例、隐私提示 | 文档描述与配置一致；`test_docs_links.py` 通过 | 文档 |
| AR4 | 门控真实冒烟：上传 → 三模式可答 → 删除后不再命中 | `RAG_AGENTIC_INTEGRATION=1` 下手动执行一次并记录 | 门控验证 |
