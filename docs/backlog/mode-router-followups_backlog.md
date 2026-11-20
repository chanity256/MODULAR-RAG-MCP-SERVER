# 检索模式路由器遗留事项台账

> 起源：[15_MODE_ROUTER_SPEC_frozen.md](../frozen/15_MODE_ROUTER_SPEC_frozen.md) 冻结遗留（MR5 的对比记表项按用户决议延后；MR6 / MR7 按其排期备注与 D6 移入台账，不阻塞冻结）。本台账只登记候选，不承诺排期；某项要推进时按单文件晋升另立提案或从台账拆分。

## 明确不做（继承规格 Out of Scope，不再重议）

- MCP 侧 auto（D1）：`query_knowledge_hub` / `agentic_search` 的 schema 与行为不变。
- Query Playground、CLI（`scripts/query.py` / `scripts/evaluate.py`）、评估链路感知 auto。
- 决策结果缓存、多模型投票、决策后二次升级（见规格 §2.2 全表）。

## 候选池

### 1. 同一问题集合 auto 与人工三选一的质量 / 延迟 / 成本对比记表（MR5 验收遗留）

- **现状**：MR5 门控冒烟已通过（2026-11-19：真实端点三类典型问题 3/3 合法定档，`clef-flash` zh-v2 单次决策 269–1316 ms；不可达 `base_url` 回退 `fallback_retrieval` 且 trace 记 `unreachable`），但「同一问题集合 auto 与人工三选一各跑一遍、对比质量 / 延迟 / 成本并记入规格任务表」一项按用户决议延后，未执行。
- **三问草案**：① 已观察问题 = 定档正确性（离线基线）已验证，但端到端「auto 答案质量是否与最佳人工选档相当」尚无实测记录，`default_retrieval: auto` 的默认化决策缺依据；② 更小方案 = 不做（保持 auto 为显式选项，已上线）；完整对比需真实 LLM + sidecar（agentic 两档）与人工判卷；③ 验证 = 对比表记入台账或后续规格（原规格已冻结，不再回填任务表）。
- **执行口径建议**：标注集每档一题（共 3 题，即 MR5 门控同款）× 4 模式（auto / hybrid / agentic_fast / agentic_deep），记录每轮端到端延迟、LLM token、答案质量（用户判卷）；可扩至每档 3–5 题。凭据经 gitignored `.env-mode-router.local` 注入。
- **优先级**：中（影响是否把 `chat.default_retrieval` 设为 `auto` 的使用决策，不阻塞任何已交付功能）。

### 2. 接入 Jev（`typesafe` 后端，原 MR6）— 已决议不接入（2026-11-19）

- **决议**：用户决定后续不再接入 Jev / `typesafe` 后端，本项移出候选池、不再排期，以下条目仅留档。
- **现状**：规格 §3.6 已定契约（直连 `https://api.typesafe.ai/v1/systemone`、无外层包装、固定版本号不用 `jev-latest`）；`backend` 校验仍只接受 `cloudflare_workers_ai`。
- **前置**：用户提供 TypeSafe API key 与固定的模型版本号。
- **口径**：按原 MR6 行验收——开放 `typesafe`；组件用例（URL 与无包装响应解析）；同一标注集跑 MR0.2 同口径基线，与 Clef 并表（结果记本台账）。实测确认 Clef 与 Jev 的请求 / 响应只在端点、鉴权与外层包装上不同。
- **优先级**：随凭据到位后排期。

### 3. 接入本地微调模型（`openai_compatible` 后端，原 MR7）

- **现状**：规格 §3.2/§3.6 已定契约（vLLM / Ollama 的 OpenAI 兼容端点；system prompt 列候选标签、只输出一个标签，`temperature: 0` / `max_tokens: 8`）；若本地模型提供 System One 接口则复用 MR2 后端加 `base_url` 覆盖。
- **前置**：用户提供已部署端点、模型名与输出格式（三档标签原文或其他标签集及映射）。
- **口径**：按原 MR7 行验收——开放 `openai_compatible` 并过组件用例；同一标注集同口径基线并表。
- **优先级**：随端点到位后排期；纯本地路径可消除 §8 的外发边界顾虑。
