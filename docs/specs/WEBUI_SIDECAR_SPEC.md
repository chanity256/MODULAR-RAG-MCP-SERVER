# WEBUI_SIDECAR_SPEC — sidecar 托管编排 + Playground Deep 进度日志（WS 系列）

> **定位**：收口 [backlog/webui-parity-followups_backlog.md](../backlog/webui-parity-followups_backlog.md) 剩余的两项实现型差异（台账 #4 sidecar 托管编排、#1 Deep 实时进度日志），二者均为「物理删除 Streamlit 前」的退役门槛项。台账 #3（展示细节差异）为接受决议，不在本规格范围内。基线：`feat/sirchmunk-agentic-search` @ 051f903（WP0–WP2 已随 [frozen/08_WEBUI_PARITY_SPEC_frozen.md](../frozen/08_WEBUI_PARITY_SPEC_frozen.md) 收口）。

**版本**：v0.1（2026-10-18）
**起源**：由 [backlog/webui-parity-followups_backlog.md](../backlog/webui-parity-followups_backlog.md) 第二次拆分移交（第一次移交 #5/#2/#3 预检 → WP 系列），原文细节见 git 历史；上游依赖背景见 [backlog/sirchmunk-extension-followups_backlog.md](../backlog/sirchmunk-extension-followups_backlog.md) #1（SX5.3）。
**决策依据**：台账「决策三问」+ 会话内拍板（2026-10-18）——① #4 选**抽共享模块实现 `--manage-sidecar`**（非文档声明接受）；② #1 **现在立项**，设计为默认关闭/门控，随上游修复（SX5.3 线程泄漏 PR）后再开启；③ 两项合并一份规格（同一台账起源、同一退役门槛关联）。

---

## 1. 核心目标与范围

### In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| `scripts/` + `scripts/start_api.py` | WS0 sidecar 托管编排 | P1 | 把 `start_dashboard.py` 的托管编排抽成共享模块，`start_api.py` 新增 `--manage-sidecar` |
| `src/core/chat/` | WS1 共享日志转帧 | P2 | 从 ChatEngine 抽出「sidecar 日志 → `search_log` 帧」可复用 helper |
| `src/api/routes/search.py` | WS2 搜索流式端点（门控） | P2 | `WS /api/v1/search/ws` 流式 Deep 检索，`api.deep_progress_stream` 默认关闭 |
| `frontend/` | WS3 Playground Deep 接入 | P2 | 门控开启时 Deep 实时渲染进度日志，关闭时维持现状 |
| `docs/` | WS4 文档同步 | P3 | USAGE_GUIDE 第 10 章补 WS 协议与 `--manage-sidecar`；README 更新启动说明 |

### Out of Scope（明确不做）

- **SX5.3 上游线程泄漏修复**：按「不改上游」红线不在本项目修；向上游提 PR 是唯一根治路径（见 sirchmunk-extension-followups 台账 #1）。本规格只做**默认关闭 + 门控注记**，不因开启流式而放大泄漏面。
- **台账 #3 展示细节差异**：接受决议，不排期。
- **物理删除 Streamlit**：本规格只补齐退役门槛中的两项差异；删除动作与 WC10.3 门控冒烟另行评估。
- **MCP tool 流式**：`agentic_search` tool 保持同步端点（SX5.3 实证不受泄漏影响），不改。
- **Chat 行为变更**：WS1 抽取属纯重构，Chat 的帧协议、去重窗口、取消语义不变。

## 2. 技术选型与模块设计

```
scripts/sidecar_manager.py（新，WS0）        src/core/chat/progress.py（新，WS1）
  sidecar_healthy / ensure_sidecar /          sidecar_log_frames(on_log 回调 →
  stop_sidecar（自 start_dashboard.py 迁出）    search_log 帧，0.5s 去重窗口）
        ▲                ▲                          ▲                  ▲
        │                │                          │                  │
scripts/start_api.py  scripts/start_dashboard.py   ChatEngine      api/routes/search.py
  --manage-sidecar     （改为复用，行为不变）      _call_retriever   WS /api/v1/search/ws
```

### 2.1 WS0：sidecar 托管编排（台账 #4）

现状只有 `start_dashboard.py` 有完整编排：探活（`sidecar_healthy`，任何 HTTP 响应含 401 都算存活）→ 未运行则 `bash scripts/sirchmunk/start_sidecar.sh` 拉起 → 等健康（超时/脚本退出不致命，降级为仅启动主服务）→ 退出时回收（先 TERM 10s 再 KILL）。`start_api.py` 完全没有，README 明示「不托管 sidecar」。

设计（对比过的方案：**文档声明手动启动并接受**——被否，因 Web UI 是主界面而 Streamlit 是待退役的 legacy，独有逻辑应随主界面迁移而非随 legacy 消亡）：

- 新建 `scripts/sidecar_manager.py`，原样迁出 `sidecar_healthy` / `ensure_sidecar` / `stop_sidecar` 与常量（`SIDECAR_SCRIPT`、`SIDECAR_STARTUP_TIMEOUT_S`），不 import sirchmunk、不碰主 venv 红线（只 subprocess + urllib）。
- `start_api.py` 新增 `--manage-sidecar`（**默认不托管**，保持现状与 README 口径；显式传入且 `agentic_search.enabled=true` 时按 `ensure_sidecar → uvicorn.run → finally stop_sidecar` 编排，SIGTERM 转 SystemExit 以走 finally，先于 uvicorn 启动、后于其退出）。`agentic_search.enabled=false` 时传了该标志只打提示、不报错。
- `start_dashboard.py` 改为 `from sidecar_manager import ...`（同目录脚本导入），`--no-sidecar` 语义与编排行为不变——**既有测试语义是契约**：`tests/unit/test_start_dashboard.py` 现有 9 个用例的断言不改。

### 2.2 WS1：共享「sidecar 日志 → search_log 帧」转换（台账 #1 的复用前提）

Chat 已有完整实现：`ChatRetriever.retrieve(..., on_log=...)` 把 sidecar 日志回调接入，`ChatEngine._call_retriever` 将回调事件经队列 + 0.5s 去重窗口转成 `{"type": "search_log", task_id, level, message, timestamp?}` 帧（`src/core/chat/engine.py` 455–514）。Streamlit 的 `SearchService.run_stream`（SX5）提供了等价的 `(level, message, timestamp)` 回调入口。

设计：抽出 `src/core/chat/progress.py`（或同级模块，落地时定名）承载「回调事件队列 + 去重 + 帧构造」的纯逻辑；`ChatEngine._call_retriever` 改为调用它（工作线程编排、取消检查仍留在引擎，`_LOG_DEDUP_WINDOW_S` 等常量随迁）。Chat 单测语义不变。

### 2.3 WS2：搜索流式端点 + 默认关闭门控（台账 #1 主体）

- 新端点 `WS /api/v1/search/ws`：请求帧带 `{mode: "deep", query, top_k?, collection?}`，服务端回 `search_log` 进度帧（复用 WS1 helper，`task_id` 用本次 trace id）+ 最终结果帧（形态对齐 `POST /search` 的 `SearchResponse`，`is_error` 口径一致），结束发 `done` 帧。连接管理对齐 Chat WS 先例：origin 校验、连接限流、错误帧、cancel 帧（取消后丢弃结果）。
- **门控**：`api.deep_progress_stream`（`ApiSettings` 新字段，**默认 `false`**，理由：`run_stream` 走 sidecar `search_stream`，命中 SX5.3 每请求约 10 线程泄漏；上游修复 PR 合入并验证回落后再建议开启）。关闭时：`/search/capabilities` 不报 `progress_stream`，WS 握手直接拒绝（错误码 + 错误帧，不进检索链路）。
- 检索执行复用 `SearchService.run_stream`（仅 Deep 走后端流式，其余模式/不支持时回退 `run`，结果形态一致）——不新增第二套流式实现，也不改 SearchService 检索链路（对齐 D8「Playground 原样复用」）。

### 2.4 WS3：前端 Playground Deep 接入

- `capabilities.progress_stream=false`（默认）：Deep 按钮行为与现状完全一致（spinner → 一次性结果），不建立 WS 连接。
- 开启时：Deep 执行改走 WS，进度日志逐条渲染（优先复用 Chat 页的日志渲染组件，不可直接复用则抽共享组件），完成后展示与现在相同的结果区；连接失败降级为现有 `POST /search` 同步路径并提示。

## 3. 测试方案（离线确定性，零网络零真实模型）

| 层 | 注入方式 | 覆盖 |
|----|---------|------|
| WS0 单测 | 沿用 `test_start_dashboard.py` 的 fake（monkeypatch urllib/Popen）；新增 `test_start_api_sidecar.py` | 托管编排语义随迁不变（既有 9 用例零断言改动全绿）；`--manage-sidecar`：enabled=false 只提示、探活复用不重复拉起、拉起后随 uvicorn 退出回收、启动失败/超时降级不致命 |
| WS1 单测 | 纯逻辑直测：fake on_log 回调序列 | 帧构造字段、0.5s 去重窗口、done/异常传播；Chat 既有引擎测试作回归契约 |
| WS2 集成 | FastAPI TestClient WebSocket + fake SearchService（on_event 注入日志序列） | 门控关闭拒绝握手（无任务副作用）；开启后逐条 `search_log` 帧 + 结果帧 + done；cancel 提前断开；capabilities 暴露 `progress_stream`；`responses` 文档同步 |
| WS3 e2e | Playwright + fake 后端 WS 推帧 | 开启：Deep 日志逐条出现 + 最终结果渲染；关闭（默认）：回归 spinner 现状、无 WS 连接；capabilities 驱动的 UI 分支 |

真实 sidecar 流式冒烟沿用 `RAG_AGENTIC_INTEGRATION=1` 门控（不新增门控变量）。

## 4. 任务排期与进度跟踪

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|---------|------|
| WS0 | `scripts/sidecar_manager.py` 共享模块 + `start_api.py --manage-sidecar` + `start_dashboard.py` 改复用 | `test_start_dashboard.py` 全绿零断言改动；新增 `test_start_api_sidecar.py` 全绿；typecheck/lint 干净 | 已完成（2026-10-18） |
| WS1 | 抽共享「sidecar 日志 → search_log 帧」helper，ChatEngine 复用 | 新增纯逻辑单测全绿；Chat 既有测试语义不变全绿 | 已完成（2026-10-18） |
| WS2 | `WS /api/v1/search/ws` + `api.deep_progress_stream`（默认 false）+ capabilities 暴露 | 集成测试全绿（门控拒绝握手、帧序列、cancel、capabilities） | 未开始 |
| WS3 | Playground Deep 前端接入（门控分支） | vitest + Playwright 全绿（开启逐条日志 / 默认回归现状） | 未开始 |
| WS4 | USAGE_GUIDE 第 10 章（WS 协议、`--manage-sidecar`、SX5.3 风险注记）+ README 启动说明更新 | 文档与实现一致；drift guard 绿 | 未开始 |

> 状态回填约定：完成一项回填「已完成（日期）+ 实测数据」，实现偏差记入完成备注。

## 5. 进度备注

- v0.1（2026-10-18）：立项。承接台账 #4（决策：抽共享模块实现）与 #1（决策：默认关闭门控，随上游修复再开启）；二选一与「现在立项 vs 暂缓」由用户拍板。
- WS0（2026-10-18）已完成：`scripts/sidecar_manager.py` 迁入编排（探活/拉起/等健康/回收）；`start_api.py --manage-sidecar`（默认不传不托管，agentic 关闭时只打提示不报错，SIGTERM 转 SystemExit 走 finally 回收，uvicorn 启动后由其自身信号处理优雅关闭）；`start_dashboard.py` 改为委托共享模块。测试：`test_start_dashboard.py` 9 用例零断言改动全绿、`test_start_api_entry.py` 9 用例全绿、新增 `test_start_api_sidecar.py` 6 用例（flag 默认关、默认路径不碰 sidecar、agentic 关闭跳过、ensure→uvicorn→stop 顺序、失败不致命、agentic 注入）；`pytest tests/unit` 2102 passed。实现偏差：① 既有单测经 `cli.` 属性打补丁（`cli.sidecar_healthy` / `cli.SIDECAR_STARTUP_TIMEOUT_S`），故 `ensure_sidecar` 共享实现增加 `probe` / `startup_timeout_s` 注入参数，`start_dashboard.py` 以薄适配器传入本模块全局量，测试文件零改动；② 编排日志前缀 `[dashboard]` → `[sidecar]`（无测试断言依赖）。Python 侧无 lint/typecheck 工具链配置（pyproject 无 ruff/mypy），以 pytest 全绿代之。
- WS1（2026-10-18）已完成：新增 `src/core/chat/progress.py`（`SearchLogFramer.to_frame`：事件 → `search_log` 帧，`(level, message)` 0.5s 去重窗口，窗口常量随迁为 `LOG_DEDUP_WINDOW_S`）；`ChatEngine._call_retriever` 改用 framer（工作线程编排、取消检查、`_LOG_POLL_S` 轮询留在引擎）。测试：新增 `tests/unit/test_chat_progress.py` 5 用例（帧形态、窗口内去重、窗口过期、窗口可配、非字符串矫正）；`test_chat_engine.py` + `test_api_chat.py` 131 passed 语义零改动；`pytest tests/unit` 2108 passed。实现偏差：无（纯抽取，帧协议与去重语义不变）。
