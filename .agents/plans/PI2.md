# Plan: [PI2] sidecar 服务 + pi_adapter + start_sidecar.sh + 红线规则

Status: implemented

## Files

新建：

1. `scripts/pageindex/pi_adapter.py` — 运行期唯一 import `pageindex` / `litellm` 的文件（红线精确白名单成员，3.2 / 3.6 第 5 条；`poc_compare.py` 为预研存档例外）。全部上游 import 懒加载在函数体内（模块导入零副作用，且不供主 venv 测试导入——§4 明确 `pi_adapter.py` 不进主 venv 测试）。职责：
   - env 装配：`PAGEINDEX_STORAGE`（默认 `data/db/pageindex`）、`LLM_MODEL` / `LLM_API_BASE` / `LLM_API_KEY`（D4）、`PAGEINDEX_RENDER_TIMEOUT_S`、`PAGEINDEX_SUMMARY_CONCURRENCY`（默认 3）。
   - `PageIndexAdapter.status()` → `{version, pageindex_version, model, documents}`；`pageindex_version` 经 `importlib.metadata.version("pageindex")`（启动日志同值，对齐 KE0 实际版本落日志先例）。
   - `PageIndexAdapter.documents()` → `tree_store` 记录列表（`derived_pdf` 仅 MD）。
   - `PageIndexAdapter.index(source_path, file_hash)` → `{doc_id, format, node_count, llm_tokens, render_ms?, derived_pages?}`：
     - 幂等：`tree_store` 中该 `source_path` 的 `file_hash` 未变即直接返回已有记录（不再转换 / 重提交）。
     - PDF：`PageIndexClient(index_model, chat_model, storage_path=PAGEINDEX_STORAGE, summary_concurrency=…)`（3.2：显式传 storage_path 与低并发，防上游默认 128 触发 429）→ `submit_document` → `get_document_structure` → `usage["total_tokens"]` 采集。
     - MD（D3 / D11 / D12）：`md_render.render` 转 `<storage>/derived/<md_sha256>.pdf`（调用方命名产物路径，PI1 契约）→ 同 PDF 路径 `submit_document` 建树 → `tree_store` 记录含 `headings` 缓存与 `derived_pdf`；`RenderError` 三类原样上抛（server 转 `render:` 前缀错误）。
     - 建树限流补丁：POC `patch_pageindex_ratelimit` 幂等化移植（全局信号量 + 重试耗尽指数退避 + 单节点 400 内容审核降级 600 字符摘录，3.2 要求保留）。
   - `PageIndexAdapter.delete(source_path)` → 删 `tree_store` 记录，MD 连带删派生 PDF（D5）。
   - `PageIndexAdapter.search(query, source_paths, max_nodes, max_loops, max_token_budget)` → 组装 `tree_loop.LoopDoc`（树经 `get_document_structure(doc_id)` 现取）→ 注入 `complete`（litellm.completion，连接类错误 3 次指数退避，POC 同款）与 `read_node`：
     - MD（D12④）：`HeadingIndex.locate(title, parent_path)` 回定位 → 读原 MD 行区间；**读前实算原 MD SHA256 与记录 `file_hash` 核对**（PI1.1 语义：漂移 / 越界即安全回退 `get_page_content` 派生 PDF 页文本，`how="page_fallback"`）；回定位失败 `how="unlocated"`（只给 section）。
     - PDF：`get_page_content(doc_id, page)` 按 `start_index`–`end_index`（物理页 1 起计）逐页拼接（≤6 页 / 节点，POC 口径），`page_start` / `page_end` 给页码。
2. `scripts/pageindex/server.py` — FastAPI 编排 + 鉴权（3.4 契约）；不 import pageindex / litellm / pi_adapter 顶层（`create_app(adapter, token)` 注入；`main()` 内延迟 `from pi_adapter import PageIndexAdapter`）。统一信封 `{"success": bool, "data": ..., "error": str | null}`；Bearer 鉴权 `hmac.compare_digest`；日志走 stderr。
   - `GET /api/v1/status`、`GET /api/v1/documents`、`POST /api/v1/index`、`POST /api/v1/index/delete`、`POST /api/v1/search`。
   - `/index` 请求体校验（绝对路径 + file_hash 必填）；`RenderError` → `success=false, error="render: ..."`；其他异常 → `success=false, error=类型: 摘要`（500 不裸抛堆栈）。
   - `/search`：请求体 `query` 必填、`source_paths` ∈ {null, list}；**空列表 fail-closed** 直接空证据（不调 adapter，3.4 纵深防御）；非空列表时 adapter 返回的证据按列表二次过滤、`telemetry.unindexed` 计未索引路径（不信任 sidecar 内部过滤的对称口径）；证据输出裁剪为 TreeEvidence 十字段（丢弃 loop 内部 `truncated`，telemetry 已有 `truncated_evidence`）。
   - `ensure_loopback(host)`：非 loopback 拒绝启动（3.4「绑定 127.0.0.1」）。
   - `main()`：env 读 `PAGEINDEX_HOST` / `PAGEINDEX_PORT` / `PAGEINDEX_API_TOKEN`（缺失 fail-fast）/ `PAGEINDEX_STORAGE`；uvicorn.run。
3. `scripts/pageindex/start_sidecar.sh` — venv 存在性检查、`set -a` 导出 env 文件、exec sidecar venv python 跑 `server.py`（host 默认 127.0.0.1、port 默认 8585）；启动日志打印 pageindex 实际版本（对齐 sirchmunk start_sidecar.sh 与 KE0 先例）。
4. `scripts/pageindex/setup_venv.sh`（改）— 追加 pandoc 与系统 Chrome/Chromium 探测（报告缺失但不失败——playwright 是可选回退，D11；缺失只影响 MD 建树，原生 PDF 不受影响，R9）。
5. `scripts/check_redlines.py`（改）— 新规则 `no-pageindex-import`：与 `no-sirchmunk-import` 同法（AST 扫 `src` / `scripts` / `tests` / `main.py`，含 `importlib.import_module` / `__import__` 字面量），命中 `pageindex` / `litellm` 及子模块即违规；白名单 `PAGEINDEX_IMPORT_EXEMPTIONS` 按文件路径精确匹配只含 `scripts/pageindex/pi_adapter.py` 与 `scripts/pageindex/poc_compare.py`（不得放宽到目录，KE1 先例）；`SKIP_DIRS` 追加 `.venv-pageindex`。
6. `tests/unit/test_pageindex_sidecar_server.py` — FastAPI TestClient + fake adapter（§4 PI2 行）：无 token / 错 token 401；鉴权通过各端点 schema；`/index` MD 渲染失败 `render:` 前缀；`/index/delete` 返回 `{removed}`；`/search` 空列表不调 adapter、unindexed 计数、越界证据被过滤、TreeEvidence 字段裁剪；`ensure_loopback` 非 loopback 拒绝；导入本模块断言 `sys.modules` 无 pageindex / litellm。
7. `tests/unit/test_check_redlines.py`（改）— 新增 `TestPageindexImport`：正样例（src / scripts / tests / main.py 四处 import 形态 + 动态 import）与负样例（docstring / 字符串 / `pageindex_like` 模块名）；白名单精确匹配与同目录不放宽；白名单条目带规格引用断言。

## Order

pi_adapter → server → start_sidecar.sh + setup_venv.sh 探测 → check_redlines 规则 + 测试 → server 测试 → 全量回归。

## Risks

- **红线时序**：pi_adapter.py 落地后若先跑旧 `check_redlines.py` 会 0 违规（规则尚不存在）——规则与单测同批交付后复跑 `python scripts/check_redlines.py` 必须 0 违规（白名单生效）。
- **主 venv 纯净**：server.py 顶层只 import fastapi / 标准库 / 同目录纯模块；测试断言 `sys.modules`；pi_adapter 不被任何主 venv 测试导入（§4 明文）。
- **契约 fixture**：`tests/fixtures/pageindex/contract_search_response.json`（PI3 用）的真实响应抓取依赖真实 sidecar + 真实 token，属门控验证——本任务不落该 fixture，PI2 行备注留待门控冒烟时抓取。
- **门控验收项**：PI2 验收标准中「对 1 篇 MD + 1 篇 PDF 建树并检索返回带行号 / 页码证据」需真实 sidecar 与真实 LLM token（门控），离线交付只到代码 + 离线测试全绿；真实验证按 auto-coder 门控语义暂停请示。
- **上游 DocStore 残留**：`/index/delete` 只删映射与派生 PDF；上游 DocStore 无删除接口，doc_id 残留不违反契约（`/documents` 只报 tree_store），已知限制记入 plan 不另行处理。
- **既有测试语义**：不改任何现有测试文件断言（test_check_redlines.py 只新增类）；`check_redlines.py` 既有三条规则判定不变。

## Verification

- `pytest tests/unit/test_pageindex_sidecar_server.py tests/unit/test_check_redlines.py -q` 全绿。
- `pytest tests -o addopts="" -q` 全量离线回归不回退（基线 3012 passed / 23 skipped / 1 xfailed；本地已知 2 例 `test_loader_pdf_contract` 环境差异除外）。
- `python scripts/check_redlines.py` 0 违规（新规则上线且白名单生效）。
- `bash -n scripts/pageindex/start_sidecar.sh` 语法检查。

## Deviations

1. **`telemetry.unindexed` 由 adapter 计入**：§3.4「未索引的路径忽略并计入 `telemetry.unindexed`」落在 `pi_adapter.search`（它持有 tree_store，知道索引集），server 不为此二次查询 `/documents`；server 另做证据归属过滤（不信任 adapter 内部过滤），剔除数记 `telemetry.out_of_scope_dropped`（仅非零时出现）。
2. **证据输出裁剪**：`/search` 响应证据只保留 TreeEvidence 十字段（§3.4），丢弃 loop 内部 `truncated`（telemetry 已有 `truncated_evidence`），避免契约面漂移。
3. **业务失败走信封而非 5xx**：`/index` render 失败与 `/search` adapter 异常均返回 HTTP 200 + `success=false`（`error` 带 `render:` 前缀或异常类型名），unexpected 异常不裸抛堆栈；主 venv 侧 PI3 按 `success=false → TreeSearchBackendError` 映射。
4. **`env.example` 增补 `PAGEINDEX_SUMMARY_CONCURRENCY`（默认 3）**：§3.2 要求显式低 `summary_concurrency`，3.3 env 清单未列出口；补为配置出口（默认 3 = PI0 实测值）。
5. **MD 证据读取以「实算 SHA256 核对 + 全量行解析」为准**：D12④ 的「哈希或行边界/首行一致」二选一取哈希口径（更强）；`tree_store` 仍按规格存 `headings` 缓存（index 时落盘），运行期读取用漂移核对通过后的新鲜解析，不消费缓存。
6. **建树 `optimize=merge` 显式传入两条 submit 路径**（MD 派生 PDF 与原生 PDF 统一），落实 D11 终裁「merge 为默认档」。
7. 全量回归 4 例失败均为本地环境既有（`test_loader_pdf_contract` ×2 = PI1 行已记；`test_chat_auto_mode` ×2 经 stash 对照在 HEAD 复现，网络环境相关），非本次引入。
