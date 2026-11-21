# Plan: [AC4] Hybrid 第三路白名单修复 + collection 定界

Status: implemented

> 实测（2026-11-21）：新测试 10 项 + 既有第三路套件（query_agentic /
> hybrid_agentic_route / agentic_dedup）56 项全绿；全量禁网 pytest
> 3005 passed / 23 skipped / 1 xfailed；红线脚本通过；Fix-Rounds: 1。
> 一处实现细化（非偏离）：装配模式下 `last_response` 记录过滤后响应
> （供 verbose/评估展示本路实际使用的证据；Token/耗时字段原样保留），
> 裸构造基线模式仍为原始响应、逐字节一致。

## Files
- `src/libs/agentic_search/adapter.py` — 适配器扩展（settings/history 注入、懒装配、显式 paths、证据过滤）
- `src/core/query_engine/hybrid_search.py` — 第三路调用点条件传 `collection=` 关键字
- `tests/unit/test_agentic_route_scoping.py` — 新文件（既有第三路测试文件零改动）

## Order
1. `AgenticRouteAdapter.__init__(backend, *, settings=None, history=None)`：
   新增可选 `settings`（供懒装配）与 `history`（测试注入）；缺省构造
   （`AgenticRouteAdapter(backend)`）保持基线。
2. 懒装配 getter：`_get_path_guard()`（settings 为 None → 返回 None=基线模式；
   否则 `PathGuard(cfg.allowed_roots, cfg.denied_substrings)`，构造失败即抛，
   不调用 sidecar）；`_get_history()`（仅 collection 分支触达，懒构造
   `SQLiteIntegrityChecker`）。
3. `retrieve(query, top_k=None, trace=None, collection=None)`：
   - guard 为 None（基线）→ 与现状逐字节一致（`paths=None`，证据不过滤）；
   - guard 存在 + 无 collection → `paths = guard.allowed_roots`（白名单修复，
     不再回退 sidecar CWD）；
   - guard 存在 + collection → AC1 resolver 推定界集；空集返回 `[]`（D3，
     不调 sidecar）；非空 `paths = scoped`；
   - guard 存在时证据恒经 `filter_evidence_paths`（白名单外剔除），带
     collection 再按 scoped 成员过滤，`evidence_to_retrieval_results` 之前完成；
     响应重建字段与 MCP tool `_build_result` 完全同形。
   - `last_response` / `last_elapsed_ms` 语义不变。
4. `build_agentic_retriever(settings)` → `AgenticRouteAdapter(backend, settings=settings)`。
5. `hybrid_search.py` 第三路调用：`processed.filters.get("collection")` 非空时
   才追加 `collection=` 关键字；否则调用形态与基线完全相同（鸭子类型 fake
   `retrieve(query, top_k, trace=None)` 不改即过）。
6. 新测试（六组，见 Verification）。

## Risks
- **基线兼容（最大风险）**：`test_query_agentic.py` 断言裸构造适配器发送
  `AgenticSearchRequest(query=...)`（paths=None）→ 由「guard 为 None 即基线
  模式」保证；`test_hybrid_agentic_route.py` / `test_agentic_dedup.py` 的
  鸭子类型 fake → 由条件关键字保证；未触发第三路主链路逐字节一致 →
  第三路代码路径只在 `triggered` 内变化，调用形态变化仅限该分支。
- PathGuard 契约 / sirchmunk import 红线不变：只消费 `validate_paths` /
  `filter_evidence_paths` / `allowed_roots`（经 AC1 组件）。
- `evaluate.py` / `query.py --agentic` 经 `build_agentic_retriever` 获得装配
  后适配器 → 行为收紧（恒发显式 paths）为 spec 明示的预期变化。

## Verification
- `pytest tests/unit/test_agentic_route_scoping.py tests/unit/test_query_agentic.py
  tests/unit/test_hybrid_agentic_route.py tests/unit/test_agentic_dedup.py -o addopts="" -q`
  全绿（新六组：无 collection 发 allowed_roots / collection 定界与空集短路 /
  证据两级过滤 / PathGuard 构造失败降级 / 鸭子类型兼容 / S2.3 未触发回归）。
- 全量禁网 pytest + `check_redlines.py` 全绿；既有第三路测试文件零改动。

## Deviations
（实现偏离时回填）
