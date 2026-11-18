<!-- 规格定位：为 Web UI Chat 新增 `auto` 检索模式，由进程外本地部署的微调决策模型在 hybrid / agentic_fast / agentic_deep 中定档，失败即回退。基线：S / SX / WC / CMC / AR 均已收口冻结。与既有规格的关系：ChatEngine 与 Chat 前端的扩展，不改三档既有模式的行为契约，不改 MCP 工具，不回改 CMC 冻结规格。 -->

# 检索模式路由器规格 (MODE_ROUTER_SPEC)

> 版本：0.1 — 由 backlog 提案立项为可执行规格；MR0.2 决策质量门控通过前不进入编码（MR1 起）
>
> 起源与决策依据：由 backlog/mode-router_backlog.md 单文件晋升，原文见 git 历史；提案起源为会话提案（2026-11-16，用户需求：检索模式选择交由本地部署的微调决策模型自动完成，非文件来源）。依据 [06_WEBUI_CHAT_SPEC_frozen.md](../frozen/06_WEBUI_CHAT_SPEC_frozen.md)（Chat 引擎、检索器协议 D8、WS 帧协议）、[03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) S2.2（`agentic_search.trigger` 与独立校准集纪律）、[11_CHAT_MODE_COLORS_SPEC_frozen.md](../frozen/11_CHAT_MODE_COLORS_SPEC_frozen.md)（模式选择器配色规则）
>
> 基线：`a404207`（main），S / SX / WC / CMC / AR 系列均已收口冻结；`mode_router` 配置节与 `auto` 模式尚不存在
>
> 日期：2026-11-18

红线不变：主 venv 不 import 推理框架、不加载模型权重；决策模型只经 HTTP 调用独立进程；`mode_router.enabled: false`（默认）时零加载，存量行为完全不变。

## 目录

- 1 项目概述（1.1 结论速览、1.2 背景与现状、1.3 决策依据与终裁、1.4 对提案的勘误）
- 2 核心目标与范围（2.1 In Scope、2.2 Out of Scope）
- 3 技术选型与模块设计（3.1 运行架构、3.2 模块设计、3.3 配置契约、3.4 涉及文件清单、3.5 选型理由）
- 4 测试方案
- 5 任务排期与进度
- 6 验收清单
- 7 实现合入后的回写清单
- 8 隐私注记
- 9 风险与开放问题（本规格追加章节）

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 要解决什么问题？ | 三档检索模式的延迟与能力差异很大，用户每轮只能人工三选一或固定一档，简单问题忍受 deep 的延迟、复杂问题只拿到 hybrid 的浅层结果 |
| 定位是什么？ | ChatEngine 与 Chat 前端的扩展：新增 `auto` 请求值，检索前由本地决策模型定档，之后走既有三档检索路径（零改动）；不替换既有模式 |
| 实施范围（已决策） | MR0.2 为立项门（决策质量不达标即搁置）；MR1–MR4 为承诺任务（门控通过后）；MR5 含门控真实冒烟。MCP 侧不做（D1） |
| 主要代价 | 每轮多一次本地决策调用（硬顶 `timeout_s`）；新增一个可选进程外依赖与配置节；模式常量多处登记需同步扩展 |

### 1.2 背景与现状

系统现有三种检索模式：

| 模式 | 延迟量级 | 能力边界 |
|------|---------|---------|
| `hybrid`（Dense + BM25 + RRF） | 秒级 | 主库内语义 + 关键词检索，依赖已摄取语料 |
| `agentic_fast`（Sirchmunk FAST） | 十秒级 | 可越出主库扫描白名单目录，单轮探索 |
| `agentic_deep`（Sirchmunk DEEP） | 数十秒级 | 多轮迭代探索 + 目录扫描，成本最高 |

当前用户要么每轮在 Web UI 手选，要么把 `chat.default_retrieval` 固定为一档。问题复杂度、是否可能超出已摄取语料、对延迟的容忍度，这三类信息在提问时就能从问题文本推断，属于小模型分类任务。

代码现状（基线 `a404207`）：

- `src/core/chat/engine.py:240-244`：`run_turn` 解析 `mode = retrieval or self._settings.default_retrieval`，随即校验 `mode in RETRIEVAL_MODES` 与 `mode in self._retrievers`；参数校验在调用时同步完成，之后才返回 `_run` 生成器。
- `src/core/chat/engine.py:286-298`：`_run` 先做历史处理与查询改写（`_rewrite`），再用改写后的 `search_query` 调检索器。
- `src/observability/dashboard/services/chat_retrievers.py:203-223`：`build_chat_retrievers` 在 `agentic_search.enabled` 时同时注册 fast 与 deep；**deep 不受 `allow_deep` 影响照样注册**，由调用时抛 `DeepDisabledError` 门控。引擎对 `DEEP_DISABLED` 不重试、不降级（`engine.py:302`）。
- `src/api/chat_wiring.py:22-42`：`compute_capabilities` 已按 `agentic_search.enabled` 与 `allow_deep` 计算可用模式与 `allow_deep`。
- 模式枚举分散在六处：`core/chat/retriever.py:28` `RETRIEVAL_MODES`、`core/settings.py:349` `CHAT_RETRIEVAL_MODES`、`api/chat_wiring.py:19` `_ALL_MODES`、`api/schemas/chat.py:24` `RetrievalMode`（同时约束 `user_message.retrieval`、`capabilities.retrievals` 与 `capabilities.default_retrieval`）、`frontend/lib/chat/types.ts:3-5`、`frontend/lib/chat/normalize.ts:31` `isRetrievalMode`（capabilities 的非法 `default_retrieval` 被静默归一为 hybrid）。
- `src/mcp_server/tools/query_knowledge_hub.py:48-66`：MCP 工具只做 hybrid，不经 ChatEngine；agentic 检索是独立工具 `agentic_search`。

### 1.3 决策依据与终裁

提案三问的答案：

1. **已观察到的问题**：见 1.1、1.2。
2. **更简单的方案**：
   - 维持 `default_retrieval`：零成本，但失衡持续；本规格以 `enabled: false` 为默认，存量行为不变。
   - 规则/启发式路由（关键词、长度阈值）：覆盖不了「是否超出语料」这类语义信号，规则集合会持续膨胀。
   - 主 LLM 兼任决策：每轮多一次主 LLM 调用（延迟与费用），且与主 LLM 共用故障域；本地小模型决策毫秒级、零 token 费用。
   - 交叉编码器分数触发第三路（`agentic_search.trigger: fallback`，S2.2）：在检索**之后**补触发，解决「信号不足时补救」；本规格在检索**之前**定模式，解决「用户免选」。两者正交，可共存。
3. **如何确认有效**：
   - 决策质量：独立标注集（问题 → 期望模式）上的命中率与混淆矩阵（MR0.2）；标注集与 golden test set 分离，不用评估 golden set 调参（S2.2 独立校准集先例）。
   - 端到端：同一问题集合上，`auto` 与人工三选一的「回答质量 × 平均延迟 × token 成本」对比（MR5）。
   - 降级安全：路由器失败/超时的用例回退 `fallback_retrieval` 后，回答质量不劣于固定 hybrid 基线（MR3 离线 + MR5 冒烟）。

| 编号 | 决策 | 终裁 |
|------|------|------|
| D1 | MCP `query_knowledge_hub` 是否新增 `retrieval` / `auto` 参数（原提案 G7 后半与 MR5） | **不做**（2026-11-18，用户拍板）。MCP 调用方是外部 LLM，它已经通过选择 `query_knowledge_hub` 或 `agentic_search` 工具来选检索方式；让 hybrid 工具转派 agentic 会改变 MCP 工具契约与描述。后续有需求另立提案 |
| D2 | MR0.2 决策质量门槛 | **本规格只固定度量口径，数值门槛在 MR0.2 基线跑出后与用户共同拍板**（2026-11-18，用户拍板）。门槛终裁前 MR1 不开工；终裁结果回填本表与 MR0.2 行 |
| D3 | sidecar 已配置但未启动时 auto 如何处理 | **不做存活探测**（2026-11-18，用户拍板）。候选集只看配置与 `allow_deep`；路由选中 agentic 模式但 sidecar 不可用时，行为与人工选中该模式完全一致（默认 error 帧，`chat.fallback_to_chat_only: true` 时降级纯聊天），规格与 USAGE_GUIDE 写明 |
| D4 | 候选集来源 | **由装配层计算后注入引擎**（规格设计决策，依据 1.4 勘误 1）：候选 = 已注册检索器 ∩ 门控可用（`allow_deep=false` 时剔除 `agentic_deep`），与 `compute_capabilities` 同一口径；引擎不直接取 `self._retrievers` 的 keys |
| D5 | 决策时机与输入 | **在 `_run` 中查询改写之后决策，输入改写后的 `search_query`**（规格设计决策，依据 1.4 勘误 3）：多轮追问（如「那第二点呢」）用改写后的独立问题判断复杂度；决策期间下发 `status` 帧，`run_turn` 的同步校验不引入网络调用 |

### 1.4 对提案的勘误

1. **门控候选集（原 G4）不成立**：提案称「候选集直接取 `self._retrievers` 的 keys，`allow_deep=false` 时 deep 不进候选」。实际 deep 在 agentic 启用时总被注册（1.2 第 3 条），按原方案路由器可能选中被禁用的 deep，得到不降级的 `DEEP_DISABLED` 错误。改为 D4。
2. **sidecar 未启动时不进候选不成立**：检索器按配置注册，与 sidecar 存活无关。改为 D3 的明确口径。
3. **插入点**：提案写的 `ChatEngine.chat()` 不存在，实际入口是 `run_turn`（同步校验）+ `_run`（生成器）。改为 D5。
4. **模式常量引用点**：提案只列了 `RETRIEVAL_MODES` 与 `RetrievalMode`，实际有六处（1.2 第 5 条）；`capabilities.default_retrieval` 用 `RetrievalMode` 类型，`auto` 作默认值时会被 schema 拒绝，前端 `isRetrievalMode` 会把它静默改成 hybrid。处置见 3.2。
5. **MCP 侧**：`query_knowledge_hub` 只做 hybrid，不经 ChatEngine，加参数等于让它转派 agentic 工具。按 D1 移出范围；原 MR5（MCP）删除，原 MR6 顺延为 MR5。
6. **装配位置**：提案写「`services/` 内新增 `build_mode_router`」。路由器不依赖 Pipeline 或存储，直接由 `src/api/chat_wiring.py` 经 `libs/mode_router` 工厂懒建即可（与其懒建 `LLMFactory` 同构），不新增 services 文件。
7. **backend 枚举**：Ollama 自带 OpenAI 兼容端点（`/v1/chat/completions`），单一 `openai_compatible` 实现即可覆盖 vLLM 与 Ollama；不单列 `ollama` backend。
8. **出处**：提案的「DEV_SPEC 9.5 纪律」在主规格中不存在，独立校准集纪律的先例是 SIRCHMUNK S2.2；`trigger: fallback` 属 S 系列 S2.2，不属 SX 系列。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| 预研 | 决策质量基线 | 门控（MR0.2，立项门） | 独立标注集 + 已部署微调模型的命中率 / 混淆矩阵 / 延迟基线；门槛按 D2 终裁，不达标即搁置 |
| 配置 | `mode_router` 配置节与常量 | 承诺（MR1） | `ModeRouterSettings` + fail-fast 校验；`chat.default_retrieval` 允许 `auto`（需路由器启用）；`RETRIEVAL_AUTO` 常量与 `ModeRouter` 协议 |
| 组件 | `libs/mode_router/` | 承诺（MR2） | `BaseModeRouter` + OpenAI 兼容实现 + 工厂；`enabled: false` 时工厂返回 `None`、零加载 |
| 引擎 | ChatEngine auto 分支 | 承诺（MR3） | 改写后决策 → 候选内校验 → 失败回退 `fallback_retrieval`；决策与降级原因入 trace 与 `done` 帧 |
| API + 前端 | capabilities 与 Chat UI | 承诺（MR4） | schema 区分「请求模式」与「实际模式」；capabilities 上报 `auto_routing`；ModeSelector 新增 Auto 选项与配色 |
| 验证与文档 | e2e、门控冒烟、USAGE_GUIDE | 承诺（MR5，其中真实冒烟门控） | 假后端 e2e 离线覆盖 auto 帧链路；`RAG_MODE_ROUTER_INTEGRATION=1` 真实端点冒烟；使用文档收口 |

### 2.2 Out of Scope（明确不做）

- **决策模型的训练/微调**：模型由外部带入，本规格只管已部署模型的接入、调用与降级。
- **MCP 侧 auto**（D1）：`query_knowledge_hub` 与 `agentic_search` 的输入 schema、描述与行为不变。
- **Query Playground 的 auto**：Playground 是调试各档模式的工具，显式选型即其语义。
- **`scripts/query.py`、`scripts/evaluate.py`、`EvaluationService` 感知 auto**：CLI 与评估链路不经 ChatEngine，保持显式模式。
- **sidecar 存活探测**（D3）：不在候选计算或每轮决策前探测 sidecar。
- **决策后二次升级**（如 hybrid 结果弱再自动重跑 deep）：属 `agentic_search.trigger` 的职责范围，本规格不在引擎内新增重试分支。
- **决策结果缓存**：本地小模型毫秒级，缓存收益不成比例；后续观察再议。
- **改变既有三档模式的行为契约**：`RETRIEVAL_MODES` 校验、capabilities 的 `retrievals` 列表、`DEEP_DISABLED` 不降级、`agentic_search.trigger` 均不改语义；`auto` 走独立常量。
- **回改 CMC 冻结规格**：`auto` 配色是本规格的新行为，三档既有配色与 `11_CHAT_MODE_COLORS_SPEC_frozen.md` 不动。

## 3. 技术选型与模块设计

### 3.1 运行架构

```
Web UI Chat：user_message { retrieval: "auto" }   （或 chat.default_retrieval: auto 且未传 retrieval）
   │
   ▼
ChatEngine.run_turn（同步校验）
   │  auto 且未注入 router → ValueError（WS 映射 BAD_REQUEST）
   ▼
ChatEngine._run（生成器）
   │  历史处理 → _rewrite → search_query
   │  yield status { stage: "routing" }                         ← MR3 新增
   │  ModeRouter.decide(search_query, candidates)               ← 候选由 chat_wiring 注入（D4）
   │     ├─ 成功且 ∈ candidates → mode = 决策结果
   │     └─ 超时 / 不可达 / 非法输出 / 异常 → mode = fallback_retrieval，记 fallback_reason
   │  trace 记 mode_routing 阶段；state.retrieval = mode
   ▼
既有 _retrieve(self._retrievers[mode], ...) → 回答 → done { retrieval: mode, routing: {...} }

装配（src/api/chat_wiring.py，懒建）：
   create_mode_router(settings)  ── enabled=false → None（零加载）
   candidates = 已注册检索器 ∩ 门控可用（allow_deep=false 剔除 agentic_deep）
   ChatEngine(..., mode_router=router, auto_candidates=candidates, auto_fallback=fallback_retrieval)

决策模型（进程外）：vLLM / Ollama 的 OpenAI 兼容端点
   POST {base_url}/chat/completions  { model, messages, temperature: 0, max_tokens: 8 }
```

### 3.2 模块设计

- **`src/core/chat/retriever.py`**（MR1）：新增 `RETRIEVAL_AUTO = "auto"`（不进 `RETRIEVAL_MODES`）与 `ModeRouter` Protocol：`decide(query: str, candidates: tuple[str, ...]) -> str`。core/chat 保持零实现依赖。
- **`src/libs/mode_router/`**（MR2）：
  - `base_mode_router.py`：`BaseModeRouter` 抽象 + `ModeRouterError`（带 `reason` 字段：`timeout` / `unreachable` / `invalid_output` / `error`）。
  - `openai_compatible_router.py`：httpx 同步客户端，`timeout=timeout_s`；system prompt 列出候选标签、要求只输出一个标签；输出 `strip().lower()` 后必须精确等于某个候选，否则抛 `invalid_output`。prompt 模板与 MR0.2 评估脚本共用同一份（单一来源）。
  - `mode_router_factory.py`：`create_mode_router(settings) -> BaseModeRouter | None`。
- **`ChatEngine`**（MR3）：构造参数新增 `mode_router`、`auto_candidates`、`auto_fallback`（均可选）。构造时校验 `auto_fallback ∈ auto_candidates`。`run_turn` 中 `auto` 仅在 router 注入时合法；`_run` 中按 D5 决策，引擎对决策结果再做一次候选内校验（不信任实现）。任何异常都回退，不中断会话。
- **trace 与帧**（MR3，只增不改）：
  - trace 新增 `mode_routing` 阶段：`requested="auto"`、`decided`、`candidates`、`fallback_reason`（成功时为 `null`）、`elapsed_ms`；trace 元数据 `retrieval` 记**实际使用的模式**。
  - `done` 帧 `retrieval` 仍为实际模式，新增可选 `routing` 对象（同上字段）；非 auto 轮次不出现该字段。
  - 会话 `settings.retrieval` 记用户请求值（`auto`），以便恢复会话时保留选择。
- **API schema**（MR4）：保留 `RetrievalMode`（三档真实模式）供 `capabilities.retrievals` 使用；新增 `RequestedRetrieval = RetrievalMode | Literal["auto"]` 用于 `user_message.retrieval` 与 `capabilities.default_retrieval`；capabilities 新增 `auto_routing: bool`（路由器启用且候选非空）。`retrievals` 列表不含 `auto`。
- **前端**（MR4）：`types.ts` 增加请求模式类型；`normalize.ts` 允许 `default_retrieval = "auto"`（仅当 `auto_routing` 为 true，否则仍归一为 hybrid）；`modes.ts` 在 `auto_routing` 为 true 时提供 Auto 选项，`resolveMode` 同步处理；ModeSelector 为 Auto 增加第四种选中态配色，延续 CMC 规则（light 浅底深字 / dark 半透明底亮字，对比度 ≥ WCAG AA），三档既有配色不变；助手消息展示实际模式（如「Auto → Hybrid」），回退时提示回退原因。

### 3.3 配置契约

`config/settings.yaml` 新增可选节（缺失即 `enabled: false`），风格对齐 `agentic_search` 节：

```yaml
mode_router:
  enabled: false                 # true 才提供 auto 模式；false 时零加载
  backend: openai_compatible     # 唯一取值；vLLM 与 Ollama 均经 OpenAI 兼容端点接入
  base_url: http://127.0.0.1:8590/v1
  api_token: ${MODE_ROUTER_API_TOKEN}   # 从环境变量解析，不落盘；本地端点可留空
  model: mode-router             # 部署时的模型名
  timeout_s: 3                   # 决策发生在每轮检索之前，超时即回退
  fallback_retrieval: hybrid     # 决策失败 / 超时 / 输出非法时的兜底模式
```

校验（fail-fast，`ConfigError`）：

- `enabled` 布尔；`backend` 只能是 `openai_compatible`；`base_url` 为非空 http(s) URL；`model` 非空；`timeout_s` 为正数且不超过 10。
- `fallback_retrieval ∈ CHAT_RETRIEVAL_MODES`；取 agentic 模式时要求 `agentic_search.enabled: true`，取 `agentic_deep` 时另要求 `allow_deep: true`。
- `chat.default_retrieval: auto` 仅当 `mode_router.enabled: true` 时合法，否则启动即报错。

部署示例（写入 USAGE_GUIDE）：

```bash
vllm serve /path/to/your-finetuned-model --host 127.0.0.1 --port 8590   # base_url: http://127.0.0.1:8590/v1
ollama create mode-router -f ./Modelfile && ollama serve                # base_url: http://127.0.0.1:11434/v1
```

### 3.4 涉及文件清单

新增：

| 文件 | 职责 | 任务 |
|------|------|------|
| `scripts/eval_mode_router.py` | 决策质量评估脚本：读标注集、调真实端点、输出命中率 / 混淆矩阵 / 延迟分位 / 非法输出率；真实运行，不进 `pytest tests` | MR0.2 |
| `tests/fixtures/mode_router/labeled_set.jsonl` | 独立标注集（问题 → 期望模式），与 `golden_test_set.json` 分离 | MR0.2 |
| `src/libs/mode_router/__init__.py`、`base_mode_router.py`、`openai_compatible_router.py`、`mode_router_factory.py` | 3.2 所述组件 | MR2 |
| `tests/unit/test_mode_router.py` | 组件离线单测 | MR2 |
| `tests/unit/test_chat_auto_mode.py` | 引擎 auto 分支离线单测 | MR3 |

修改：

| 文件 | 改动 | 任务 |
|------|------|------|
| `src/core/settings.py` | `ModeRouterSettings` + 校验；`chat.default_retrieval` 允许 `auto` | MR1 |
| `src/core/chat/retriever.py` | `RETRIEVAL_AUTO` + `ModeRouter` Protocol | MR1 |
| `config/settings.yaml` | `mode_router` 节示例（`enabled: false`） | MR1 |
| `src/core/chat/engine.py` | auto 分支、`mode_routing` trace 阶段、`done.routing` | MR3 |
| `src/api/chat_wiring.py` | 懒建 router、计算候选并注入、capabilities `auto_routing` | MR3 / MR4 |
| `src/api/schemas/chat.py` | `RequestedRetrieval`、`auto_routing` 字段 | MR4 |
| `frontend/lib/chat/types.ts`、`normalize.ts`、`modes.ts`、`components/chat/ModeSelector.tsx`（及消息展示组件） | 3.2 前端改动 | MR4 |
| `tests/e2e_web/fake_api_server.py` 与 `frontend/e2e/` | 假后端支持 auto 帧 | MR5 |

### 3.5 选型理由

1. **进程外 HTTP 端点 vs 主 venv 内加载模型**：与 Sirchmunk sidecar 同一隔离原则；主 venv 不加推理依赖，端点故障只降级不拖垮主服务；httpx 已在依赖中，`requirements.txt` / `pyproject.toml` 依赖区不动。
2. **`auto` 独立常量 vs 并入 `RETRIEVAL_MODES`**：`RETRIEVAL_MODES` 同时用于引擎校验与 capabilities 过滤，混入 `auto` 会让「不存在名为 auto 的检索器」的校验失效，并污染 `capabilities.retrievals`。
3. **装配层注入候选 vs 引擎取 keys**：见 D4 与 1.4 勘误 1；与 `compute_capabilities` 同一口径，前端看到的可用模式与路由器的候选一致。
4. **改写后决策 vs `run_turn` 中决策**：`run_turn` 是同步校验点，放入网络调用会在推流前阻塞最多 `timeout_s`，且拿不到改写后的查询。
5. **单一 `openai_compatible` 实现 vs 每种部署一个后端**：Ollama 与 vLLM 都提供 OpenAI 兼容端点，一个实现覆盖两者，少一个配置分支。

## 4. 测试方案

- **离线确定性测试**（零网络，`httpx.MockTransport` 与 fake router 注入）：
  - 配置（MR1）：缺省节 → `enabled=false`；各字段非法值 fail-fast；`fallback_retrieval` 与 agentic / `allow_deep` 的交叉校验；`default_retrieval: auto` 在路由器未启用时报错、启用时通过。
  - 组件（MR2）：MockTransport 返回合法标签 → 返回该模式；大小写与首尾空白归一；候选外标签、空输出、多标签 → `invalid_output`；超时 → `timeout`；连接失败 → `unreachable`；请求体含 `temperature: 0`、`max_tokens: 8` 与候选列表；`api_token` 存在时带 `Authorization` 头；工厂 `enabled=false` 返回 `None` 且不 import httpx 客户端以外的模块。
  - 引擎（MR3）：fake router 选中各模式 → 走对应检索器；router 抛各类错误 / 返回候选外值 → 回退 `fallback_retrieval`，`fallback_reason` 正确；决策使用改写后的查询；决策前下发 `routing` status 帧；trace `mode_routing` 阶段字段完整、元数据 `retrieval` 为实际模式；`done.routing` 只在 auto 轮次出现；未注入 router 时 `auto` → `ValueError`；`auto_fallback ∉ auto_candidates` 构造即报错。
  - 装配（MR3/MR4）：`allow_deep=false` 时候选不含 `agentic_deep`；agentic 关闭时候选只有 hybrid；capabilities `auto_routing` 随启用状态变化、`retrievals` 不含 `auto`。
  - 前端（MR4，vitest）：`auto_routing` 为 true 时出现 Auto 选项、false 时不出现；`normalize` 对 `default_retrieval: "auto"` 的两种分支；`resolveMode` 选择逻辑；ModeSelector Auto 选中态 class 与对比度断言，既有三档用例不变。
  - e2e（MR5）：`tests/e2e_web` 假后端 + Playwright 用例：选 Auto 发问 → 收到 `routing` status 帧与带 `routing` 的 `done` 帧 → 消息展示实际模式。
- **真实依赖冒烟**：`RAG_MODE_ROUTER_INTEGRATION=1` 门控，需真实决策端点已启动，默认跳过（MR5）。决策质量评估（`scripts/eval_mode_router.py`）真实运行、不进 `pytest tests`、不阻塞 CI（MR0.2）。
- **既有测试语义**（不得破坏）：
  - `ChatEngine` 构造时拒绝未知检索模式；`run_turn` 对未注册模式抛 `ValueError`。
  - `DEEP_DISABLED` 不重试、不降级（含 auto 路由选中的情形——按 D4 不会选中被禁用的 deep）。
  - `chat.fallback_to_chat_only` 的降级语义不变（D3：auto 选中 agentic 后的检索失败与人工选中一致）。
  - `capabilities.retrievals` 只含三档真实模式；`allow_deep` 字段口径不变。
  - WS 帧协议既有字段只增不改；非 auto 轮次的帧与 trace 与基线一致。
  - CMC 三档配色与 `ModeSelector` 既有单测不变；`aria` 语义不变。
  - MCP `query_knowledge_hub` / `agentic_search` 的 schema 与行为不变（D1）。
  - 全量 `pytest tests -q`、CI 等价禁网命令、`python scripts/check_redlines.py`、前端 `typecheck` / `lint` / `test` 保持全绿。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| MR0.1 | 立项与决策终裁 | D1–D3 终裁、D4/D5 设计决策与 1.4 勘误记入 §1；MOC、backlog/README、README 导航同步；`test_docs_links.py` 与 `test_spec_structure.py` 通过 | 已完成（2026-11-18） |
| MR0.2 | 决策质量预研（立项门） | 前置：用户提供已部署的微调模型端点。标注集入库（每档模式均有覆盖，与 golden set 分离）；`scripts/eval_mode_router.py` 输出总体命中率、三档混淆矩阵、deep 误选率（期望非 deep 却选 deep）、hybrid 误降率（期望非 hybrid 却选 hybrid）、非法输出率、决策延迟 p50 / p95；数值门槛按 D2 与用户拍板并回填 §1.3；未达标即本规格搁置，不改代码 | 未开始（等待模型端点） |
| MR1 | 配置节 + 常量与协议 | §4 配置组用例通过；`RETRIEVAL_MODES` / `CHAT_RETRIEVAL_MODES` 内容不变；`config/settings.yaml` 示例 `enabled: false` | 未开始（MR0.2 门控） |
| MR2 | `libs/mode_router/` 组件 + 工厂 | §4 组件组用例通过；红线脚本 0 违规；主 venv 未新增依赖 | 未开始 |
| MR3 | ChatEngine auto 分支 + 装配注入 | §4 引擎组与装配组用例通过；既有 Chat 单测与 WS 集成测试不改即通过 | 未开始 |
| MR4 | API schema、capabilities 与 Chat 前端 | §4 装配组 capabilities 断言与前端 vitest 通过；`npm run typecheck && npm run lint && npm test` 全绿；Auto 选中态对比度 ≥ WCAG AA（light / dark 实测记入本表） | 未开始 |
| MR5 | e2e、门控冒烟与文档收口 | 假后端 Playwright auto 用例通过；`RAG_MODE_ROUTER_INTEGRATION=1` 下真实端点对三类典型问题给出合法决策、停掉端点后回退 `fallback_retrieval` 且 trace 记 `unreachable`；同一问题集合 auto 与人工三选一的质量 / 延迟 / 成本对比记入本表；USAGE_GUIDE 新章节合入 | 未开始 |

> 进度备注：交付后在各任务下回填状态与关键实测数据；全部收口后本规格移入 `docs/frozen/` 并更新 MOC.md。MR0.2 不达标时，按 docs-governance 将本规格移回 backlog 或止于分析，并在谱系表如实记录。

## 6. 验收清单

- [ ] `mode_router.enabled: false`（默认）时，Chat 的帧、trace、capabilities 与基线一致，未加载路由器
- [ ] 启用后 Web UI 出现 Auto 选项；选 Auto 提问，助手消息显示实际使用的模式，trace 可查决策与耗时
- [ ] `allow_deep: false` 时 Auto 永不选 deep；决策端点停掉或超时后，回答走 `fallback_retrieval` 且标明回退原因，会话不中断
- [ ] MCP 工具的 schema 与行为不变
- [ ] `pytest tests -q`、CI 等价禁网命令、`python scripts/check_redlines.py`、前端 typecheck / lint / test / build 全绿
- [ ] 回写完成（§7）

## 7. 实现合入后的回写清单

- `docs/reference/USAGE_GUIDE.md`：新章节（决策模型部署 vLLM / Ollama、`mode_router` 配置、Auto 用法、降级行为与 D3 口径、`RAG_MODE_ROUTER_INTEGRATION` 冒烟）；第 10 章 WS 帧协议补 `routing` 字段与 `routing` status 阶段、capabilities 补 `auto_routing`
- `README.md`：配置说明中补 `mode_router` 节一句话说明
- `docs/MOC.md` 与根目录 README 导航表：本规格状态
- 本规格冻结时：按 docs-governance §2.4 分配编号、补冻结记录头；`AGENTS.md` 新增 frozen「必读」条目（改动 `src/core/chat/` 模式解析、`libs/mode_router/` 或 Chat 模式选择器前先读）；遗留事项建派生台账

## 8. 隐私注记

- **暴露面**：不新增 HTTP 路由；`GET /chat/capabilities` 只多一个布尔字段 `auto_routing`，不暴露 `base_url`、模型名或 token。
- **外发边界**：每个 auto 轮次把改写后的问题文本发给 `mode_router.base_url`。默认地址是本机回环（`127.0.0.1`）；若用户把它指向远程服务，问题文本会离开本机。不发送历史消息原文、检索结果或文档内容。
- **开关策略**：默认 `enabled: false`，不发任何请求；`api_token` 经 `${MODE_ROUTER_API_TOKEN}` 从环境变量解析，不落盘、不写入 trace。USAGE_GUIDE 写明远程端点的外发含义。

## 9. 风险与开放问题

| # | 风险 / 开放问题 | 处置 |
|---|----------------|------|
| R1 | 微调模型决策质量不达标（错选 deep 造成高成本、错选 hybrid 造成低质量） | MR0.2 立项门；门槛按 D2 与用户拍板；不达标即搁置，不改代码 |
| R2 | 决策延迟叠加每轮回答 | `timeout_s` 硬顶（默认 3，上限 10）+ 回退；`elapsed_ms` 入 trace |
| R3 | `auto` 语义扩散到其他链路 | 1.2 列出全部六处模式枚举；Out of Scope 明确 CLI、评估、Playground、MCP 不感知 |
| R4 | Auto 配色与 CMC 三色冲突或对比度不足 | MR4 验收含 light / dark 实测对比度；三档既有配色不动 |
| R5 | 决策端点成为新故障域 | 进程外托管 + 回退 + trace 可观测；默认关闭 |
| R6 | 路由选中 agentic 但 sidecar 未启动 | D3：与人工选中一致，不做探测；USAGE_GUIDE 写明，用户可开 `chat.fallback_to_chat_only` 或不启用路由器 |
