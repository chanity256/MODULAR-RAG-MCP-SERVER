<!-- 规格定位：为 Web UI Chat 新增 `auto` 检索模式，由进程外决策模型（首期 Cloudflare Clef，后续 TypeSafe Jev 与本地微调模型）在 hybrid / agentic_fast / agentic_deep 中定档，失败即回退。基线：S / SX / WC / CMC / AR 均已收口冻结。与既有规格的关系：ChatEngine 与 Chat 前端的扩展，不改三档既有模式的行为契约，不改 MCP 工具，不回改 CMC 冻结规格。 -->

# 检索模式路由器规格 (MODE_ROUTER_SPEC)

> **📌 冻结记录**
>
> - 冻结时间：2026-11-19
> - 完成概要：MR0.1–MR5 全部收口——MR0.2 立项门通过（`clef-flash` zh-v2 留出集命中率 93.3%，D2/D8 终裁）；MR1 `mode_router` 配置节 + `RETRIEVAL_AUTO` / `ModeRouter` 协议；MR2 `libs/mode_router/` 组件（System One 请求/解析 + `cloudflare_workers_ai` 后端 + 工厂，零新增依赖）；MR3 ChatEngine auto 分支（改写后定档 D5、`mode_routing` trace 阶段、失败回退、候选装配注入 D4）；MR4 API schema（`RequestedRetrieval`、capabilities `auto_routing`）与 Chat 前端（Auto 选项 violet 选中态对比度实测 ≥ AA、实际模式与回退原因展示）；MR5 假后端 Playwright auto 用例 + `RAG_MODE_ROUTER_INTEGRATION=1` 门控冒烟（真实端点三类典型问题 3/3 合法定档，单次决策 269–1316 ms；不可达 `base_url` 回退且 trace 记 `unreachable`）+ USAGE_GUIDE 第 11 章。全量禁网 2941 passed / 23 skipped / 1 xfailed，红线 0 违规，前端 typecheck / lint / vitest / Playwright 全绿。
> - 遗留：见 [backlog/mode-router-followups_backlog.md](../backlog/mode-router-followups_backlog.md)（#1 同一问题集合 auto 与人工三选一对比记表延后；#2 / #3 为 MR6 Jev、MR7 本地微调模型，凭据 / 端点到位后推进）

> 版本：0.3 — 由 backlog 提案立项为可执行规格；MR0.2 决策质量门控已通过（2026-11-18），MR1 起可进入编码
>
> v0.3（2026-11-18）更新依据：MR0.2 基线与留出集复核完成，D2 / D8 终裁回填 §1.3；默认 `model: clef-flash`、`min_confidence: null`；§3.6 请求示例改为选定的 zh-v2 文案
>
> v0.2（2026-11-18）更新依据：用户决定首期接入 Cloudflare 的 Clef 决策模型，TypeSafe 的 Jev 与本地微调模型后续接入（D6，MR6 / MR7）。Clef 与 Jev 共用 System One API（typed questions → 带概率与 confidence 的结构化答案），与 v0.1 假设的 chat completions 接口不同，据此改写 3.1–3.5 的后端设计与配置契约，追加 3.6（D7、D8），并修正 v0.1 中「本地小模型、零 token 费用」的表述与 §8 外发边界
>
> 起源与决策依据：由 backlog/mode-router_backlog.md 单文件晋升，原文见 git 历史；提案起源为会话提案（2026-11-16，用户需求：检索模式选择交由本地部署的微调决策模型自动完成，非文件来源）。依据 [06_WEBUI_CHAT_SPEC_frozen.md](../frozen/06_WEBUI_CHAT_SPEC_frozen.md)（Chat 引擎、检索器协议 D8、WS 帧协议）、[03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) S2.2（`agentic_search.trigger` 与独立校准集纪律）、[11_CHAT_MODE_COLORS_SPEC_frozen.md](../frozen/11_CHAT_MODE_COLORS_SPEC_frozen.md)（模式选择器配色规则）
>
> 基线：`a404207`（main），S / SX / WC / CMC / AR 系列均已收口冻结；`mode_router` 配置节与 `auto` 模式尚不存在
>
> 日期：2026-11-18

红线不变：主 venv 不 import 推理框架或厂商 SDK、不加载模型权重；决策模型只经 HTTP 调用（托管服务或独立进程）；`mode_router.enabled: false`（默认）时零加载，存量行为完全不变。

## 目录

- 1 项目概述（1.1 结论速览、1.2 背景与现状、1.3 决策依据与终裁、1.4 对提案的勘误）
- 2 核心目标与范围（2.1 In Scope、2.2 Out of Scope）
- 3 技术选型与模块设计（3.1 运行架构、3.2 模块设计、3.3 配置契约、3.4 涉及文件清单、3.5 选型理由、3.6 决策后端与 System One 请求契约（v0.2 追加））
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
| 定位是什么？ | ChatEngine 与 Chat 前端的扩展：新增 `auto` 请求值，检索前由决策模型定档（首期 Cloudflare Clef 托管服务，后续 TypeSafe Jev 与本地微调模型），之后走既有三档检索路径（零改动）；不替换既有模式 |
| 实施范围（已决策） | MR0.2（Clef 基线）为立项门（决策质量不达标即搁置）；MR1–MR4 为承诺任务（门控通过后）；MR5 含门控真实冒烟；MR6（Jev）与 MR7（本地微调模型）为后续接入任务，凭据或端点到位后排期（D6）。MCP 侧不做（D1） |
| 主要代价 | 每轮多一次决策调用（硬顶 `timeout_s`）；首期 Clef 是外部托管服务，改写后的问题文本会发往 Cloudflare，并按输入 token 计费（§8、3.6）；新增一个可选外部依赖与配置节；模式常量多处登记需同步扩展 |

### 1.2 背景与现状

系统现有三种检索模式：

| 模式 | 延迟量级 | 能力边界 |
|------|---------|---------|
| `hybrid`（Dense + BM25 + RRF） | 秒级 | 主库内语义 + 关键词检索，依赖已摄取语料 |
| `agentic_fast`（Sirchmunk FAST） | 十秒级 | 可越出主库扫描白名单目录，单轮探索 |
| `agentic_deep`（Sirchmunk DEEP） | 数十秒级 | 多轮迭代探索 + 目录扫描，成本最高 |

当前用户要么每轮在 Web UI 手选，要么把 `chat.default_retrieval` 固定为一档。问题复杂度、是否可能超出已摄取语料、对延迟的容忍度，这三类信息在提问时就能从问题文本推断，属于决策模型（System One 类模型，见 3.6）的典型任务：从预先定义的选项中选一个，并给出每个选项的概率。

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
   - 主 LLM 兼任决策：每轮多一次主 LLM 调用（延迟与输出 token 费用），输出需要解析，且与主 LLM 共用故障域。决策模型只返回候选内的结构化答案；厂商公布的中位延迟为 Clef 209 ms、Clef-flash 39 ms、Jev 524 ms，只按输入 token 计费（Clef $0.24 / M、Clef-flash $0.038 / M），见 3.6。
   - 交叉编码器分数触发第三路（`agentic_search.trigger: fallback`，S2.2）：在检索**之后**补触发，解决「信号不足时补救」；本规格在检索**之前**定模式，解决「用户免选」。两者正交，可共存。
3. **如何确认有效**：
   - 决策质量：独立标注集（问题 → 期望模式）上的命中率与混淆矩阵（MR0.2）；标注集与 golden test set 分离，不用评估 golden set 调参（S2.2 独立校准集先例）。
   - 端到端：同一问题集合上，`auto` 与人工三选一的「回答质量 × 平均延迟 × token 成本」对比（MR5）。
   - 降级安全：路由器失败/超时的用例回退 `fallback_retrieval` 后，回答质量不劣于固定 hybrid 基线（MR3 离线 + MR5 冒烟）。

| 编号 | 决策 | 终裁 |
|------|------|------|
| D1 | MCP `query_knowledge_hub` 是否新增 `retrieval` / `auto` 参数（原提案 G7 后半与 MR5） | **不做**（2026-11-18，用户拍板）。MCP 调用方是外部 LLM，它已经通过选择 `query_knowledge_hub` 或 `agentic_search` 工具来选检索方式；让 hybrid 工具转派 agentic 会改变 MCP 工具契约与描述。后续有需求另立提案 |
| D2 | MR0.2 决策质量门槛 | **本规格只固定度量口径，数值门槛在 MR0.2 基线跑出后与用户共同拍板**（2026-11-18，用户拍板）。门槛终裁前 MR1 不开工；终裁结果回填本表与 MR0.2 行。v0.2 补：`min_confidence` 阈值（D8）与默认 `model` 和门槛一同拍板；Jev（MR6）与本地模型（MR7）沿用同一口径与同一标注集。**终裁（2026-11-18，用户拍板）**：门槛 = 默认配置在留出集（`holdout_set.jsonl`）上命中率 ≥ 90%、deep 误选率 ≤ 10%、非法输出率 0；默认 `model: clef-flash`、criteria 用 zh-v2。实测 93.3% / 5% / 0，通过。门槛在看到结果后设定，后续模型（MR6 / MR7）与文案改动按同一门槛复核，标注集与留出集同时跑 |
| D3 | sidecar 已配置但未启动时 auto 如何处理 | **不做存活探测**（2026-11-18，用户拍板）。候选集只看配置与 `allow_deep`；路由选中 agentic 模式但 sidecar 不可用时，行为与人工选中该模式完全一致（默认 error 帧，`chat.fallback_to_chat_only: true` 时降级纯聊天），规格与 USAGE_GUIDE 写明 |
| D4 | 候选集来源 | **由装配层计算后注入引擎**（规格设计决策，依据 1.4 勘误 1）：候选 = 已注册检索器 ∩ 门控可用（`allow_deep=false` 时剔除 `agentic_deep`），与 `compute_capabilities` 同一口径；引擎不直接取 `self._retrievers` 的 keys |
| D5 | 决策时机与输入 | **在 `_run` 中查询改写之后决策，输入改写后的 `search_query`**（规格设计决策，依据 1.4 勘误 3）：多轮追问（如「那第二点呢」）用改写后的独立问题判断复杂度；决策期间下发 `status` 帧，`run_turn` 的同步校验不引入网络调用 |
| D6 | 决策模型接入顺序 | **首期接入 Cloudflare Clef（Workers AI 托管），TypeSafe Jev 与本地微调模型后续接入**（2026-11-18，用户拍板）。MR0.2 的基线与 MR2 的实现只针对 Clef；Jev 为 MR6，本地微调模型为 MR7，均在用户提供凭据或端点后排期。本规格冻结时若二者仍未到位，移入派生台账，不阻塞冻结 |
| D7 | 决策后端接口 | **以 System One API 为主契约**（规格设计决策，依据 3.6）：一次请求只问一个 `choice` 问题，`criteria` 由当前候选动态生成；Clef 与 Jev 只在端点、鉴权、响应外层包装上不同，共用一份请求构造与答案解析。chat completions 形态（`openai_compatible`）只服务 MR7 的本地模型；若本地模型也提供 System One 接口，则直接复用 System One 后端 |
| D8 | 低置信度处理 | **`choice` 答案的 `confidence` 低于 `mode_router.min_confidence` 时回退 `fallback_retrieval`，`fallback_reason = low_confidence`**（规格设计决策）。默认 `null`（不启用）；阈值依据 MR0.2 的置信度分桶命中率，与 D2 一同拍板。`openai_compatible` 后端没有 confidence，该配置必须为 `null`。**终裁（2026-11-18，用户拍板）**：默认 `null`。留出集上 0.3 使 clef-flash zh-v2 命中率由 93.3% 降到 86.7%，置信度不足以作为回退依据；配置项保留，换模型或文案后可按同口径重测 |

### 1.4 对提案的勘误

1. **门控候选集（原 G4）不成立**：提案称「候选集直接取 `self._retrievers` 的 keys，`allow_deep=false` 时 deep 不进候选」。实际 deep 在 agentic 启用时总被注册（1.2 第 3 条），按原方案路由器可能选中被禁用的 deep，得到不降级的 `DEEP_DISABLED` 错误。改为 D4。
2. **sidecar 未启动时不进候选不成立**：检索器按配置注册，与 sidecar 存活无关。改为 D3 的明确口径。
3. **插入点**：提案写的 `ChatEngine.chat()` 不存在，实际入口是 `run_turn`（同步校验）+ `_run`（生成器）。改为 D5。
4. **模式常量引用点**：提案只列了 `RETRIEVAL_MODES` 与 `RetrievalMode`，实际有六处（1.2 第 5 条）；`capabilities.default_retrieval` 用 `RetrievalMode` 类型，`auto` 作默认值时会被 schema 拒绝，前端 `isRetrievalMode` 会把它静默改成 hybrid。处置见 3.2。
5. **MCP 侧**：`query_knowledge_hub` 只做 hybrid，不经 ChatEngine，加参数等于让它转派 agentic 工具。按 D1 移出范围；原 MR5（MCP）删除，原 MR6 顺延为 MR5。
6. **装配位置**：提案写「`services/` 内新增 `build_mode_router`」。路由器不依赖 Pipeline 或存储，直接由 `src/api/chat_wiring.py` 经 `libs/mode_router` 工厂懒建即可（与其懒建 `LLMFactory` 同构），不新增 services 文件。
7. **backend 枚举**：Ollama 自带 OpenAI 兼容端点（`/v1/chat/completions`），单一 `openai_compatible` 实现即可覆盖 vLLM 与 Ollama；不单列 `ollama` backend。（v0.2：`backend` 枚举改为按决策服务划分，见 3.3 与 3.6；本条结论在 MR7 的 `openai_compatible` 后端内仍成立。）
8. **出处**：提案的「DEV_SPEC 9.5 纪律」在主规格中不存在，独立校准集纪律的先例是 SIRCHMUNK S2.2；`trigger: fallback` 属 S 系列 S2.2，不属 SX 系列。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| 预研 | 决策质量基线 | 门控（MR0.2，立项门） | 独立标注集 + Clef（`clef` 与 `clef-flash`）的命中率 / 混淆矩阵 / 置信度分桶 / 延迟基线；门槛按 D2 终裁，不达标即搁置 |
| 配置 | `mode_router` 配置节与常量 | 承诺（MR1） | `ModeRouterSettings` + fail-fast 校验；`chat.default_retrieval` 允许 `auto`（需路由器启用）；`RETRIEVAL_AUTO` 常量与 `ModeRouter` 协议 |
| 组件 | `libs/mode_router/` | 承诺（MR2） | `BaseModeRouter` + System One 请求构造与解析 + `cloudflare_workers_ai` 后端（Clef）+ 工厂；`enabled: false` 时工厂返回 `None`、零加载 |
| 引擎 | ChatEngine auto 分支 | 承诺（MR3） | 改写后决策 → 候选内校验 → 失败回退 `fallback_retrieval`；决策与降级原因入 trace 与 `done` 帧 |
| API + 前端 | capabilities 与 Chat UI | 承诺（MR4） | schema 区分「请求模式」与「实际模式」；capabilities 上报 `auto_routing`；ModeSelector 新增 Auto 选项与配色 |
| 验证与文档 | e2e、门控冒烟、USAGE_GUIDE | 承诺（MR5，其中真实冒烟门控） | 假后端 e2e 离线覆盖 auto 帧链路；`RAG_MODE_ROUTER_INTEGRATION=1` 真实端点冒烟；使用文档收口 |
| 后续接入 | Jev（`typesafe` 后端） | 后续（MR6，TypeSafe API key 到位后排期） | 复用 MR2 的 System One 构造与解析，只加端点与鉴权；同一标注集跑基线，与 Clef 并表 |
| 后续接入 | 本地微调模型 | 后续（MR7，端点与输出格式到位后排期） | vLLM / Ollama 的 OpenAI 兼容端点（`openai_compatible` 后端）；若本地模型提供 System One 接口，改为复用 MR2 后端加 `base_url` 覆盖 |

### 2.2 Out of Scope（明确不做）

- **决策模型的训练/微调**：模型由外部带入，本规格只管已部署模型的接入、调用与降级。
- **MCP 侧 auto**（D1）：`query_knowledge_hub` 与 `agentic_search` 的输入 schema、描述与行为不变。
- **Query Playground 的 auto**：Playground 是调试各档模式的工具，显式选型即其语义。
- **`scripts/query.py`、`scripts/evaluate.py`、`EvaluationService` 感知 auto**：CLI 与评估链路不经 ChatEngine，保持显式模式。
- **sidecar 存活探测**（D3）：不在候选计算或每轮决策前探测 sidecar。
- **决策后二次升级**（如 hybrid 结果弱再自动重跑 deep）：属 `agentic_search.trigger` 的职责范围，本规格不在引擎内新增重试分支。
- **决策结果缓存**：单次决策百毫秒级，费用按输入 token 计且很低，缓存收益不成比例；后续观察再议。
- **同时调用多个决策模型或投票**：同一时刻只配置一个后端；Clef / Jev / 本地模型的对比只在 MR0.2 / MR6 / MR7 的离线评估中做。
- **Clef 的图片输入与 Cloudflare 的 RL 微调服务**：不使用。AI Gateway 只作为可选的 `base_url` 覆盖写入 USAGE_GUIDE，不做专门适配。
- **厂商 SDK**（如 `@typesafe-ai/sdk`）：不引入，直接用 httpx 调 REST。
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
   │     └─ 超时 / 不可达 / 非法输出 / 低置信度（D8）/ 异常 → mode = fallback_retrieval，记 fallback_reason
   │  trace 记 mode_routing 阶段；state.retrieval = mode
   ▼
既有 _retrieve(self._retrievers[mode], ...) → 回答 → done { retrieval: mode, routing: {...} }

装配（src/api/chat_wiring.py，懒建）：
   create_mode_router(settings)  ── enabled=false → None（零加载）
   candidates = 已注册检索器 ∩ 门控可用（allow_deep=false 剔除 agentic_deep）
   ChatEngine(..., mode_router=router, auto_candidates=candidates, auto_fallback=fallback_retrieval)

决策模型（进程外，按 mode_router.backend 选一个，见 3.6）：
   cloudflare_workers_ai（Clef，MR2）  POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/{model}
   typesafe（Jev，MR6）               POST https://api.typesafe.ai/v1/systemone
      body: { model, state: search_query, questions: { retrieval_mode: { type: "choice", instructions, criteria: {候选 → 描述} } } }
      答案: answers.retrieval_mode.{ choice, probabilities, confidence }
   openai_compatible（本地微调，MR7）  POST {base_url}/chat/completions  { model, messages, temperature: 0, max_tokens: 8 }
```

### 3.2 模块设计

- **`src/core/chat/retriever.py`**（MR1）：新增 `RETRIEVAL_AUTO = "auto"`（不进 `RETRIEVAL_MODES`）、`RoutingDecision` 数据类（`mode`、`confidence`、`probabilities`，后两者可为 `None`）与 `ModeRouter` Protocol：`decide(query: str, candidates: tuple[str, ...]) -> RoutingDecision`。core/chat 保持零实现依赖，libs 实现引用这里的类型。
- **`src/libs/mode_router/`**（MR2）：
  - `base_mode_router.py`：`BaseModeRouter` 抽象 + `ModeRouterError`（带 `reason` 字段：`timeout` / `unreachable` / `invalid_output` / `low_confidence` / `error`）。
  - `routing_question.py`（MR2）：三档模式的 `instructions` 与 `criteria` 描述文案，按候选动态生成 `choice` 问题；评估脚本与各后端共用这一份（单一来源）。
  - `system_one_router.py`（MR2）：httpx 同步客户端，`timeout=timeout_s`；构造 System One 请求、解析 `answers.retrieval_mode`；`choice` 必须属于候选，否则抛 `invalid_output`；`confidence < min_confidence` 时抛 `low_confidence`。端点、鉴权与响应外层包装由后端决定：`cloudflare_workers_ai`（MR2）按 `account_id` 与 `model` 拼 URL，解开 Workers AI 的 `{result, success, errors}` 包装，`success: false` 记 `error`；`typesafe`（MR6）直连 `https://api.typesafe.ai/v1/systemone`，响应无包装。
  - `openai_compatible_router.py`（MR7）：system prompt 列出候选标签、要求只输出一个标签；输出 `strip().lower()` 后必须精确等于某个候选，否则抛 `invalid_output`；无 confidence。
  - `mode_router_factory.py`：`create_mode_router(settings) -> BaseModeRouter | None`，按 `backend` 分派。
- **`ChatEngine`**（MR3）：构造参数新增 `mode_router`、`auto_candidates`、`auto_fallback`（均可选）。构造时校验 `auto_fallback ∈ auto_candidates`。`run_turn` 中 `auto` 仅在 router 注入时合法；`_run` 中按 D5 决策，引擎对决策结果再做一次候选内校验（不信任实现）。任何异常都回退，不中断会话。
- **trace 与帧**（MR3，只增不改）：
  - trace 新增 `mode_routing` 阶段：`requested="auto"`、`decided`、`candidates`、`backend`、`model`、`confidence` 与 `probabilities`（后端不提供时为 `null`）、`fallback_reason`（成功时为 `null`）、`elapsed_ms`、输入 token 数（后端返回 `usage` 时）；不记 `api_token` 与 `account_id`；trace 元数据 `retrieval` 记**实际使用的模式**。
  - `done` 帧 `retrieval` 仍为实际模式，新增可选 `routing` 对象（同上字段）；非 auto 轮次不出现该字段。
  - 会话 `settings.retrieval` 记用户请求值（`auto`），以便恢复会话时保留选择。
- **API schema**（MR4）：保留 `RetrievalMode`（三档真实模式）供 `capabilities.retrievals` 使用；新增 `RequestedRetrieval = RetrievalMode | Literal["auto"]` 用于 `user_message.retrieval` 与 `capabilities.default_retrieval`；capabilities 新增 `auto_routing: bool`（路由器启用且候选非空）。`retrievals` 列表不含 `auto`。
- **前端**（MR4）：`types.ts` 增加请求模式类型；`normalize.ts` 允许 `default_retrieval = "auto"`（仅当 `auto_routing` 为 true，否则仍归一为 hybrid）；`modes.ts` 在 `auto_routing` 为 true 时提供 Auto 选项，`resolveMode` 同步处理；ModeSelector 为 Auto 增加第四种选中态配色，延续 CMC 规则（light 浅底深字 / dark 半透明底亮字，对比度 ≥ WCAG AA），三档既有配色不变；助手消息展示实际模式（如「Auto → Hybrid」），回退时提示回退原因。

### 3.3 配置契约

`config/settings.yaml` 新增可选节（缺失即 `enabled: false`），风格对齐 `agentic_search` 节：

```yaml
mode_router:
  enabled: false                 # true 才提供 auto 模式；false 时零加载
  backend: cloudflare_workers_ai # cloudflare_workers_ai（Clef，MR2）| typesafe（Jev，MR6）| openai_compatible（本地微调，MR7）
  model: clef-flash              # Workers AI：clef | clef-flash（D2 终裁默认 clef-flash）；TypeSafe：固定版本号，不用会漂移的 jev-latest；本地：部署名
  account_id: ${CLOUDFLARE_ACCOUNT_ID}  # 仅 cloudflare_workers_ai；从环境变量解析
  base_url: null                 # 可选覆盖（AI Gateway 或本地端点）；cloudflare_workers_ai 缺省按 account_id 拼接，typesafe 缺省 https://api.typesafe.ai/v1
  api_token: ${MODE_ROUTER_API_TOKEN}   # Workers AI API token / TypeSafe API key；从环境变量解析，不落盘
  timeout_s: 3                   # 决策发生在每轮检索之前，超时即回退；MR0.2 实测 clef-flash 最大约 1.0 s
  min_confidence: null           # D8：低于该值回退；null 不启用（D8 终裁默认 null）
  fallback_retrieval: hybrid     # 决策失败 / 超时 / 输出非法 / 低置信度时的兜底模式
```

`account_id` 单独成字段，是因为 `_resolve_env` 只解析整串的 `${VAR}`（`src/core/settings.py:49`），无法在 `base_url` 中嵌入环境变量。

校验（fail-fast，`ConfigError`）：

- `enabled` 布尔；`model` 非空；`timeout_s` 为正数且不超过 10；`base_url` 若给出须为 http(s) URL。
- `backend` 取值随任务开放：MR1 只接受 `cloudflare_workers_ai`，`typesafe` 在 MR6、`openai_compatible` 在 MR7 交付时加入；尚未交付的取值 fail-fast，并在报错中指出对应任务。
- `cloudflare_workers_ai` 要求 `model ∈ {clef, clef-flash}`、`account_id` 非空；两个托管后端要求 `api_token` 非空。仍是未解析 `${...}` 占位符的值视为缺失。
- `min_confidence` 为 `null` 或 (0, 1) 内的数；`backend: openai_compatible` 时必须为 `null`。
- `fallback_retrieval ∈ CHAT_RETRIEVAL_MODES`；取 agentic 模式时要求 `agentic_search.enabled: true`，取 `agentic_deep` 时另要求 `allow_deep: true`。
- `chat.default_retrieval: auto` 仅当 `mode_router.enabled: true` 时合法，否则启动即报错。

接入示例（写入 USAGE_GUIDE）：

```bash
# 首期：Clef（Cloudflare Workers AI；控制台创建 Workers AI API token 并复制 Account ID）
export CLOUDFLARE_ACCOUNT_ID=<account id>
export MODE_ROUTER_API_TOKEN=<workers ai api token>
# 后续：Jev（MR6）改 backend: typesafe、model 为固定版本号，token 换为 TypeSafe API key
# 后续：本地微调模型（MR7）
vllm serve /path/to/your-finetuned-model --host 127.0.0.1 --port 8590   # base_url: http://127.0.0.1:8590/v1
ollama create mode-router -f ./Modelfile && ollama serve                # base_url: http://127.0.0.1:11434/v1
```

### 3.4 涉及文件清单

新增：

| 文件 | 职责 | 任务 |
|------|------|------|
| `scripts/eval_mode_router.py` | 决策质量评估脚本：读标注集、调真实决策服务、输出命中率 / 混淆矩阵 / 置信度分桶 / 延迟分位 / 非法输出率；真实运行，不进 `pytest tests`。MR0.2 时请求构造与问题文案写在脚本内（门控通过前不改 `src/`）；MR2 把文案移入 `routing_question.py`，脚本改为调用 `libs/mode_router`，此后脚本与运行时共用一份；MR6 / MR7 复用 | MR0.2 / MR2 |
| `tests/fixtures/mode_router/labeled_set.jsonl`、`holdout_set.jsonl` | 独立标注集与留出集（问题 → 期望模式），与 `golden_test_set.json` 分离；留出集用于 D2 门槛复核 | MR0.2 |
| `src/libs/mode_router/__init__.py`、`base_mode_router.py`、`routing_question.py`、`system_one_router.py`、`mode_router_factory.py` | 3.2 所述组件（首期 `cloudflare_workers_ai` 后端） | MR2 |
| `src/libs/mode_router/openai_compatible_router.py` | 本地微调模型后端 | MR7 |
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
| `src/core/settings.py`、`src/libs/mode_router/system_one_router.py`、`mode_router_factory.py` | 开放 `typesafe` 后端 | MR6 |
| `src/core/settings.py`、`src/libs/mode_router/mode_router_factory.py` | 开放 `openai_compatible` 后端 | MR7 |

### 3.5 选型理由

1. **进程外 HTTP 端点 vs 主 venv 内加载模型**：与 Sirchmunk sidecar 同一隔离原则；主 venv 不加推理依赖，端点故障只降级不拖垮主服务；httpx 已在依赖中，`requirements.txt` / `pyproject.toml` 依赖区不动。
2. **`auto` 独立常量 vs 并入 `RETRIEVAL_MODES`**：`RETRIEVAL_MODES` 同时用于引擎校验与 capabilities 过滤，混入 `auto` 会让「不存在名为 auto 的检索器」的校验失效，并污染 `capabilities.retrievals`。
3. **装配层注入候选 vs 引擎取 keys**：见 D4 与 1.4 勘误 1；与 `compute_capabilities` 同一口径，前端看到的可用模式与路由器的候选一致。
4. **改写后决策 vs `run_turn` 中决策**：`run_turn` 是同步校验点，放入网络调用会在推流前阻塞最多 `timeout_s`，且拿不到改写后的查询。
5. **单一 `openai_compatible` 实现 vs 每种部署一个后端**：Ollama 与 vLLM 都提供 OpenAI 兼容端点，一个实现覆盖两者，少一个配置分支。（v0.2：只适用于 MR7 的本地后端。）
6. **System One `choice` 问题 vs chat completions 生成标签**：答案在类型上限定为候选之一，附带每档概率与 confidence，可直接做低置信度回退（D8）和校准分析；不计输出 token。引擎仍做候选内校验，不因厂商声明「不会出类型错误」而省掉。
7. **首期托管的 Clef vs 先做本地模型**：用户决定（D6）。Clef 无需自备 GPU 与微调就能跑出 MR0.2 基线，先回答「决策模型能否胜任三档路由」；Jev 与本地模型随后用同一标注集横向对比。Clef 权重以 Apache 2.0 开源，日后本地部署也有路径。

### 3.6 决策后端与 System One 请求契约（v0.2 追加）

| 后端 | 服务 | 端点 | 鉴权 | 响应 | 任务 |
|------|------|------|------|------|------|
| `cloudflare_workers_ai` | Cloudflare Clef（`@cf/cloudflare/clef`，27B，上下文 64K）/ Clef-flash（`@cf/cloudflare/clef-flash`，9B，上下文 24K） | `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/{model}` | `Authorization: Bearer <Workers AI API token>` | Workers AI REST 外层 `{result, success, errors, messages}`，`result` 内为 System One 答案 | MR2 |
| `typesafe` | TypeSafe Jev（early access） | `https://api.typesafe.ai/v1/systemone` | `Authorization: Bearer <TypeSafe API key>` | System One 答案，无外层包装 | MR6 |
| `openai_compatible` | 本地微调模型（vLLM / Ollama） | `{base_url}/chat/completions` | 可选 Bearer | chat completions 文本，需解析 | MR7 |

请求体（Clef 与 Jev 相同；`criteria` 只含当前候选；下例为 MR0.2 选定的 zh-v2 文案，MR2 起固定在 `routing_question.py`，原 v1 文案只讲「是否已摄取」，基线命中率 46.7–63.3%，已弃用）：

```json
{
  "model": "clef-flash",
  "state": "<改写后的 search_query>",
  "questions": {
    "retrieval_mode": {
      "type": "choice",
      "instructions": "应由哪种检索模式回答这个问题？",
      "criteria": {
        "hybrid": "解释一个概念或事实，一两段内容即可回答，包括同一处讲到的简单对比，例如 X 是什么、为什么、X 和 Y 有何区别；最快",
        "agentic_fast": "需要逐字扫描本地文件：哪个文件或章节提到某个词、列出所有出现位置、从指定文件原样摘录原文或链接、列出某文件的标题，或查找新上传、尚未索引的文件",
        "agentic_deep": "需要阅读并综合多个文件：对比分散在不同文档里的对象、汇总全部记录，或基于多个章节写综述、计划或论证；最慢、成本最高"
      }
    }
  }
}
```

答案取 `answers.retrieval_mode`，形如 `{"type": "choice", "choice": "hybrid", "probabilities": {"hybrid": 0.81, "agentic_fast": 0.15, "agentic_deep": 0.04}, "confidence": 0.74}`（数值为示意）。

已知事实与约束（来源：[Clef 模型页](https://developers.cloudflare.com/workers-ai/models/clef/)、[Clef 发布说明](https://developers.cloudflare.com/changelog/post/2026-10-01-clef-workers-ai/)、[TypeSafe 快速开始](https://docs.typesafe.ai/introduction/quickstart)，2026-11-18 查阅）：

- Clef 遵循 System One API，Cloudflare 称从 Jev 切换只需换端点与模型名。MR6 依赖这一点，但以实测为准。
- 计费只算输入 token：Clef $0.24 / M，Clef-flash $0.038 / M。一次路由请求只含问题与三条描述，量级为数百 token。
- 厂商在自己的基准环境中公布的延迟：中位数 Clef 209 ms、Clef-flash 39 ms、Jev 524 ms。本机到服务端的网络往返另计；MR0.2 本机复用连接实测 clef-flash p95 约 0.44–0.52 s、最大约 1.0 s，`timeout_s` 默认 3 保持不变。
- Clef 模型页标注 Zero data retention；TypeSafe 服务当前部署在美国西海岸。
- 模型页没有写明 Clef 的 REST 响应是否也带 Workers AI 通用的 `result` 外层包装（通用 REST 文档是带的）。解析同时接受有包装与无包装两种形态。2026-11-18 首次真实调用已确认 Clef 带包装（见 MR0.2 行）。
- `choice` 没有内置「都不合适」选项。三档模式已覆盖全部情况，不加 other 项。
- 用户问题多为中文。MR0.2 实测中英文 criteria 表现相当（留出集 clef-flash 均为 93.3%），选用中文 zh-v2。


## 4. 测试方案

- **离线确定性测试**（零网络，`httpx.MockTransport` 与 fake router 注入）：
  - 配置（MR1）：缺省节 → `enabled=false`；各字段非法值 fail-fast；`backend` 与 `model` / `account_id` / `api_token` / `min_confidence` 的交叉校验，未解析的 `${...}` 占位符视为缺失；未交付的 `backend` 取值报错；`fallback_retrieval` 与 agentic / `allow_deep` 的交叉校验；`default_retrieval: auto` 在路由器未启用时报错、启用时通过。
  - 组件（MR2）：MockTransport 返回 Workers AI 包装的合法 `choice` → 返回该模式及 confidence / probabilities；无包装形态同样能解析；`success: false` 或非 2xx → `error`；`choice` 不在候选内、缺 `answers.retrieval_mode`、类型不是 `choice` → `invalid_output`；`confidence < min_confidence` → `low_confidence`，`min_confidence: null` 时不判；超时 → `timeout`；连接失败 → `unreachable`；请求 URL 按 `account_id` 与 `model` 拼接（有 `base_url` 覆盖时用覆盖值）；请求体 `state` 为传入查询、`criteria` 的键恰为候选集；带 `Authorization: Bearer`；工厂 `enabled=false` 返回 `None`。
  - 组件（MR6 / MR7）：`typesafe` 后端的 URL 与无包装响应；`openai_compatible` 后端的标签解析（大小写与空白归一；候选外、空输出、多标签 → `invalid_output`），请求体含 `temperature: 0` 与 `max_tokens: 8`。
  - 引擎（MR3）：fake router 选中各模式 → 走对应检索器；router 抛各类错误 / 返回候选外值 → 回退 `fallback_retrieval`，`fallback_reason` 正确；决策使用改写后的查询；决策前下发 `routing` status 帧；trace `mode_routing` 阶段字段完整（含 confidence / probabilities，不含 token 与 account ID）、元数据 `retrieval` 为实际模式；`done.routing` 只在 auto 轮次出现；未注入 router 时 `auto` → `ValueError`；`auto_fallback ∉ auto_candidates` 构造即报错。
  - 装配（MR3/MR4）：`allow_deep=false` 时候选不含 `agentic_deep`；agentic 关闭时候选只有 hybrid；capabilities `auto_routing` 随启用状态变化、`retrievals` 不含 `auto`。
  - 前端（MR4，vitest）：`auto_routing` 为 true 时出现 Auto 选项、false 时不出现；`normalize` 对 `default_retrieval: "auto"` 的两种分支；`resolveMode` 选择逻辑；ModeSelector Auto 选中态 class 与对比度断言，既有三档用例不变。
  - e2e（MR5）：`tests/e2e_web` 假后端 + Playwright 用例：选 Auto 发问 → 收到 `routing` status 帧与带 `routing` 的 `done` 帧 → 消息展示实际模式。
- **真实依赖冒烟**：`RAG_MODE_ROUTER_INTEGRATION=1` 门控，首期需 `CLOUDFLARE_ACCOUNT_ID` 与 `MODE_ROUTER_API_TOKEN`（MR6 / MR7 换为对应凭据或端点），默认跳过（MR5）。决策质量评估（`scripts/eval_mode_router.py`）真实运行、不进 `pytest tests`、不阻塞 CI（MR0.2）。
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
| MR0.2 | 决策质量预研（立项门，Clef） | 前置：用户提供 Cloudflare Account ID 与 Workers AI API token；标注集由用户提供，或由 agent 起草、用户逐条确认（每档模式均有覆盖，与 golden set 分离）。`scripts/eval_mode_router.py` 对 `clef` 与 `clef-flash` 各跑一遍，输出总体命中率、三档混淆矩阵、deep 误选率（期望非 deep 却选 deep）、hybrid 误降率（期望非 hybrid 却选 hybrid）、非法输出率、置信度分桶命中率及不同 `min_confidence` 下的覆盖率与命中率、本机实测决策延迟 p50 / p95、平均输入 token；对比中英文 criteria 文案；确认 Workers AI 响应包装形态。数值门槛、默认 `model` 与 `min_confidence` 按 D2 / D8 与用户拍板并回填 §1.3；未达标即本规格搁置，不改 `src/` | 已完成（2026-11-18，门控通过）。响应带 Workers AI `{result, success, errors, messages}` 包装，`usage.output_tokens` 为 0。交付 `scripts/eval_mode_router.py`（离线单测 `tests/unit/test_eval_mode_router_script.py`）、标注集 `tests/fixtures/mode_router/labeled_set.jsonl`（60 条，每档 20，中 16 / 英 4，取材于 `data/documents/` 下的学习笔记，用户确认）与留出集 `holdout_set.jsonl`（30 条，每档 10，不含「文件 / 搜 / 原文 / 对比 / 综合」等提示词，用户确认，跑前未见结果）。全部运行无非法输出。**v1 文案**（只讲「是否已摄取」）标注集命中率 46.7–63.3%，fast / deep 大量误判为 hybrid（hybrid 误降率 55–78%）。**v2 文案**（按问题形态描述三档，见 §3.6）标注集 95.0–98.3%；留出集 clef 96.7%（zh / en），clef-flash 93.3%（zh / en），deep 误选率 0–5%。`min_confidence` 0.3 在标注集上把命中率提到 98.3–100%，在留出集上却降到 83.3–90.0%（被丢弃的低置信度决策中正确的 agentic 选择多于错选），两集合计 clef-flash zh-v2 不设阈值 95.6%、0.3 为 94.4%，故不启用。复用连接延迟（p50 / p95）clef-flash 约 0.35–0.38 s / 0.44–0.52 s、全部运行最大约 1.0 s；clef 约 0.63–0.80 s / 1.5–1.7 s、偶发 5 s 与每轮首次约 6 s。v2 输入约 270–286 token。终裁见 D2 / D8 |

| MR1 | 配置节 + 常量与协议 | §4 配置组用例通过；`RETRIEVAL_MODES` / `CHAT_RETRIEVAL_MODES` 内容不变；`config/settings.yaml` 示例 `enabled: false` | 已完成（2026-11-18）。`ModeRouterSettings` + `_build_mode_router`（含 `${...}` 未解析占位符视为缺失、enabled 时才要求凭据、未交付 backend 报错并指出 MR6 / MR7）；跨节校验入 `validate_settings`（fallback 需 agentic enabled / allow_deep、`default_retrieval: auto` 需路由器启用）；`retriever.py` 新增 `RETRIEVAL_AUTO` / `RoutingDecision` / `ModeRouter` Protocol。离线单测 `tests/unit/test_mode_router_config.py`（配置组 32 例）；全量禁网 2888 passed，红线 0 违规。计划存档 `.agents/plans/MR1.md` |
| MR2 | `libs/mode_router/` 组件 + 工厂 | §4 组件组用例通过；红线脚本 0 违规；主 venv 未新增依赖 | 已完成（2026-11-18）。`routing_question.py`（zh-v2/en-v2 文案单一来源，v1 历史变体不进运行时库）、`base_mode_router.py`（`ModeRouterError` 五型失败分型 + 候选内校验 + D8 置信度回退共用）、`system_one_router.py`（System One 请求构造 / 解析，兼容 Workers AI 包装与无包装 + `CloudflareWorkersAIRouter`，实例级持久连接懒建 + `close()`，transport 可注入）、`mode_router_factory.py`（`enabled=false → None`）；`scripts/eval_mode_router.py` 文案改从 `routing_question` 导入（3.4 单一来源）。离线单测 `tests/unit/test_mode_router.py` 22 例；全量禁网 2916 passed，红线 0 违规，主 venv 零新增依赖。计划存档 `.agents/plans/MR2.md`；一处测试断言笔误经用户确认后修正（SD3.7） |
| MR3 | ChatEngine auto 分支 + 装配注入 | §4 引擎组与装配组用例通过；既有 Chat 单测与 WS 集成测试不改即通过 | 已完成（2026-11-18）。`ChatEngine` 新增 `mode_router` / `auto_candidates` / `auto_fallback` 构造参数（`auto_fallback ∉ auto_candidates` 构造即报错）；`run_turn` 接受 `auto`（未注入 router 即 `ValueError`）；`_run` 改写后定档（D5）：`status(routing)` 帧 → `mode_routing` trace 阶段（requested / decided / candidates / backend / model / confidence / probabilities / fallback_reason / elapsed_ms / input_tokens，不含 token 与 Account ID）→ 候选内复检 + 失败回退 `fallback_retrieval`（`ModeRouterError.reason` 分型，其余异常记 `error`）；trace metadata 与 result 帧记实际模式，result 帧只增 `retrieval`（全部轮次）与 `routing`（仅 auto 轮次）。`ChatServices` 懒建共享 router（跨轮复用连接）+ `_auto_candidates`（D4：已注册 ∩ 门控）。离线单测 `tests/unit/test_chat_auto_mode.py` 19 例；全量禁网 2935 passed（既有 Chat 单测与 `test_api_chat.py` 未改即通过），红线 0 违规。计划存档 `.agents/plans/MR3.md`；一处新测试笔误（漏传 `retrieval="auto"`）经用户确认后修正（SD3.7） |
| MR4 | API schema、capabilities 与 Chat 前端 | §4 装配组 capabilities 断言与前端 vitest 通过；`npm run typecheck && npm run lint && npm test` 全绿；Auto 选中态对比度 ≥ WCAG AA（light / dark 实测记入本表） | 已完成（2026-11-19）。后端：`api/schemas/chat.py` 新增 `RequestedRetrieval`（`user_message.retrieval` 与 `capabilities.default_retrieval` 放宽，`retrievals` 仍为 `RetrievalMode` 三档）、capabilities 新增 `auto_routing`；`compute_capabilities` 按「路由器启用且候选非空」上报（候选 = available ∩ 门控，与 `ChatServices._auto_candidates` 同口径）。前端：`types.ts` 增 `RequestedRetrieval` / `RoutingInfo` / `ChatStage` 增 `routing` / result 帧与 `ChatMessage` 增 `retrieval`（实际模式）与 `routing`；`normalize.ts` 解析 routing 与 capabilities `auto_routing`（`default_retrieval = "auto"` 仅在 `auto_routing` 为 true 时保留，否则归一 hybrid）；`modes.ts` 在 `auto_routing` 时提供 Auto 选项（`buildModeOptions` / `resolveMode`）+ `modeLabel` 与回退原因文案；`reducer.ts` result 帧回填实际模式与 routing；`ModeSelector` 增 Auto 选中态配色（violet，延续 CMC 构造，三档不动）；`MessageBubble` auto 轮次展示「Auto → 实际模式」与回退原因。**对比度实测**：light `violet-800` on `violet-100` 7.57:1、dark `violet-200` on `violet-500/20`(over slate-800) 8.44:1，均 ≥ WCAG AA 4.5:1（既有三档同法复核 6.37–6.78:1）。前端 typecheck / lint / vitest 全绿（277 passed，新增 modes / normalize / reducer / components 用例）；离线装配组 capabilities 用例入 `tests/unit/test_chat_auto_mode.py`（`test_capabilities_*` 4 例）。全量禁网 2939 passed（既有 capabilities 精确字典断言补 `auto_routing: false` 一键，语义不变），红线 0 违规。计划存档 `.agents/plans/MR4.md` |
| MR5 | e2e、门控冒烟与文档收口 | 假后端 Playwright auto 用例通过；`RAG_MODE_ROUTER_INTEGRATION=1` 下真实端点对三类典型问题给出合法决策、把 `base_url` 指向不可达地址后回退 `fallback_retrieval` 且 trace 记 `unreachable`；同一问题集合 auto 与人工三选一的质量 / 延迟 / 成本对比记入本表；USAGE_GUIDE 新章节合入 | 已完成（2026-11-19，门控通过）。离线交付：假后端 `ScriptedModeRouter`（关键词定档 / 抛 unreachable）+ capabilities `auto_routing`；Playwright auto 用例（Auto 选项出现、「Auto → 实际模式」、回退文案）随 chat spec 7 passed（需重建 `frontend/out`）；不可达 `base_url` 的离线等价用例（真实 `CloudflareWorkersAIRouter` + 不可达 loopback → 回退 + trace 记 `unreachable`）；USAGE_GUIDE 第 11 章（含目录 / §10.4 Auto 行与 `routing` 帧 / §10.8 门控变量 / README 配置表同步）。全量禁网 2941 passed / 23 skipped / 1 xfailed（基线 2939 + 2），红线 0 违规，前端 typecheck / lint / vitest 277 passed 全绿。门控实测（凭据经 gitignored `.env-mode-router.local` 注入，本地经 `NO_PROXY='*'` 绕过系统代理）：真实端点对标注集每档一题 **3/3 合法定档**（conf 0.837 / 0.885 / 0.627，单次决策 269–1316 ms，首次调用含连接建立；`clef-flash` zh-v2）；`base_url` 指向不可达地址经真实 settings 装配回退 `fallback_retrieval`，result 帧与 trace `mode_routing` 均记 `unreachable`。期间实测发现并修复两处缺陷：`BaseModeRouter.__init__` 实例属性遮蔽子类 `backend_name` 类属性（真实后端 trace `backend` 会记 `unknown`）、`engine.py` ↔ `libs.mode_router` 循环导入（`scripts/eval_mode_router.py` 单独运行即 ImportError，导入移入 `_route`）。**同一问题集合 auto 与人工三选一的质量 / 延迟 / 成本对比按用户决议延后**，移入 [mode-router-followups 台账](../backlog/mode-router-followups_backlog.md) #1。计划存档 `.agents/plans/MR5.md`（含偏离记录） |
| MR6 | 接入 Jev（`typesafe` 后端） | 前置：用户提供 TypeSafe API key 与固定的模型版本号。配置开放 `typesafe`；§4 MR6 组件用例通过；用同一标注集跑 MR0.2 同口径基线，与 Clef 并表记入本行；实测确认 Clef 与 Jev 的请求和响应只在端点、鉴权与外层包装上不同 | 未开始（后续接入，凭据到位后排期） |
| MR7 | 接入本地微调模型 | 前置：用户提供已部署的端点、模型名与输出格式（三档标签原文，或其他标签集及映射）。端点提供 System One 接口时，复用 MR2 后端加 `base_url` 覆盖；否则开放 `openai_compatible` 后端并通过 §4 MR7 组件用例。用同一标注集跑同口径基线，与 Clef / Jev 并表 | 未开始（后续接入，端点到位后排期） |

> 进度备注：交付后在各任务下回填状态与关键实测数据；全部收口后本规格移入 `docs/frozen/` 并更新 MOC.md。MR0.2 不达标时，按 docs-governance 将本规格移回 backlog 或止于分析，并在谱系表如实记录。MR6 / MR7 在本规格冻结时若仍未到位，移入派生台账（D6），不阻塞冻结。

## 6. 验收清单

- [x] `mode_router.enabled: false`（默认）时，Chat 的帧、trace、capabilities 与基线一致，未加载路由器（MR1/MR3 离线组：工厂 `None`、非 auto 轮次无 `routing` 帧 / `mode_routing` 阶段；全量套件不改即绿）
- [x] 启用后 Web UI 出现 Auto 选项；选 Auto 提问，助手消息显示实际使用的模式，trace 可查决策与耗时（MR4 前端 + MR5 Playwright auto 用例，假后端 `ScriptedModeRouter`）
- [x] `allow_deep: false` 时 Auto 永不选 deep；决策服务不可达或超时后，回答走 `fallback_retrieval` 且标明回退原因，会话不中断（MR3 候选口径单测 + MR5 门控不可达用例）
- [x] MCP 工具的 schema 与行为不变（MR1 起 D1 红线，`scripts/check_redlines.py` 0 违规）
- [x] Clef 凭据只经环境变量提供，trace 与日志中不出现 token 与 Account ID（MR2/MR3 组断言 `api_token` / `account_id` 不入 trace）
- [x] `pytest tests -q`、CI 等价禁网命令、`python scripts/check_redlines.py`、前端 typecheck / lint / test / build 全绿（MR5 收口：2941 passed / 23 skipped / 1 xfailed；红线 0 违规；vitest 277 passed；Playwright chat 7 passed）
- [x] 回写完成（§7：USAGE_GUIDE 第 11 章 + 第 10 章 `routing` 帧、README 配置表与导航、MOC、派生台账）

## 7. 实现合入后的回写清单

- `docs/reference/USAGE_GUIDE.md`：新章节（Clef 接入：Cloudflare Account ID 与 Workers AI API token 的获取、可选 AI Gateway；Jev 与本地模型部署在 MR6 / MR7 交付时补充；`mode_router` 配置、Auto 用法、降级行为与 D3 口径、`RAG_MODE_ROUTER_INTEGRATION` 冒烟）；第 10 章 WS 帧协议补 `routing` 字段与 `routing` status 阶段、capabilities 补 `auto_routing`
- `README.md`：配置说明中补 `mode_router` 节一句话说明
- `docs/MOC.md` 与根目录 README 导航表：本规格状态
- 本规格冻结时：按 docs-governance §2.4 分配编号、补冻结记录头；`AGENTS.md` 新增 frozen「必读」条目（改动 `src/core/chat/` 模式解析、`libs/mode_router/` 或 Chat 模式选择器前先读）；遗留事项建派生台账

## 8. 隐私注记

- **暴露面**：不新增 HTTP 路由；`GET /chat/capabilities` 只多一个布尔字段 `auto_routing`，不暴露 `base_url`、模型名或 token。
- **外发边界**：每个 auto 轮次把改写后的问题文本（及三档模式的固定描述）发给所选决策服务。首期 Clef 是 Cloudflare 托管服务，问题文本会离开本机发往 Cloudflare（模型页标注 Zero data retention）；Jev（MR6）发往 TypeSafe（服务在美国）；只有本地模型（MR7）走本机回环。不发送历史消息原文、检索结果或文档内容，但改写后的问题可能带入历史中被指代的内容。
- **开关策略**：默认 `enabled: false`，不发任何请求；启用托管服务必须显式改配置并提供凭据。`api_token`（`${MODE_ROUTER_API_TOKEN}`）与 `account_id`（`${CLOUDFLARE_ACCOUNT_ID}`）从环境变量解析，不落盘、不写入 trace 与日志。USAGE_GUIDE 写明托管服务的外发含义与 Workers AI 计费。

## 9. 风险与开放问题

| # | 风险 / 开放问题 | 处置 |
|---|----------------|------|
| R1 | 微调模型决策质量不达标（错选 deep 造成高成本、错选 hybrid 造成低质量） | MR0.2 立项门；门槛按 D2 与用户拍板；不达标即搁置，不改代码。2026-11-18 已通过（见 D2） |
| R2 | 决策延迟叠加每轮回答 | `timeout_s` 硬顶（默认 3，上限 10）+ 回退；`elapsed_ms` 入 trace；托管服务另有跨境网络往返，MR0.2 在本机实测 p95 后确认 `timeout_s`，必要时改用 `clef-flash` |
| R3 | `auto` 语义扩散到其他链路 | 1.2 列出全部六处模式枚举；Out of Scope 明确 CLI、评估、Playground、MCP 不感知 |
| R4 | Auto 配色与 CMC 三色冲突或对比度不足 | MR4 验收含 light / dark 实测对比度；三档既有配色不动 |
| R5 | 决策端点成为新故障域 | 进程外托管 + 回退 + trace 可观测；默认关闭 |
| R6 | 路由选中 agentic 但 sidecar 未启动 | D3：与人工选中一致，不做探测；USAGE_GUIDE 写明，用户可开 `chat.fallback_to_chat_only` 或不启用路由器 |
| R7 | 厂商服务变动：Jev 处于 early access，模型别名（如 `jev-latest`）会随版本漂移，价格与 API 也可能变 | 配置要求固定版本号；阈值按具体版本标定，换版本后重跑同口径基线 |
| R8 | 决策模型对中文问题与英文 criteria 的表现未知 | MR0.2 已对比：中英文相当，选用 zh-v2；影响更大的是文案内容（v1 → v2 命中率约 +35 个百分点） |
| R9 | 首期依赖外部托管服务，问题文本出本机 | §8 写明；默认关闭；MR7 提供纯本地选项 |
