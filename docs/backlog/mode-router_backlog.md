<!-- 候选功能提案：检索模式路由器（Mode Router，MR 系列）。格式对齐 backlog/README.md 约定与 OBSIDIAN_RAG_SPEC_backlog.md 先例。 -->
# 检索模式路由器（Mode Router）提案 (MODE_ROUTER)

> 版本：0.1 — 候选提案，未立项排期
>
> 起源：会话提案（2026-11-16，用户需求：Chat / 检索的模式选择交由本地部署的微调决策模型自动完成，替代人工三选一）

## 目录

- 背景与动机
- 三问回答（backlog 约定）
- 目标与非目标
- 方案概览
- 本地决策模型接入方式
- 配置契约草案
- 涉及文件清单
- 测试方案草案
- 风险与开放问题
- 任务草案（MR 系列，立项时改写）

---

## 1. 背景与动机

### 1.1 解决哪个已经观察到的问题？

系统现有三种检索模式，成本/延迟/收益差异极大：

| 模式 | 延迟量级 | 能力边界 |
|------|---------|---------|
| `hybrid`（Dense + BM25 + RRF） | 秒级 | 主库内语义 + 关键词检索，依赖已摄取语料 |
| `agentic_fast`（Sirchmunk FAST） | 十秒级 | 可越出主库扫描白名单目录，单轮探索 |
| `agentic_deep`（Sirchmunk DEEP） | 数十秒级 | 多轮迭代探索 + 目录扫描，成本最高 |

当前用户必须在三选一中**人工决策**：要么每轮在 Web UI 手选，要么把 `chat.default_retrieval` 固定为某一档（通常折中选 hybrid）。结果两种失衡：

- 简单问题也被迫忍受 deep 的数十秒延迟（如果用户为了覆盖面把默认值调高）；
- 复杂问题只拿到 hybrid 的浅层结果（如果默认值选低），用户还得自行意识到并手动换 deep 重问。

三类信息（问题复杂度、是否可能超出已摄取语料、用户对延迟的容忍度）**在提问那一刻就可以由问题文本本身推断**，这正是小模型分类任务的定义域。

### 1.2 更简单、改动更小的替代方案（及为何不取）

- **不做路由，维持 default_retrieval**：零成本，但 1.1 的失衡持续存在；本提案以 `enabled: false` 为默认，存量行为完全不变，增量风险可控。
- **用规则/启发式路由**（关键词匹配、问题长度阈值）：零模型成本，但覆盖不了"是否超出语料"这类需要语义判断的信号，且规则集合会持续膨胀难维护。
- **用现有主 LLM 兼任决策**：不加组件，但每轮多一次主 LLM 调用（延迟与费用），且与"主 LLM 服务检索降级/纯聊天"共用可用性，故障域耦合；本地小模型决策是毫秒级、零 token 费用。
- **用交叉编码器分数触发第三路**（既有 `trigger: fallback` 机制，SX 系列 S2.2）：它在检索**之后**补触发，解决"信号不足时补救"；本提案在检索**之前**定模式，解决"用户免选"。两者正交，可共存。

### 1.3 如何确认它确实有效？

- **决策质量**：构建独立标注集（问题 → 期望模式），度量决策命中率；校准与评估集与 golden test set 分离，禁用评估 golden set 调参（DEV_SPEC 9.5 纪律）。
- **端到端指标**：同一问题集合上，`auto` 与人工三选一的「回答质量 × 平均延迟 × token 成本」对比。
- **降级安全性**：路由器失败/超时的用例中，回退 `fallback_retrieval` 后回答质量不劣于固定 hybrid 基线。

---

## 2. 目标与非目标

### 2.1 In Scope（目标）

| # | 目标 | 一句话 |
|---|------|--------|
| G1 | `auto` 检索模式 | 新增第四种模式值，选中时由决策模型在三档真实模式中定一档 |
| G2 | 可插拔 `ModeRouter` 组件 | 落在 `src/libs/`，`Base*` 抽象 + 工厂，`enabled: false` 时零加载（对齐 `agentic_search` 先例） |
| G3 | 进程外本地决策模型 | 经 OpenAI 兼容 HTTP 端点（vLLM / Ollama）调用本地微调模型，主 venv 不装推理框架 |
| G4 | 门控感知候选集 | 喂给决策模型的候选只含当前可 serve 的检索器；deep 受 `allow_deep` 门控约束 |
| G5 | 失败降级 | 超时 / 非法输出 / 端点不可达 → 回退 `fallback_retrieval`，trace 记录降级原因，不中断会话 |
| G6 | 决策可观测 | 决策结果与降级原因写入 trace（`settings.retrieval` / search_log 帧），Web UI 追踪列表可查 |
| G7 | 双入口覆盖 | Web UI Chat（WS `user_message.retrieval`）与 MCP `query_knowledge_hub`（新增 `retrieval` 参数）均可传 `auto` |

### 2.2 Out of Scope（明确不做）

- **不做决策模型的训练/微调本身**：模型从外部带入；本规格只管"已部署模型的接入、调用与降级"。
- **不做 Query Playground 的 auto**：Playground 是调试三档 agentic 模式的工具，显式选型即其语义；`auto` 先落在 Chat 与 MCP。若后续有需求另行提案。
- **不动既有三档模式的行为契约**：`RETRIEVAL_MODES` 校验、capabilities 过滤、`agentic_search.trigger` 机制均不改语义（`auto` 走独立常量，见 3.1）。
- **不改 CMC 冻结规格的配色方案**：ModeSelector 新增 `auto` 选中态配色属于新行为，由本规格立项后的任务承接，不回改 `11_CHAT_MODE_COLORS_SPEC_frozen.md`。
- **不做决策结果缓存**：同一问题的重复决策直接再问路由器（本地小模型毫秒级，缓存收益不成比例；后续观察再议）。

---

## 3. 方案概览

### 3.1 核心决策点与 `auto` 常量

模式解析现收敛于 `ChatEngine.chat()` 一行：`mode = retrieval or self._settings.default_retrieval`。方案在其之前插入决策步骤。

**`auto` 作独立常量、不进 `RETRIEVAL_MODES`**：`RETRIEVAL_MODES` 同时被引擎校验（`mode not in self._retrievers` 会失败——不存在名为 auto 的检索器）与 capabilities 过滤（`_ALL_MODES` 过滤）复用，混入 `auto` 会污染两处语义。引擎内单独分支处理：

```python
if retrieval == RETRIEVAL_AUTO:
    retrieval = self._router.decide(message, candidates=tuple(self._retrievers))
```

### 3.2 组件与数据流

```
用户问题 → 前端发 retrieval: "auto"（或 MCP 工具参数）
        → ChatEngine.chat() 识别 auto
        → ModeRouter.decide(query, candidates=当前可用检索器的 keys)
              ├─ 成功 → 用决策出的模式走原有 _run()（零改动）
              └─ 超时/非法输出 → fallback_retrieval + trace 记录降级原因
        → 决策结果写入 trace settings.retrieval / search_log 帧
```

- `ModeRouter` 协议定义在 `src/core/chat/retriever.py`（与 `ChatRetriever` 同文件，core/chat 保持零实现依赖），实现落在 `src/libs/mode_router/`，由 `src/api/chat_wiring.py` 懒建注入（与检索器装配同构，见 `build_chat_retrievers` 先例）。
- 候选集直接取 `self._retrievers` 的 keys——它天然就是"当前可 serve 的模式"：sidecar 未启动或 `allow_deep=false` 时，deep/fast 不进候选，决策模型不可能选出不可用模式（G4）。

### 3.3 MCP 侧

`query_knowledge_hub` 增加可选参数 `retrieval: auto | hybrid | agentic_fast | agentic_deep`：路由器启用时 `auto` 可用；未启用时传 `auto` 返回 `-32602`（对齐既有越权参数校验语义）。MCP 的调用方是外部 LLM，让它显式传 `auto` 比隐式改默认行为更符合 MCP 工具契约。

---

## 4. 本地决策模型接入方式

### 4.1 部署：OpenAI 兼容 HTTP 端点（进程外托管）

```bash
# 方式一：vLLM（吞吐高，适合常驻）
vllm serve /path/to/your-finetuned-model --host 127.0.0.1 --port 8590

# 方式二：Ollama（本机最省事；libs/llm 已有 ollama 后端先例）
ollama create mode-router -f ./Modelfile
ollama serve
```

### 4.2 调用

```
POST {base_url}/chat/completions
body: { model, messages: [system: "从候选模式中选一，只输出标签", user: 问题],
        temperature: 0, max_tokens: 8 }
解析输出 → 校验在候选集内 → 决策结果
```

三个对齐既有纪律的理由：

1. **进程隔离**：与 Sirchmunk sidecar 同一自觉，主 venv 不 import 推理框架、不加载权重；决策端点挂掉只降级不拖垮主服务。
2. **零新依赖**：httpx 已在依赖中，`requirements.txt` / `pyproject.toml` 依赖区不动；`libs/llm/openai_llm.py` 已有同构调用代码可参照。
3. **离线测试纪律天然满足**：单测经 `httpx.MockTransport` 注入 fake 决策响应；真实模型走环境变量门控集成测试（对齐 `RAG_LLM_INTEGRATION` 先例，如 `RAG_MODE_ROUTER_INTEGRATION=1`）。

---

## 5. 配置契约草案

`config/settings.yaml` 新节，模式照抄 `agentic_search` 节（注释风格、env var 不落盘、fail-fast 校验）：

```yaml
mode_router:
  enabled: false                 # true 才注册 auto 模式；false 时零加载
  backend: openai_compatible     # openai_compatible | ollama
  base_url: http://127.0.0.1:8590/v1
  api_token: ${MODE_ROUTER_API_TOKEN}   # 从环境变量解析，不落盘；本地端点可留空
  model: mode-router             # 部署时的模型名
  timeout_s: 3                   # 决策发生在每轮回答之前，超时即回退
  fallback_retrieval: hybrid     # 决策失败/超时/输出不合法时的兜底模式
```

`timeout_s` 3 秒量级是刻意的：决策本身是每轮回答的延迟成本，太长不如直接用 hybrid。`chat.default_retrieval` 增加合法值 `auto`（仅当 `mode_router.enabled: true` 时允许，否则启动即报错——fail-fast）。

---

## 6. 涉及文件清单

### 6.1 新增

| 文件 | 职责 |
|------|------|
| `src/libs/mode_router/__init__.py` | 包入口，导出工厂函数 |
| `src/libs/mode_router/base_mode_router.py` | `BaseModeRouter` 抽象：`decide(query, candidates) -> str` + `ModeRouterError`；输出约束在候选集内 |
| `src/libs/mode_router/openai_compatible_router.py` | OpenAI 兼容端点实现（含 ollama backend 复用路径） |
| `src/libs/mode_router/mode_router_factory.py` | `create_mode_router(settings)` 工厂；`enabled: false` 返回 `None` |
| `src/observability/dashboard/services/` 内装配函数 | `build_mode_router(settings)`（供 `chat_wiring.py` 懒建注入） |
| `tests/unit/test_mode_router.py` | 离线单测：MockTransport 注入、非法输出回退、超时回退、候选集门控 |
| `tests/unit/test_chat_auto_mode.py` | 引擎 auto 路径：决策入 trace、失败降级、门控候选集 |

### 6.2 修改

| 文件 | 改动 |
|------|------|
| `src/core/settings.py` | `mode_router` 配置节 + 校验；`chat.default_retrieval` 允许 `auto` |
| `src/core/chat/retriever.py` | `ModeRouter` Protocol + `RETRIEVAL_AUTO` 常量 |
| `src/core/chat/engine.py` | auto 分支：决策 → 降级回退 → trace 记录 |
| `src/api/schemas/chat.py` | `RetrievalMode` Literal 加 `auto`；capabilities 上报 `auto_routing` |
| `src/api/chat_wiring.py` | 懒建注入 router；capabilities 含 `auto`（仅启用时） |
| `src/mcp_server/tools/query_knowledge_hub.py` | 新增 `retrieval` 参数（`auto` 需路由器启用，否则 `-32602`） |
| `frontend/lib/chat/modes.ts` | labels / `buildModeOptions` 加 `auto`（capabilities 门控） |
| `frontend/components/chat/ModeSelector.tsx` | `auto` 选中态配色（新行为，承接于本规格，不回改 CMC 冻结规格） |
| `config/settings.yaml` | `mode_router` 配置节示例（默认 `enabled: false`） |
| `pyproject.toml` | 集成测试门控 marker 说明（`RAG_MODE_ROUTER_INTEGRATION`，若有） |

### 6.3 文档与登记

| 文档 | 改动 | 时机 |
|------|------|------|
| `docs/specs/`（本文件 `git mv` 晋升） | 改写为规格（格式对齐 SIRCHMUNK / OBSIDIAN 先例），三问答案并入第 1 章决策依据 | 立项时 |
| `docs/backlog/README.md`、`docs/MOC.md`、README 导航表 | 提案登记（随本提案创建已同步） | 已完成 |
| `docs/reference/USAGE_GUIDE.md` | 新章节：决策模型部署（vLLM/Ollama 命令）、配置示例、auto 用法、降级行为 | 收口时 |
| `AGENTS.md` | 规格冻结后补 frozen「必读」条目 | 冻结时 |
| `docs/MOC.md` 文档谱系表 | 立项（单文件晋升）与冻结时各更新一次 | 立项/冻结时 |

---

## 7. 测试方案草案

| 层 | 内容 | 纪律 |
|----|------|------|
| 单元 | `BaseModeRouter` 输出解析与校验、工厂 enabled/disabled、配置校验 fail-fast | 离线，fake 注入 |
| 单元 | 引擎 auto 分支：决策成功走目标模式、决策结果入 trace、超时/非法输出/异常回退 fallback、降级原因入 trace | 离线 |
| 单元 | 门控候选集：`allow_deep=false` / sidecar 未注册时候选不含对应模式；capabilities 上报 | 离线 |
| 单元 | MCP 参数：`auto` 未启用时 `-32602`；启用时透传 | 离线 |
| 前端 | vitest：`auto` 选项随 capabilities 出现/消失；ModeSelector 选中态 | 离线 |
| 集成 | 真实决策端点冒烟（含真实微调模型决策质量抽查） | `RAG_MODE_ROUTER_INTEGRATION=1` 门控 |
| e2e | Web UI 假后端：auto 帧 → 假路由器响应 → 检索器路由断言 | 离线（对齐 `tests/e2e_web` 先例） |

决策质量评估（标注集命中率）不进 pytest，走评估脚本（对齐 evals 纪律：真实运行不阻塞 CI，标注集与 golden set 分离）。

---

## 8. 风险与开放问题

| # | 风险/开放问题 | 处置倾向 |
|---|--------------|---------|
| R1 | 微调模型决策质量不达标（错选 deep 造成高成本、错选 hybrid 造成低质量） | 立项前先跑独立标注集基线；质量门槛不达标则本提案搁置，不改代码 |
| R2 | 决策延迟叠加每轮回答 | `timeout_s: 3` 硬顶 + 回退；实测决策耗时入 trace 可观测 |
| R3 | `auto` 语义扩散（SessionStore 历史、评估脚本、`scripts/query.py` 等处是否需要感知） | 立项时全量盘点 `RETRIEVAL_MODES` / `RetrievalMode` 引用点，宁收窄不扩散；Playground 明确不做 |
| R4 | CMC 冻结规格配色约束 | `auto` 配色作为本规格任务，风格延续 CMC（light 浅底深字 / dark 半透明底亮字，对比度 ≥ WCAG AA） |
| R5 | 决策模型可用性成为新的故障域 | 进程外托管 + 降级回退 + trace 可观测；`enabled: false` 默认保证存量零风险 |

---

## 9. 任务草案（MR 系列，立项时改写为规格任务表）

> 仅为提案粒度，非承诺排期；立项时按 SIRCHMUNK/OBSIDIAN 先例改写（验收标准列 + 测试方案 + In/Out of Scope）。任务编号 MR 系列，与既有系列无冲突。

| 任务 | 内容 | 优先级 |
|------|------|--------|
| MR0 | 决策质量预研：标注集 + 本地微调模型基线命中率（不达标即终止） | P0（gate） |
| MR1 | 配置节 + `settings.py` 校验 + `RETRIEVAL_AUTO` 常量与 Protocol | P0 |
| MR2 | `src/libs/mode_router/` 组件 + 工厂 + 装配注入 | P0 |
| MR3 | ChatEngine auto 分支（决策/降级/trace） + Chat 单测 | P0 |
| MR4 | capabilities 与 Web UI 前端（modes.ts / ModeSelector 配色） | P1 |
| MR5 | MCP `query_knowledge_hub` 参数 | P1 |
| MR6 | 端到端验证（e2e 假后端 + 门控集成冒烟） + USAGE_GUIDE 章节 + 文档收口 | P1 |
