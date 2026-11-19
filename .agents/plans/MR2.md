# Plan: [MR2] libs/mode_router 组件 + 工厂（cloudflare_workers_ai 后端）

Status: implemented

## Files
1. `src/libs/mode_router/routing_question.py` — zh-v2 / en-v2 文案单一来源 + 按候选生成 choice 问题。
2. `src/libs/mode_router/base_mode_router.py` — `ModeRouterError`（reason 五型）+ `BaseModeRouter`（候选校验 / D8 置信度回退 / probabilities 归一共用；`decide` 失败抛异常，回退归引擎 MR3）。
3. `src/libs/mode_router/system_one_router.py` — System One 请求构造 / 响应解析（兼容 Workers AI 包装与无包装）+ `CloudflareWorkersAIRouter`（httpx 同步客户端，懒建持久连接 + `close()`，transport 可注入）。
4. `src/libs/mode_router/mode_router_factory.py` — `create_mode_router(settings)`；`enabled=false → None`。
5. `src/libs/mode_router/__init__.py` — 导出。
6. `scripts/eval_mode_router.py` — 文案改从 `routing_question` 导入（3.4：MR2 起脚本与运行时共用一份）；加 src path shim。
7. `tests/unit/test_mode_router.py` — §4 组件组全部用例（MockTransport，零网络）。

## Order
routing_question（无依赖）→ base → system_one_router → factory → __init__ → 脚本重构 → 测试。

## Risks
- 红线：主 venv 不 import 推理框架 / 厂商 SDK，仅 httpx（已有依赖）；无 sirchmunk import。
- `decide` 抛异常不回退——回退语义归 ChatEngine（MR3），组件只负责失败分型。
- 持久 httpx.Client 线程安全（httpx 保证），懒建 + 锁 + `close()`；transport 注入供 MockTransport。
- 脚本重构后 `tests/unit/test_eval_mode_router_script.py` 经 `entry.CRITERIA` 别名应不改即通过（zh/en 两变体保留，v1 历史变体不进运行时库）。

## Verification
- `pytest tests/unit/test_mode_router.py tests/unit/test_eval_mode_router_script.py -q` 全绿。
- CI 等价禁网全量 + `check_redlines.py` 0 违规。

## Deviations
（无）
