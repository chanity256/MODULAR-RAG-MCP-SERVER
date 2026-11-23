# Plan: [PI3] libs/tree_search/ + tree_search 配置节

Status: implemented

## Scope

主 venv 侧 `libs/tree_search/`（与 `libs/agentic_search/` 同构但独立，3.2 / 3.6 第 3 条）与
`tree_search` 配置节（3.3）。**不含** Chat 接入（PI5）、`pageindex_sync.py`（PI4）、
`start_api.py` 预建（PI5）；不新增 MCP 工具、不动 `agentic_search` 契约。

## Files

新建：

1. `src/libs/tree_search/base_tree_search.py` — 契约层（不 import pageindex / litellm）：
   - 错误分型：`TreeSearchError` 基类 + `TreeSearchTimeoutError` / `TreeSearchBackendError` /
     `TreeSearchResponseError`（PI5 `classify_error` 按此映射）。
   - `TreeSearchRequest(query, source_paths: list[str] | None, max_nodes, max_loops, max_token_budget)`：
     query 非空字符串；`source_paths` None（全量）/ 列表（含空列表——3.4 空列表 fail-closed 是
     sidecar 契约，请求层放行、由 sidecar 返回空证据 + PI5 层不调用兜底）；三个预算字段
     正整数或 None（None = 沿用 config 同名值，对齐 agentic 请求级覆盖先例）。
   - `TreeEvidence(source_path, doc_id, node_id, text, score, section, page_start, page_end, line_start, line_end)`
     （D8 十字段；`section` 为 ` > ` 连接的标题路径；MD 证据 page_* 为 None、PDF 证据 line_* 为 None）。
   - `TreeSearchResponse(evidences, llm_call_count, total_llm_tokens, elapsed_ms, telemetry)`。
   - `BaseTreeSearch` ABC：`search(request) -> TreeSearchResponse`（无 search_stream——
     longdoc 无进度流，2.2 Out of Scope）。
2. `src/libs/tree_search/tree_search_factory.py` — `TreeSearchFactory` /
   `TreeSearchBackendRouteError`，镜像 `AgenticSearchFactory`（`register` 装饰器 + `create(settings)`）。
3. `src/libs/tree_search/http_backend.py` — `PageIndexHttpBackend`（工厂键 `pageindex_http`，
   唯一生产后端）：POST `{base_url}/api/v1/search`，Bearer 头，payload
   `{query, source_paths, max_nodes, max_loops, max_token_budget}`（None → config 值回填）；
   错误映射按 3.4：超时 → `TreeSearchTimeoutError`；连接失败 / 4xx / 5xx / `success=false` →
   `TreeSearchBackendError`；非 JSON / `data` 结构畸形 → `TreeSearchResponseError`。
   transport 可注入（MockTransport）。无流式端点。
4. `src/libs/tree_search/converter.py` — 两段：
   - `parse_search_data(data) -> TreeSearchResponse`：`data` 非 dict / `evidences` 缺失或非
     list → `TreeSearchResponseError`；单条证据缺必需字段（source_path/doc_id/node_id/text）
     降级跳过（对齐 agentic converter 先例）；`llm_call_count` / `total_llm_tokens` 缺失为
     None；`telemetry` 非 dict 非 None → `TreeSearchResponseError`。
   - `tree_evidence_to_retrieval_results(response, *, top_k=None)`（D8 / converter 细则）：
     `chunk_id = "pageindex:{doc_id}:{node_id}"`；score None 回退 0.0；
     metadata = `retrieval_route="longdoc"` / `source_path` / `page`(=page_start) /
     `page_end` / `section` / `line_start` / `line_end` / `node_id`（None 值保留键，对齐
     agentic converter 的 metadata 口径）；`section` 用 ` > ` 连接（由 sidecar 给出）。
5. `src/libs/tree_search/fake_backend.py` — `FakeTreeSearch`（工厂键 `fake`，测试注入）：
   确定性输出由构造参数决定，记录 `last_request`；默认一条全字段 MD 形证据。
6. `src/libs/tree_search/__init__.py` — 导出 + 导入即完成后端注册（agentic `__init__` 先例）。

修改：

7. `src/core/settings.py` — `TreeSearchConfig` dataclass（3.3 默认值：timeout_s=240 /
   index_timeout_s=1800 / max_nodes=5 / max_loops=3 / max_token_budget=32000 /
   denied_substrings `[".env", ".git", "data/db"]`）+ `_build_tree_search()` +
   `Settings.tree_search` 字段 + `load_settings` 接线。校验（3.3 逐条）：
   - 未知 `backend` fail-fast（`_TREE_SEARCH_BACKENDS = ("pageindex_http", "fake")`，
     mode_router delivered-backend 常量先例——settings 不 import libs）；
   - `timeout_s` / `index_timeout_s` 正数；`max_nodes` / `max_loops` / `max_token_budget`
     正整数；`allowed_roots` 非空字符串列表；`denied_substrings` 字符串列表；
   - 相对 `allowed_roots` 经 `resolve_data_path` 锚定项目根（RA2，与 agentic 同法）；
   - enabled=true（跨字段放最后）：`base_url` 经 `urlsplit` 校验 `scheme=="http"` 且
     hostname ∈ {127.0.0.1, localhost, ::1}（拒 `http://127.0.0.1.evil.com` 与
     `https://127.0.0.1`）；`allowed_roots` 非空；`api_token` 必填且非未解析 `${...}`。
   - `validate_settings()` 追加跨节校验：`chat.default_retrieval == "longdoc"` 要求
     `tree_search.enabled=true`（PI3 时点 `longdoc` 尚不进 `CHAT_RETRIEVAL_MODES`，
     load_settings 路径先被 `_build_chat` 拒绝；该跨节校验经手工构造 Settings 直测，
     PI5 接入后端到端生效）。
8. `config/settings.yaml` — 新增 `tree_search` 节（`enabled: false` + 注释，缺省禁用）。
9. `tests/fixtures/pageindex/contract_search_response.json` — 新增（**合成契约样例**：
   按 PI2 server 输出的 TreeEvidence 十字段 shape 构造，覆盖 MD（line_*）与 PDF（page_*）
   两种证据；PI2 门控真实抓取后替换，规格 4 章「PI2 真实响应抓取后替换」口径）。

## Tests

新建（全部离线、零网络、fake 注入）：

- `tests/unit/test_tree_search_base.py` — 契约构造与校验（query 空 / source_paths 非法 /
  预算非正 int / score 类型；TreeEvidence 十字段 to_dict；Response 校验）。
- `tests/unit/test_tree_search_factory.py` — 注册表路由（`pageindex_http` / `fake`）、
  未知 backend 抛 `TreeSearchBackendRouteError`。
- `tests/unit/test_tree_search_backend.py` — `httpx.MockTransport`：Bearer 头、payload
  契约（含 None → config 回填与请求级覆盖）、`source_paths` 三形态透传；超时 /
  连接失败 / 5xx / `success=false` / 非 JSON / data 畸形的错误分型；契约 fixture 经
  MockTransport 解析全链路；导入后断言 `sys.modules` 无 pageindex / litellm。
- `tests/unit/test_tree_search_converter.py` — `parse_search_data` 畸形矩阵 + MD / PDF
  两种证据的 `RetrievalResult` 映射（chunk_id 规则、page/page_end、line_*、score 回退、
  top_k）。
- `tests/unit/test_tree_search_config.py` — 3.3 每条校验（默认值、fail-fast 参数化、
  loopback URL 拒绝样例、api_token 必填/占位符、allowed_roots 锚定、enabled 组合）、
  默认配置下 `load_settings()` 除新增节外与基线一致、跨节校验直测。
- `tests/integration/test_tree_search_http.py` — 线程 HTTP stub（`http.server`，对齐
  `test_agentic_http.py` 先例）走真 httpx 栈全链路：200 信封 → 解析 → converter →
  RetrievalResult；500 → BackendError；连接拒绝 → BackendError；401 → BackendError。

## Order

settings 配置节 → base → factory → converter → http_backend → fake_backend → `__init__`
→ settings.yaml → 测试 → 全量回归 + redlines。

## Risks

- **既有测试语义**：不改任何现有测试文件；`load_settings()` 默认结果除新增 `tree_search`
  节外逐字节不变（spec 4 章 PI3 验收）；`agentic_search` 节与 CHAT_RETRIEVAL_MODES 不动
  （PI5 才动）。
- **红线**：`libs/tree_search` 全部模块不 import pageindex / litellm；`check_redlines.py`
  新规则（PI2 已上线）须保持 0 违规——单测加 `sys.modules` 断言双保险。
- **契约 fixture 时序**：PI2 门控未跑，真实响应未抓取；PI3 以合成样例 fixture 先行锁定
  解析路径，PI2 门控冒烟后替换为真实抓取（替换时只改 fixture 文件，不改测试逻辑）。
- **主 venv 纯净**：converter 只 import `core.types` 与本包 base；httpx 懒加载在函数体内
  （对齐 SirchmunkHttpBackend 先例，模块导入零副作用）。

## Verification

- `pytest tests/unit/test_tree_search_base.py tests/unit/test_tree_search_factory.py tests/unit/test_tree_search_backend.py tests/unit/test_tree_search_converter.py tests/unit/test_tree_search_config.py tests/integration/test_tree_search_http.py -q` 全绿。
- `pytest tests -o addopts="" -q` 全量离线回归不回退（基线 3114 passed / 23 skipped / 1 xfailed；本地已知 4 例环境既有失败除外）。
- `python scripts/check_redlines.py` 0 违规。
