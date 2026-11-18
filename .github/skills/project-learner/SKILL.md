---
name: project-learner
description: "Interactive project learning coach via interview-style Q&A. Reads codebase and docs, dynamically generates interview questions per knowledge domain and sub-topic, conducts up to 4 follow-up rounds, scores answers, provides learning guidance with code/doc references, and persists progress. 13 domains × 4-6 sub-topics = 65 knowledge points covering RAG pipeline, Web UI/Chat, Sirchmunk sidecar, logging, ingestion robustness and CI/evals (aligned with docs/frozen/ specs). Use when user says '学习项目', '了解项目', '检验项目', '项目学习', '面试准备', 'learn project', 'study project', 'review project', 'interview prep', 'knowledge check', or wants to understand/master the project through guided Q&A."
---

# Project Learner

Interactive interview-coach that helps users master this project through guided Q&A.

All user-facing interaction in **中文**. Internal instructions in English.

## Pipeline Overview

```
Discovery → Check History → User Intent → Select Domain → Select Sub-topic
→ Generate Question → Interactive Q&A (≤4 follow-ups) → Evaluate
→ Learning Guide → Persist Progress → Continue or End
```

---

## Phase 1: Project Discovery

Autonomously build project understanding. Do NOT ask user anything yet.

1. Read `docs/frozen/01_DEV_SPEC_frozen.md` — project goals, architecture, tech stack, module design
2. Read `config/settings.yaml` — configuration system
3. List `src/` directory tree — module structure (core/, ingestion/, libs/, api/, mcp_server/, observability/); list `frontend/` top level
4. Read key entry points: `main.py`, `scripts/ingest.py`, `scripts/query.py`, `scripts/start_api.py`
5. List `tests/` — testing strategy overview
6. Skim `docs/MOC.md` 文档谱系表 — which frozen specs (WC/SX/WS/LG/IR/KE/SD) govern which modules

Build an internal mental model covering these **13 Knowledge Domains**, each containing **4-6 Sub-topics** (知识点), totaling **65 interview knowledge points**:

### Domain & Sub-topic Map

| ID | 知识域 / 知识点 | Key Code Areas |
|----|----------------|---------------|
| **D1** | **RAG Pipeline 整体架构** | |
| D1.1 | 端到端数据流：MCP 工具路径与 Web UI Chat 路径两条链路 | `docs/frozen/01_DEV_SPEC_frozen.md`, `main.py`, `scripts/` |
| D1.2 | 分层架构设计：core/ingestion/libs + api/ + frontend/ 各层职责与依赖方向 | `src/core/`, `src/ingestion/`, `src/libs/`, `src/api/`, `frontend/` |
| D1.3 | Pipeline 组装：配置驱动的组件组合机制 | `main.py`, `config/settings.yaml`, `src/core/settings.py` |
| D1.4 | 核心数据类型：Document、Chunk、ChunkRecord 等类型系统 | `src/core/types.py` |
| D1.5 | 入口脚本设计：ingest/query/evaluate/start_api 的职责划分与参数传递 | `scripts/ingest.py`, `scripts/query.py`, `scripts/evaluate.py`, `scripts/start_api.py` |
| **D2** | **Ingestion Pipeline** | |
| D2.1 | Pipeline 整体流程：从文档加载到向量存储的阶段设计 | `src/ingestion/pipeline.py` |
| D2.2 | Chunking 策略与 ChunkID：RecursiveSplitter 分割逻辑、`{doc_id}_{index}_{hash8}` 确定性 ID | `src/ingestion/chunking/`, `src/libs/splitter/` |
| D2.3 | Transform 链：ChunkRefiner、MetadataEnricher 的职责与执行顺序 | `src/ingestion/transform/` |
| D2.4 | Embedding 编码：Dense/Sparse 双编码、差量计算与批处理 | `src/ingestion/embedding/` |
| D2.5 | 存储层：VectorUpserter、BM25 JSON 索引、ImageStorage 三类存储协同 | `src/ingestion/storage/` |
| **D3** | **Hybrid Search & Retrieval** | |
| D3.1 | Dense Retrieval：向量检索原理与 DenseRetriever 实现 | `src/core/query_engine/dense_retriever.py` |
| D3.2 | Sparse Retrieval：BM25 稀疏检索与 JSON 索引持久化 | `src/core/query_engine/sparse_retriever.py`, `src/ingestion/storage/bm25_indexer.py` |
| D3.3 | RRF 融合与 N 路 `fuse_routes`：含 Agentic 第三路（dense>sparse>agentic） | `src/core/query_engine/hybrid_search.py`, `fusion.py` |
| D3.4 | QueryProcessor：查询预处理与查询扩展机制 | `src/core/query_engine/query_processor.py` |
| D3.5 | Response 构建：ResponseBuilder、CitationGenerator、MultimodalAssembler | `src/core/response/` |
| **D4** | **Rerank 机制** | |
| D4.1 | Reranker 抽象与工厂模式：BaseReranker 与 RerankerFactory 设计 | `src/libs/reranker/base_reranker.py`, `reranker_factory.py` |
| D4.2 | CrossEncoder Reranker：模型原理与实现细节 | `src/libs/reranker/cross_encoder_reranker.py` |
| D4.3 | LLM Reranker：基于大语言模型的重排序方案与 Prompt 设计 | `src/libs/reranker/llm_reranker.py` |
| D4.4 | Rerank 在检索 Pipeline 中的集成位置与降级策略 | `src/core/query_engine/reranker.py` |
| **D5** | **MCP Server 协议** | |
| D5.1 | MCP 协议与 stdio JSON-RPC：交互模型与 stdout 纪律（日志走 stderr） | `src/mcp_server/server.py` |
| D5.2 | Tool 注册机制：3 个常驻工具 + `agentic_search` 门控注册（`enabled` 缺省 false） | `src/mcp_server/tools/` |
| D5.3 | ProtocolHandler：请求路由、分发、参数校验（越权 → `-32602`）与能力协商 | `src/mcp_server/protocol_handler.py` |
| D5.4 | Server 生命周期管理与异常处理（懒初始化、trace 记录） | `src/mcp_server/server.py`, `protocol_handler.py` |
| **D6** | **可插拔架构 & 配置系统** | |
| D6.1 | 工厂模式全景：LLM/Embedding/Splitter/Loader/VectorStore/Reranker/AgenticSearch 七类工厂 + fake 后端 | `src/libs/*/factory*.py` |
| D6.2 | settings.yaml 配置结构与 Settings 加载：缺失走默认、非法值 fail-fast、legacy `dashboard:` 节静默忽略 | `config/settings.yaml`, `src/core/settings.py` |
| D6.3 | LLM Provider 多厂商支持与真流式 `chat_stream`（SSE/NDJSON） | `src/libs/llm/` |
| D6.4 | Embedding Provider 抽象：多后端实现对比与选型策略 | `src/libs/embedding/` |
| D6.5 | Base 类设计哲学：接口抽象、可选能力方法（`chat_stream`/`search_stream` 默认回退） | `src/libs/*/base_*.py` |
| **D7** | **多模态与文档格式** | |
| D7.1 | PDF/Office 解析：PDFLoader、markitdown optional 依赖（extra `ingest-formats`） | `src/libs/loader/`, `src/ingestion/pipeline.py` |
| D7.2 | Markdown Vault 感知摄取：frontmatter、wikilink、`![[img]]` ImageRef、title 三级兜底 | `src/libs/loader/markdown_loader.py`, `docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md` §3.8.1 |
| D7.3 | Vision LLM 与 ImageCaptioner：图片理解与 Caption 生成 | `src/libs/llm/azure_vision_llm.py`, `src/ingestion/transform/image_captioner.py` |
| D7.4 | 多模态 Chunk 存储与检索：ImageStorage 与 MultimodalAssembler 协同 | `src/ingestion/storage/image_storage.py`, `src/core/response/multimodal_assembler.py` |
| D7.5 | 扩展名双登记约束：pipeline 与 ingest.py 两处登记，drift guard 锁死 | `src/ingestion/pipeline.py`, `scripts/ingest.py`, `tests/unit/test_ingest_entry.py` |
| **D8** | **可观测性 & 评估体系** | |
| D8.1 | Trace 系统：Query/Ingestion 两类 Trace + Chat 新增三个 stage（`routes="chat"`） | `src/core/trace/`, `docs/frozen/06_WEBUI_CHAT_SPEC_frozen.md` D7 |
| D8.2 | 日志分层与按日切分：`logs/traces/` 与 `logs/app/`、`RAG_LOG_ROOT` 测试隔离、不用 TimedRotatingFileHandler 的原因 | `src/core/trace/persist.py`, `src/observability/logger.py`, `docs/frozen/07_LOGGING_SPEC_frozen.md` |
| D8.3 | Web UI 架构：FastAPI + Next 静态导出单端口托管、七页等价、services 层保留（Streamlit 已退役） | `src/api/`, `frontend/`, `docs/frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md` |
| D8.4 | 评估指标体系：Recall/Precision/MRR 与 Ragas/DeepEval/自定义框架集成 | `src/observability/evaluation/`, `scripts/evaluate.py` |
| D8.5 | Trace 持久化与读取优化：JSON Lines 单行追加、TraceService `(mtime_ns,size)` 缓存 | `src/core/trace/persist.py`, `docs/frozen/07_LOGGING_SPEC_frozen.md` §3.4 |
| **D9** | **测试策略 & 工程化** | |
| D9.1 | 测试分层策略：Unit/Integration/E2E 各层覆盖范围与离线确定性纪律 | `tests/unit/`, `tests/integration/`, `tests/e2e/` |
| D9.2 | Test Fixtures 与 fake 注入：`httpx.MockTransport`、FakeAgenticSearch、`RAG_LOG_ROOT` | `tests/conftest.py`, `src/libs/agentic_search/fake_backend.py` |
| D9.3 | 门控真实集成测试：`RAG_LLM/E2E/AGENTIC/CHAT_INTEGRATION` 的触发条件与边界 | `pyproject.toml`, `tests/e2e/` |
| D9.4 | pyproject.toml 工程配置：依赖管理、optional extras、工具链集成 | `pyproject.toml` |
| D9.5 | Web UI 测试：vitest 单测、Playwright e2e（离线假后端）、`tests/e2e_web` 冒烟 | `frontend/`, `tests/e2e_web/` |
| **D10** | **Document Manager & 幂等性** | |
| D10.1 | 文档去重与历史表 v2：`(source_path, collection)` 主键、`file_hash`/`failed_stage` | `src/ingestion/document_manager.py`, `src/libs/loader/file_integrity.py` |
| D10.2 | 增量 Ingestion 与目录同步：`--sync/--prune/--dry-run` 五态（new/modified/unchanged/deleted/stale） | `src/ingestion/document_manager.py`, `scripts/ingest.py` |
| D10.3 | 跨存储协调删除：四存储固定顺序（BM25→Chroma→图片→历史表最后）与可重入幂等 | `src/ingestion/document_manager.py` |
| D10.4 | 版本替换与失败回滚：按旧 file_hash 定向清理、`failed_stage` 记录、指数退避重试 | `src/ingestion/pipeline.py`, `src/ingestion/errors.py` |
| D10.5 | Collection 管理与已知遗留：chunk_id 不含 collection 的覆盖风险 | `src/ingestion/document_manager.py`, `docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md` §7.1 |
| **D11** | **Web UI 与多轮 Chat** | |
| D11.1 | Web UI 架构：FastAPI + Next 静态导出、SPA 回退、`RAG_API_TOKEN` 独立鉴权 | `src/api/`, `scripts/start_api.py`, `docs/frozen/06_WEBUI_CHAT_SPEC_frozen.md` |
| D11.2 | 多轮上下文三步法：裁剪→话题过滤→查询改写；ChatRetriever Protocol 分层守卫 | `src/core/chat/history.py`, `context.py`, `retriever.py` |
| D11.3 | AnswerGenerator 与引用对齐：编号同源同序、grounded 降级语义、空检索短路 | `src/core/chat/answer_generator.py`, `engine.py` |
| D11.4 | 真流式与 WS 协议：`chat_stream`、`WS /api/v1/chat` 帧协议、重试窗口止于首个 stream 帧 | `src/libs/llm/`, `src/core/chat/engine.py` |
| D11.5 | 会话与任务持久化：SessionStore 两表落库时机、任务模型 202+task_id | `src/core/chat/session_store.py`, `src/api/tasks.py` |
| D11.6 | Chat 模式选择器：三模式可用性（capabilities）、DEEP 门控、CMC 按模式着色 | `frontend/lib/chat/modes.ts`, `frontend/components/chat/ModeSelector.tsx` |
| **D12** | **Sirchmunk Sidecar 与 Agentic Search** | |
| D12.1 | 进程隔离红线与 G0 终裁：主 venv 永不 import sirchmunk 的四大原因、裸 rg 基线裁决 | `docs/frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md` §4/§7 |
| D12.2 | HTTP 适配层：`SirchmunkHttpBackend` 错误分型、字段名陷阱（`include_patterns`） | `src/libs/agentic_search/http_backend.py` |
| D12.3 | PathGuard 双层防线：调用前 fail-closed 校验 + 调用后证据过滤 | `src/libs/agentic_search/path_guard.py` |
| D12.4 | MCP tool 门控与第三路融合：`AgenticTrigger` 三策略、`llm_fallback` 恒 false | `src/mcp_server/tools/agentic_search.py`, `docs/frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md` |
| D12.5 | sidecar 托管与门控 WS 端点：`sidecar_manager.py`、SX5.3 线程泄漏与 `deep_progress_stream` 默认关闭 | `scripts/sidecar_manager.py`, `src/api/routes/search.py` |
| D12.6 | 知识复用与演化实验裁决：SX4 复用结论、KE2 成对实验 insufficient_evidence、默认全关 | `docs/frozen/13_SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC_frozen.md` |
| **D13** | **工程化：CI、Evals 与流程纪律** | |
| D13.1 | CI 确定性门：双 job、禁网 pytest 参数逐项、`check_redlines.py` 三条红线 | `.github/workflows/ci.yml`, `scripts/check_redlines.py` |
| D13.2 | 提交流程 D1：fork 分支保护、PR 必须、SSH 推送原因、merge commit 合入 | `docs/specs/AI_NATIVE_SDLC_SPEC.md` §1.3/§3.1 |
| D13.3 | Agent evals：判据①–⑥、fixture worktree、3 取 2、baseline 门 | `evals/check.py`, `evals/run.py` |
| D13.4 | skill 单一来源：`.github/skills/` 权威、镜像同步、状态文件 source-only | `scripts/sync_skills.py`, `tests/unit/test_skill_mirrors.py` |
| D13.5 | Hook 守卫：read_guard 先读后改（fail-open）与 test_guard 测试文件保护 | `.factory/hooks/read_guard.py`, `.factory/hooks/test_guard.py` |

> **Total: 13 domains × 4-6 sub-topics = 65 knowledge points**
> Each sub-topic can be studied multiple times with different questions, providing 150+ possible interview questions.
> 地图对齐 `docs/frozen/` 各规格收口结论（2026-11-16 修订）；出题前如需规格细节，按 Key Code Areas 里的规格路径深读。

---

## Phase 2: Check Learning History

1. Try reading `.factory/skills/project-learner/references/LEARNING_PROGRESS.md`
2. **File missing** → first-time learner, proceed to Phase 3
3. **File exists** → parse BOTH tables:
   - **Domain Summary**: which domains are ⬜/🔴/🔶/✅
   - **Sub-topic Progress**: which sub-topics are ⬜ (unlearned), 🔴 (weak ≤3), 🔶 (learning 4-6), ✅ (mastered ≥7)
   - Count: total sub-topics mastered / 65
   - Identify lowest-scoring sub-topics for review recommendation

---

## Phase 3: User Intent

Use `ask_questions` (中文) to determine what the user wants:

**Question 1 — 学习模式** (single-select):

| Option | Description |
|--------|------------|
| 🆕 学习新知识点 | Pick from unlearned/weak sub-topics |
| 📖 复习已学内容 | Review previously learned low-score sub-topics |
| 📋 查看学习进度 | Display progress table, then end |
| 🎯 Agent 推荐 | Auto-pick the best next sub-topic to study |

If user picks 📋 → display the full progress table from `LEARNING_PROGRESS.md` and stop.

If user picks 🎯 → Agent auto-selects the optimal sub-topic (prioritize: ⬜ unlearned in weakest domain → 🔴 weak → 🔶 lowest score). Skip Question 2 & 3, go directly to Phase 4.

**Question 2 — 知识域选择** (single-select, only for 🆕 or 📖):

List all 13 domains with current status + completion rate. Example format:
- `D1 RAG Pipeline 整体架构 [2/5 ✅] 🔶`
- `D2 Ingestion Pipeline [0/5 ✅] ⬜`

For 📖 mode: only show domains with previous scores. For 🆕 mode: prioritize domains with most ⬜ sub-topics.

**Question 3 — 知识点选择** (single-select, only after Question 2):

List all sub-topics under the selected domain with their status:
- `D2.1 Pipeline 整体流程 ⬜ 未学习`
- `D2.2 Chunking 策略 🔶 6/10`
- `D2.3 Transform 链 ✅ 8/10`

Include option:
- 🎯 Agent 推荐 — auto-pick the weakest/unlearned sub-topic in this domain

---

## Phase 4: Generate Interview Question

Based on the selected **sub-topic** (not just domain):

1. **Deep-read** the sub-topic's specific source code — read actual class definitions, key functions, config sections listed in the Sub-topic Map
2. **Dynamically generate** ONE main interview question (中文) grounded in this sub-topic's real code
3. **Internally prepare** up to 4 progressive follow-up questions (do NOT show these yet)
4. **Avoid repeating** questions from previous sessions — check Detailed History for this sub-topic and generate a different angle

### Question Design Principles

- Questions MUST reference real code/architecture from THIS project, never generic
- Questions should be specific to the sub-topic, not the whole domain
- Difficulty progression for follow-ups:
  - Follow-up 1: "为什么这样设计？" (design rationale)
  - Follow-up 2: "和替代方案对比有什么优劣？" (trade-offs)
  - Follow-up 3: "边界条件/异常情况怎么处理？" (edge cases)
  - Follow-up 4: "如果让你重新设计，会怎么做？" (redesign thinking)
- Adjust follow-ups dynamically based on what the user actually answers

### Question Angle Variety

Each sub-topic can be asked from multiple angles. When a sub-topic is revisited, pick a DIFFERENT angle:
- **What**: 描述这个模块/机制做了什么
- **How**: 具体实现细节，代码层面怎么做的
- **Why**: 为什么选择这种设计方案
- **Compare**: 和替代方案的对比
- **Debug**: 如果出了问题怎么排查
- **Extend**: 如果要扩展功能怎么做

### Question Format

Present to user:

```
## 🎯 面试问题

**知识域**: [Domain Name] > **知识点**: [Sub-topic Name]

**面试官问**: [Question text — specific to this sub-topic, referencing project components]

请回答：
```

---

## Phase 5: Interactive Q&A (≤4 Follow-up Rounds)

```
Round 0: Main question → User answers
Round 1-4: Brief feedback on previous answer + follow-up question → User answers
Early exit: User says "结束"/"pass"/"跳过" OR answer is sufficiently comprehensive
```

### Per-Round Behavior

1. **Acknowledge** what the user got right (1-2 sentences, 中文)
2. **Hint** at what was missed without giving away the answer (1 sentence)
3. **Ask follow-up** that digs deeper based on their answer direction

### Follow-up Output Format

```
### 第 N 轮追问

✅ **答得好**: [What they got right]
💡 **提示**: [What they could explore further]

**追问**: [Follow-up question]
```

If user's answer already covers the planned follow-up, skip to a harder one or end early.

---

## Phase 6: Evaluation

After Q&A ends, output a structured evaluation report (中文):

```markdown
## 📊 评价报告

**知识域**: [Domain] > **知识点**: [Sub-topic ID & Name] — [Question summary]
**追问轮数**: N/4

### ✅ 回答亮点
- [Strength 1 — specific to what they said]
- [Strength 2]

### ⚠️ 需要加强
- [Gap 1 — what was missed or inaccurate]
- [Gap 2]

### 📈 评分明细

| 维度 | 分数 | 说明 |
|------|------|------|
| 准确性 | X/10 | [Factual correctness of answers] |
| 深度 | X/10 | [How deep they went beyond surface] |
| 代码关联 | X/10 | [Did they reference actual code/config] |
| 设计思维 | X/10 | [Trade-off analysis, architecture reasoning] |

### 🏆 综合评分: X/10

### 📊 学习进度: [mastered count]/65 知识点已掌握
```

Scoring rules:
- Average of 4 dimensions, rounded to nearest 0.5
- 9-10: Expert level, can explain design decisions and trade-offs
- 7-8: Solid understanding, knows how and why
- 4-6: Basic understanding, knows what but not deep why
- 1-3: Surface level, needs significant study

---

## Phase 7: Learning Guide

Immediately after evaluation, provide targeted study resources (中文):

```markdown
## 📚 学习指南

### 📂 相关代码
- [file_path](file_path#LX-LY) — 说明这段代码的作用和关键逻辑

### 📄 相关文档
- [01_DEV_SPEC_frozen.md 对应章节](../../docs/frozen/01_DEV_SPEC_frozen.md) — 设计原理
- [config/settings.yaml](../../config/settings.yaml) — 相关配置项

### 🔗 参考资料
- [External concept name] — 1-sentence explanation of relevance

### 💡 建议学习路径
1. 先阅读 [file] 理解 [what]
2. 再看 [file] 掌握 [implementation detail]
3. 运行 `[command]` 实际体验效果
4. 尝试修改 [config/code] 观察变化
```

Guidelines:
- Code references MUST use actual file paths with line numbers where relevant
- Only recommend reading 3-5 key files, not entire codebase
- Include at least one hands-on command the user can run
- External references only for concepts not explained in the codebase (e.g., RRF algorithm, BM25)

---

## Phase 8: Persist Progress

Update `.factory/skills/project-learner/references/LEARNING_PROGRESS.md`.

If file doesn't exist, create it from the template in [references/LEARNING_PROGRESS.md](references/LEARNING_PROGRESS.md). If it exists, update it.

### Update Rules

1. **Append** one row to the `Detailed History` table (include Sub-topic ID)
2. **Update** the `Sub-topic Progress` table for the affected sub-topic:
   - 已学 = count of sessions for that sub-topic
   - 最高分 = max score across all sessions for this sub-topic
   - 最近分 = score from this session
   - Status: ≥7 → ✅ 掌握, 4-6 → 🔶 学习中, ≤3 → 🔴 薄弱, 0 sessions → ⬜ 未学习
3. **Recalculate** the `Domain Summary` table:
   - 已掌握 = count of ✅ sub-topics in that domain / total sub-topics in domain
   - 已学习 = count of non-⬜ sub-topics / total sub-topics
   - 平均分 = average score of all studied sub-topics in domain
   - Domain status: all sub-topics ✅ → ✅ 掌握, any studied → 🔶 学习中 or 🔴 薄弱 (based on avg), none → ⬜ 未学习
4. **Update** the `Last updated` timestamp
5. **Update** the session counter `#` (auto-increment)
6. **Update** the overall progress line: `总进度: X/65 知识点已掌握`

---

## Phase 9: Continue or End

After persisting, ask the user (中文):

| Option | Action |
|--------|--------|
| 🔄 继续学习下一个知识点 | Loop back to Phase 3 |
| 🎯 Agent 推荐下一个 | Auto-pick optimal next sub-topic, go to Phase 4 |
| 📋 查看当前学习进度 | Display full progress table |
| 🏁 结束本次学习 | Show session summary, stop |

### Session Summary (on 🏁 end)

```markdown
## 📝 本次学习总结

- 完成知识点: N 个
- 平均得分: X/10
- 最强知识点: [sub-topic] (X/10)
- 需加强知识点: [sub-topic] (X/10)
- 总进度: X/65 知识点已掌握 (XX%)

继续加油！下次建议学习: [recommended sub-topic name]
```

---

## Key Paths

| File | Purpose |
|------|---------|
| `.factory/skills/project-learner/references/LEARNING_PROGRESS.md` | Persistent learning state (65 sub-topics) |
| `docs/frozen/01_DEV_SPEC_frozen.md` | Project specification & architecture |
| `config/settings.yaml` | Configuration reference |
| `src/` | All source code modules |
| `tests/` | Test suite for understanding test strategy |
| `scripts/` | CLI entry points (ingest/query/evaluate) |
