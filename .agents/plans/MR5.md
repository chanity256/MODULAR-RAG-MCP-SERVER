# Plan: [MR5] e2e、门控冒烟与文档收口（离线部分）

Status: implemented

## Deviations

- `src/libs/mode_router/base_mode_router.py`：`backend_name` 原在 `BaseModeRouter.__init__` 写死实例属性 `"unknown"`，遮蔽了 `CloudflareWorkersAIRouter` 的类属性——真实后端的 trace `mode_routing.backend` 会记 `unknown`（MR3 组测试用 FakeRouter 未覆盖此路径）。改为 `getattr(type(self), "backend_name", "unknown")`，子类类属性覆写生效（MR7 新后端同样受益）。由 MR5 不可达用例的 trace 断言发现。
- `src/core/chat/engine.py`：`ModeRouterError` 的模块级导入与 `libs.mode_router.base_mode_router → core.chat.retriever` 反向依赖构成循环，`scripts/eval_mode_router.py` 单独运行即触发 ImportError（USAGE_GUIDE 文档命令实测发现）。导入移入唯一使用点 `_route` 局部作用域。
- `tests/e2e/test_mode_router_real_integration.py`（门控测试文件，默认 skip，属 MR5 离线交付）：真实装配路径下 `base_url` 不可达 → 回退 `fallback_retrieval`、result 帧与 trace 记 `unreachable`；trace 落 tmp 不污染真实日志；凭据缺失时 skip 不 fail。

## Files

1. `tests/e2e_web/fake_api_server.py` — 假后端支持 auto 帧：新增 `ScriptedModeRouter`（按问题关键词定档 / 抛 `unreachable`），注入 `ChatServices`（`_mode_router` 属性注入，不改生产代码），settings 以 `replace(..., mode_router=ModeRouterSettings(enabled=True, ...))` 打开 capabilities 的 `auto_routing`。
2. `frontend/e2e/chat.spec.ts` — 新增 auto 用例：选 Auto 发问 → 断言气泡 meta 行「Auto → 实际模式」；含回退关键词用例断言「(fell back: router unreachable)」。
3. `tests/unit/test_chat_auto_mode.py` — 追加 MR5 组：真实 `CloudflareWorkersAIRouter` + `base_url` 指向不可达 loopback 地址 → `decide` 抛 `unreachable`；经 `ChatEngine` auto 轮次回退 `fallback_retrieval`，result 帧 `routing.fallback_reason == "unreachable"`、trace `mode_routing` 阶段记 `unreachable`。
4. `docs/reference/USAGE_GUIDE.md` — 新章节「Auto 检索模式（mode_router）」：概念、配置节、环境变量、回退语义、AI Gateway `base_url` 覆盖、门控冒烟命令；目录同步；§10.8 门控环境变量表补 `RAG_MODE_ROUTER_INTEGRATION`。
5. `docs/specs/MODE_ROUTER_SPEC.md` — MR5 行回填离线部分完成状态（门控真实冒烟单独执行后再收口）。

## Order

1. fake_api_server（先，Playwright 用例依赖其 auto 能力）→ 2. Playwright 用例 → 3. 离线单测 → 4. 跑后端 pytest + 前端 typecheck/lint/test → 5. 构建 frontend/out 后跑 Playwright chat spec（离线假后端）→ 6. USAGE_GUIDE → 7. 规格回填 + sync_spec --force。

## Risks

- `ChatServices._mode_router` 是私有属性注入：仅 e2e 假后端使用，生产装配路径不变；docstring 已声明「所有成员都可注入」，不改公开契约。
- Playwright 依赖 `frontend/out` 构建：MR4 改过前端，必须重建后再跑，否则 Auto 选项不出现（假象失败）。
- 不可达地址测试必须只用 loopback 关闭端口（CI pytest-socket 只放行 loopback），连接立即被拒，零真实网络。
- 既有测试语义：帧协议只增不改；非 auto 轮次帧与基线一致；`ScriptedModeRouter` 只在 auto 轮次被调用。

## Verification

- `NO_PROXY='*' pytest tests -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1` 全绿（基线 2939 passed，新增 MR5 组）。
- `python scripts/check_redlines.py` 0 违规。
- `npm run typecheck && npm run lint && npm test` 全绿。
- `npm run build && npm run e2e`（chat.spec.ts 全绿，含新 auto 用例）。
- 门控真实冒烟（`RAG_MODE_ROUTER_INTEGRATION=1` + 对比记表）**不自动执行**，暂停请示用户。

## Deviations

（实现偏离时回填。）
