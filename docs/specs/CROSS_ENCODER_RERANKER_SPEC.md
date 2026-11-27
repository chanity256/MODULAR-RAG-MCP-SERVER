<!-- 开发中规格：本地 Cross-Encoder 精排生效（CER 系列，2026-11-27 立项）。让 settings.yaml 已配置的 rerank.backend: cross_encoder（cross-encoder/ms-marco-MiniLM-L-6-v2）从「配置空转、每次回退」变为真实生效：补 sentence-transformers CrossEncoder scorer 适配层 + 工厂默认惰性装配 + 可选依赖登记 + 门控真实模型冒烟。契约依据：01_DEV_SPEC_frozen.md D6（Core 层编排与回退）/ B7.8（CrossEncoder scorer 注入契约）。不改既有测试语义、不改 fallback 契约。 -->

# 本地 Cross-Encoder 精排生效规格 (CROSS_ENCODER_RERANKER_SPEC)

> 版本：0.2 — v0.2 规格评审修订（2026-11-27：修正默认装配归属至工厂 `_ctor` 以保持 `test_no_scorer_raises_on_rerank` 零改动通过、统一 D6 通道①/② 失败划分并保留诊断指引、补齐 `rerank.model` 缺失契约、修正 CER4 英文模型 sanity 口径与语言适用风险、补正字段名/装配点计数/文档落点/安装命令与已装依赖离线共存验收）；v0.1 立项（跳过提案：用户在会话中直接拍板「直接立项 spec」，决策依据见第 1、2 章，无 backlog 草案阶段）
>
> 起源与决策依据：会话内配置核查（2026-11-27，非文件来源）——用户询问 `cross-encoder/ms-marco-MiniLM-L-6-v2` 是否已配置好，核查结论为「配置已写、实现缺失、运行时空转」（详见 §1.2 三问）；契约依据以 [01_DEV_SPEC_frozen.md](../frozen/01_DEV_SPEC_frozen.md) D6 / B7.8 为权威来源
>
> 基线：`config/settings.yaml` 已含 `rerank.backend: cross_encoder` / `model: cross-encoder/ms-marco-MiniLM-L-6-v2` / `top_m: 30`；主 venv 未安装 `sentence-transformers`，本机无该模型缓存；每次查询 rerank 阶段 `last_fallback=True`
>
> 日期：2026-11-27
>
> 任务前缀：CER（取 CROSS_ENCODER_RERANKER 首字母缩写；与既有 A–I / S / IR / V / SX / WC / LG / WP / WS / SR / SD / CMC / ERD / KE / AR / MR / RA / AC / PI / EJR 系列均不冲突）

## 目录

章节顺序对齐 [SPEC-TEMPLATE](../../.factory/skills/docs-governance/assets/SPEC-TEMPLATE.md)：

- 1 项目概述（1.1 结论速览、1.2 决策依据：三问、1.3 背景与现状）
- 2 核心目标与范围（2.1 In Scope、2.2 Out of Scope）
- 3 技术选型与模块设计（3.1 scorer 适配层、3.2 工厂默认惰性装配、3.3 可选依赖与配置、3.4 已知代价与风险）
- 4 测试方案
- 5 任务排期与进度
- 6 验收清单
- 7 实现合入后的回写清单
- 8 隐私注记

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 要解决什么问题？ | `config/settings.yaml` 已配置 `rerank.backend: cross_encoder` + `model: cross-encoder/ms-marco-MiniLM-L-6-v2`，但 `src/` 内不存在任何加载真实模型的 scorer 实现（B7.8 设计为「scorer 由调用方注入」，生产注入方缺失）：管线各装配点经 `Reranker(settings)` → `RerankerFactory.create(settings)` 创建 backend 时 `scorer=None`，首次 `rerank` 即在 `_ensure_scorer()` 抛 `RuntimeError`，被 D6 双通道回退捕获——**每次查询精排都在空转**（结果退回 RRF 融合排名、trace `fallback=true`）。用户以为开了精排，实际没有；同时模型未下载、`sentence-transformers` 未安装，配置中的模型名是死数据 |
| 定位是什么？ | 补齐「配置承诺与运行时行为」之间的缺口：① `CrossEncoderScorer` 适配层（包装 `sentence_transformers.CrossEncoder`，惰性加载、构建过程可注入）；② 工厂 `_ctor` 在未注入 scorer 时登记默认惰性 `scorer_factory`（直接构造 `CrossEncoderReranker(rc)` 仍保持 B7.8 原样抛错，显式注入优先，测试与既有契约不变）；③ `sentence-transformers` 进可选依赖组（不进主依赖，未安装时行为与现状一致）；④ 门控真实模型冒烟。不改 D6 回退契约、不改既有测试语义 |
| 实施范围 | CER1–CER3 为承诺任务；CER4（真实模型冒烟）为门控任务（需下载模型并本机执行） |
| 主要代价 | `sentence-transformers` 连带 torch（CPU 版约 2 GB 磁盘占用，Linux 默认 wheel 含 CUDA 更大，可按 PyTorch CPU 源安装）；模型首次使用从 HF 下载（约 90 MB，之后离线缓存）；默认 CPU 推理 top_m=30 候选的单次精排延迟需在冒烟中实测记录 |

### 1.2 决策依据（三问）

**1. 解决哪个已经观察到的问题？**

- **配置空转（2026-11-27 会话核查实录）**：`config/settings.yaml:121-124` 配置了 cross_encoder 后端与模型名，但全仓 `src/**/*.py` 中 `CrossEncoder` / `sentence_transformers` 零匹配——`src/libs/reranker/cross_encoder_reranker.py:44-51` 的 `_ensure_scorer()` 在 scorer 为 None 时抛 `RuntimeError`（该调用点在内部 try 之外，异常直接冒出 backend），`src/core/query_engine/reranker.py:109-113` 捕获后走 D6 回退（输入原序 + `last_fallback=True`）。即：**backend 配置为 cross_encoder 的每一次查询，精排都实际未执行**。源码中 `Reranker(settings)` 生产装配点共 3 处（`src/mcp_server/tools/query_knowledge_hub.py:115` 服务 MCP 与 Playground、`src/observability/dashboard/services/query_service.py:109` 服务 Chat 链路、`scripts/query.py:218` 服务 CLI），覆盖 4 条查询入口，全部不传 scorer。
- **模型未就位**：`~/.cache/huggingface` 与 `~/.cache/torch/sentence_transformers` 均不存在；`sentence-transformers` 不在 requirements.txt / pyproject.toml / 主 venv 中。配置里的模型名当前是死数据。
- **trace 噪声**：每次查询 rerank 阶段 `fallback=true`、`last_error=RuntimeError`，检索 Trace 挖掘（retrieval-trace-mining 台账）等下游消费会被持续性假回退污染。

**2. 是否有更简单、改动更小的方案？**

| 方案 | 改动 | 结论 |
|------|------|------|
| **backend 改回 `none`** | 配置一行 | 否决：诚实但放弃精排能力；CrossEncoder 精排是 D6 / B7.8 的既定设计目标，`top_m` 截断、fallback 契约、trace 打点都已就位，只缺 scorer 一环 |
| **保持现状，只写文档说明** | 文档 | 否决：配置继续误导使用者；trace 假回退持续产生 |
| **在各装配点显式构建并注入 scorer** | 3 处调用点 + 新适配层 | 否决：装配点各自 import 重依赖、各自处理降级，重复且易漏；且 MCP tools / services 层的懒初始化路径会因此提前触模型 IO |
| **在 backend 构造函数内同步加载模型** | 1 处 | 否决：违反「构造零 IO」惯例（MCP tools 懒初始化先例、M3 提案「惰性加载：构造零 IO，首次 embed 才加载模型」），`Reranker(settings)` 在无数据查询路径上也会被创建 |
| **默认 scorer 由工厂 `_ctor` 登记惰性 `scorer_factory`（显式注入优先）** | 1 处 + 新适配层文件 | **采用**：调用点零改动；构造零 IO；直接构造 `CrossEncoderReranker(rc)` 不变；首次 rerank 在 `_ensure_scorer()` 触发加载，依赖未装 / `rerank.model` 缺失 / 加载失败 → `RuntimeError`（带 `pip install -e ".[rerank]"` 提示）走 D6 通道①兜底，行为严格不劣于现状 |

**3. 如何通过测试、指标或用户流程确认它确实有效？**

- **离线确定性主证据**：新单测以注入的 fake `scorer_factory` 锁定惰性装配语义（构造零 IO、首次 rerank 构建与加载、显式注入优先、加载失败走 D6 通道①并保留诊断信息）；既有 `test_cross_encoder_reranker.py` / `test_reranker_fallback.py` / `test_query_entry.py` 语义全部不破坏。
- **门控真实冒烟**：`RAG_RERANKER_INTEGRATION=1` 下真模型加载 + 英文相关性排序 sanity 断言（query 命中文本得分 > 无关文本）+ `Reranker(settings).rerank` 端到端 `fallback=false`，并记录 top_m=30 CPU 单次精排延迟。
- **用户流程**：`pip install -e ".[rerank]"` 后 `python scripts/query.py --query "..." --verbose` 的 rerank 段显示 `backend=cross_encoder` 且无 `fallback`；未安装依赖时（干净环境或屏蔽导入的隔离复验）输出与现状完全一致；已装依赖状态下全量离线禁网测试依然 100% 全绿。

### 1.3 背景与现状

- **B7.8 契约（不得破坏）**：`CrossEncoderReranker` 的 `scorer` 协议为 `(query, texts) -> scores`（越高越相关）；推理期 scorer 异常 → 返回截断后原序 + `last_fallback=True`（不抛错）；`top_m` 截断合法（长度 ≤ 输入）；显式 `kwargs["scorer"]` 可在单次 rerank 临时覆盖构造时注入；直接构造 `CrossEncoderReranker(rc)` 且无 `scorer` / `scorer_factory` 时在 rerank 抛 `RuntimeError`（`test_no_scorer_raises_on_rerank`）。本规格的全部改动必须在此契约内进行。
- **D6 契约（不得破坏）**：backend `rerank` 抛异常 → Core 层走回退通道①，返回完整输入原序原分 + `last_fallback=True` + `last_error` 与 trace `details["error"]` 记录异常详情；后端内部回退（通道②）标记透传；每次调用重置。
- **现状时序（必须保持）**：`_ensure_scorer()` 的 `RuntimeError` 发生在 `cross_encoder_reranker.py:71`（内部 `try` 之前），走 **D6 回退通道①**（异常原文写入 `last_error`、warning 日志与 trace `details["error"]`，返回完整输入）；`scorer(query, texts)` 发生在 `try/except` 之内，异常走 **D6 回退通道②**（Core 层只记 `internal fallback`，不写 trace `error`，返回 `truncated`）。本规格严格维持该划分：缺依赖 / `rerank.model` 缺失 / 模型加载失败在 `_ensure_scorer()` 抛出（走通道①以保留 `pip install` / `HF_ENDPOINT` 修复指引），模型就绪后的推理期异常仍走通道②。
- **既有先例对齐**：可选依赖降级对齐 RagasEvaluator / M3 提案口径（未装依赖构造成功、使用时抛带 `pip install` 提示的可读错误）；惰性加载对齐 MCP tools 懒初始化先例；门控冒烟对齐 `RAG_LLM_INTEGRATION` / `RAG_AGENTIC_INTEGRATION` / `RAG_PAGEINDEX_INTEGRATION` 先例。
- **DEV_SPEC 9.5 纪律（继承）**：cross-encoder 分数是未校准 logits，`agentic_search.trigger_score_threshold: 0.5`（`config/settings.yaml:104`）仅是占位（且当前 `HybridSearch` 的 `AgenticTrigger` 判定使用融合后、rerank 前的分数，本规格启用 cross-encoder 不改变既有触发行为）；阈值校准必须用独立校准集、禁止用评估 golden set 调参——本规格只让打分真实发生，**不做阈值校准**（见 §2.2）。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| libs/reranker | CrossEncoderScorer 适配层 | 承诺（CER1） | 新增 `src/libs/reranker/cross_encoder_scorer.py`：包装 `sentence_transformers.CrossEncoder` 为 B7.8 `Scorer` 协议（`(query, texts) -> list[float]`）；构造零 IO，提供 `ensure_loaded()` 供惰性加载（`__call__` 亦可自动触发）；模型构建过程经注入点替换（离线测试用 fake）；`model_name` 缺失 / 依赖缺失 / 模型加载失败抛带 `pip install -e ".[rerank]"` 提示的可读 `RuntimeError` |
| libs/reranker | 工厂默认惰性装配 | 承诺（CER2） | 工厂 `_ctor`（`register_cross_encoder_provider`）未收到显式 `scorer` / `scorer_factory` 时登记默认惰性 `scorer_factory`（构造零 IO，首次 `_ensure_scorer` 才构建并触发加载，加载失败抛 `RuntimeError` 走 D6 通道①；直接构造 `CrossEncoderReranker(rc)` 保持原样抛错）；`rerank.model` 传给 CrossEncoder、`rerank.extra.device` / `rerank.extra.batch_size` 可选透传；显式注入恒优先，既有测试语义不变 |
| 打包与文档 | 可选依赖登记 + 使用文档 | 承诺（CER3） | `pyproject.toml` 新增可选依赖组 `rerank = ["sentence-transformers>=3.0"]`（不进主依赖、不进 CI、`requirements.txt` 不变）；`USAGE_GUIDE.md` 新增第 13 章「本地 Cross-Encoder 精排」（安装命令与 Linux CPU wheel 提示、首次下载与 `HF_ENDPOINT` 镜像、离线缓存语义、`rerank.extra.device`、默认模型英文适用范围与中文换模指引、关闭方式 `backend: none`） |
| 门控冒烟 | 真实模型集成验证 | 门控（CER4） | 新增 `tests/e2e/test_cross_encoder_integration.py`（默认 skip，`RAG_RERANKER_INTEGRATION=1` 触发）：真模型加载与缓存、英文相关性排序 sanity、端到端 `fallback=false`、CPU 精排延迟实测记录，并验证已装依赖下离线禁网全套件全绿与未装依赖隔离回退一致 |

### 2.2 Out of Scope（明确不做）

- **触发阈值校准**（`agentic_search.trigger_score_threshold: 0.5` 占位值）：DEV_SPEC 9.5 纪律要求在独立校准集上标定、禁止用评估 golden set 调参；本规格不碰。校准需求若出现，走 backlog 提案。
- **精排质量对比评估**（cross_encoder vs 融合排名 vs LLM rerank 的 RAGAS 对照）：属评估体系扩展，非本规格；门控冒烟只做排序 sanity 不做质量结论。
- **LLM reranker（B7.7）的任何改动**。
- **模型选型对比 / 更换 / 微调**（bge-reranker 等）：`ms-marco-MiniLM-L-6-v2` 为既定配置，本规格只让它跑起来；换模型 = 改配置，无须改代码。
- **评分缓存、批处理调度、GPU 自动选择调优**：`extra.device` 透传给 CrossEncoder 即止；性能优化留待真实延迟数据说话。
- **MCP / API / Chat 装配点改动**：默认惰性装配使其全部零改动；任何一层出现「需要不同模型/后端」的新需求时另起提案。
- **`sentence-transformers` 进主依赖或 CI**：CI 保持禁网且不装该组；真实模型只在门控冒烟中出现。
- **改 `config/settings.yaml` 现值**：backend / model / top_m 配置不变；本规格交付后这些配置首次真实生效。

## 3. 技术选型与模块设计

### 3.1 scorer 适配层（CER1）

```
src/libs/reranker/cross_encoder_scorer.py（新增）

class CrossEncoderScorer:
    """包装 sentence_transformers.CrossEncoder 为 B7.8 Scorer 协议。"""

    def __init__(
        self,
        model_name: str | None,
        *,
        loader: Callable[[str], ScorerLike] | None = None,   # 注入点：离线测试替换
        device: str | None = None,                            # 透传 CrossEncoder(device=...)
        batch_size: int = 32,                                 # 透传 predict(batch_size=...)
    ) -> None: ...          # 构造零 IO：只存参数，不 import、不加载

    def ensure_loaded(self) -> ScorerLike:
        ...                   # 首次调用惰性校验 model_name + import + 加载并缓存；失败抛 RuntimeError

    def __call__(self, query: str, texts: Sequence[str]) -> list[float]:
        ...                   # 内部调用 ensure_loaded() 后执行 predict
```

- **`Scorer` 协议不变**：`(query, texts) -> scores`，float 化由 backend 既有逻辑处理；空 `texts` 由 backend 在上游截断层挡掉（len==0 早退），适配层不重复处理。
- **`loader` 注入点与 `ensure_loaded()`**：默认实现为「校验 `model_name` 非空 → `from sentence_transformers import CrossEncoder` → `CrossEncoder(model_name, device=device)` → 返回具备 `.predict(pairs) -> scores` 的对象」；单测注入 fake loader 零网络零 torch。暴露 `ensure_loaded()` 供工厂默认 `scorer_factory` 在 `_ensure_scorer()` 阶段触发加载，确保加载失败走 D6 通道①而非被 `rerank()` 内部 `try/except` 吞掉。
- **`model_name` 缺失契约**：`RerankConfig.model` 在 `src/core/settings.py:142` 默认为 `None`。若 `model_name` 为 `None` 或纯空白字符串，`ensure_loaded()` 抛出可读 `RuntimeError`（提示在 `config/settings.yaml` 配置 `rerank.model`），不把 `None` 透传给上游 `CrossEncoder`。
- **失败语义**：`ImportError`（未安装）、`model_name` 缺失、模型下载/加载异常，统一包装为带修复指引的 `RuntimeError`（依赖缺失与下载异常分别含 `pip install -e ".[rerank]"` 与 `HF_ENDPOINT` 镜像提示）；经 `_ensure_scorer()` 抛出后由 Core `Reranker` **D6 通道①** 捕获（`last_error`、warning 日志与 trace `details["error"]` 完整保留指引）；模型已加载后的推理期异常由 backend `try/except` 走 **B7.8 / D6 通道②** 兜底——**适配层自己不吞异常、不做重试**。
- **`CrossEncoder.predict` 输入形态**：按上游签名传 `(query, doc)` 对列表；返回分数序列与输入等长（长度不符属上游契约破坏，交由 backend 既有「长度不符 → fallback」分支处理，适配层不静默补齐）。

### 3.2 工厂默认惰性装配（CER2）

改动集中在 `src/libs/reranker/cross_encoder_reranker.py`：

```
_ctor（register_cross_encoder_provider，现签名不变，新增可选 kwarg）
    scorer=None, scorer_factory=None
    if scorer is None and scorer_factory is None:
        scorer_factory = _make_default_scorer_factory(rc)   # 仅登记闭包，零 IO
    └─ CrossEncoderReranker(rc, scorer=scorer, scorer_factory=scorer_factory, **kwargs)

CrossEncoderReranker
    __init__: self._scorer = scorer; self._scorer_factory = scorer_factory   # 构造零 IO
    _ensure_scorer():  # 位于 rerank() 内部 try 之前 → 抛错走 D6 通道①
        self._scorer（显式注入或已缓存）is not None → 直接返回（现状，不变）
        self._scorer_factory is not None → 调用工厂构建并触发加载，成功后缓存至 self._scorer 并返回
        否则 → 维持现状抛 RuntimeError("CrossEncoderReranker requires a 'scorer' callable...")
```

- **默认工厂归属与三段优先级**：
  - 优先级：显式 `scorer`（构造或单次 `rerank` kwargs，现状不变）> 显式注入的 `scorer_factory`（离线测试锁语义用）> 工厂 `_ctor` 挂载的默认惰性 `scorer_factory`（生产路径，构造 `CrossEncoderScorer(rc.model, **extra 透传)` 并调用 `ensure_loaded()`）。
  - **直接构造兼容**：直接调用 `CrossEncoderReranker(rc)`（不经 `RerankerFactory` 且不传 `scorer` / `scorer_factory`）时 `self._scorer_factory is None`，`_ensure_scorer()` 保持 B7.8 原样抛 `RuntimeError(...scorer...)`——既有 `test_no_scorer_raises_on_rerank` 在装依赖前后均零改动通过；而经 `RerankerFactory.create(settings)` 构造时依然零 IO（`test_factory_without_scorer_still_creates` 零改动通过），首次 `rerank` 才触发加载并缓存。
  - **失败通道一致**：`scorer_factory()` 与 `ensure_loaded()` 在 `_ensure_scorer()`（`cross_encoder_reranker.py:71`，`try` 之前）执行，失败不写入 `self._scorer` 且直接冒出 `RuntimeError` 进入 D6 通道①，保证 `pip install -e ".[rerank]"` 修复指引不丢失。
- **`extra` 透传**：`rerank.extra` 中识别 `device`（str | None）与 `batch_size`（正 int，默认 32）传给适配层；未知键忽略（`extra` 现有语义即自由 dict）。不新增 `RerankConfig` 字段——`backend/model/top_m/extra` 四字段已覆盖需要。
- **装配点零改动确认**：`query_knowledge_hub.py`（MCP + Playground）/ `query_service.py`（Chat）/ `scripts/query.py`（CLI）三处生产装配点均经 `Reranker(settings)`，不修改；`scripts/query.py` D7 记录的「cross_encoder 无 scorer 时构造合法、rerank 期异常由 fallback 回退」行为在依赖缺失时原样保留。
- **红线核对**：本改动不触碰 `sirchmunk` import、MCP stdout 纪律（reranker 在 QueryEngine 进程内，不经 stdout）与 `llm_fallback` 禁令；`check_redlines.py` 预期零违规。

### 3.3 可选依赖与配置（CER3）

- **pyproject**：`[project.optional-dependencies]` 新增 `rerank = ["sentence-transformers>=3.0"]`，注释说明 torch 连带体积与「不装则 cross_encoder 后端回退融合排名」；主 `dependencies`、`dev` 组与 `requirements.txt` 均不动（对齐 `ingest-formats` 先例；CI 禁网环境不装 torch，既有测试全部走 fake）。
- **USAGE_GUIDE（新增第 13 章）**：在 `docs/reference/USAGE_GUIDE.md` 追加第 13 章「本地 Cross-Encoder 精排」（对齐第 11 章 `mode_router` / 第 12 章 `PageIndex`，同步更新文档顶部目录），涵盖：
  - 安装命令：`pip install -e ".[rerank]"`（注明 Linux 纯 CPU 环境可先按 PyTorch 官方 CPU 源安装 `torch`，避免默认拉取数 GB CUDA wheel）；
  - 首次使用自动从 HF 下载模型（约 90 MB，国内可设 `HF_ENDPOINT` 镜像），下载后完全离线；
  - `rerank.extra.device` 指定 CPU/MPS 与 `rerank.extra.batch_size` 示例；
  - **语言适用范围与中文换模指引**：注明默认模型 `cross-encoder/ms-marco-MiniLM-L-6-v2` 为英文专用（英文词表，纯中文语料大多汉字切分为 `[UNK]`），中文/多语料库应修改 `config/settings.yaml` 的 `rerank.model`（如 `BAAI/bge-reranker-v2-m3`），无需改代码；
  - 关闭方式（`rerank.backend: none` 或不安装 `[rerank]` 可选组）。
- **`config/settings.yaml` 不改**：`backend: cross_encoder` / `model: cross-encoder/ms-marco-MiniLM-L-6-v2` / `top_m: 30` 即交付形态；`extra` 示例键以注释形式补充。

### 3.4 已知代价与风险

1. **体积与安装成本**：`sentence-transformers` 连带 torch（CPU 版约 2 GB 磁盘；Linux 默认 CUDA wheel 更大，文档提供 CPU 源安装提示）。可选项不装不占；装了之后 `cross_encoder` 才是真实路径。
2. **首次使用延迟**：模型加载（秒级）+ 首次下载（网络）；发生在首次 rerank 而非进程启动，首次查询可感知变慢——文档写明。
3. **CPU 精排延迟**：top_m=30、MiniLM 6 层，单次预测预计百毫秒量级；CER4 实测记录，若超预期（>1 s）再议降 top_m 或换小模型，不在本规格内预防性优化。
4. **行为可见变化**：装依赖后查询结果顺序可能改变（精排生效的本意）；对既有用户这是「配置首次兑现」而非回归，验收清单单列一条。
5. **Windows/macOS device 探测**：默认 None 交由上游自动选择；`extra.device` 提供显式覆盖逃生口，不做平台探测逻辑。
6. **默认模型的语言适用范围**：`cross-encoder/ms-marco-MiniLM-L-6-v2` 基于英文 MS MARCO 与英文 WordPiece 词表训练，仅保证英文相关性排序；本仓包含中文语料与查询（如 `tests/fixtures/golden_test_set.json`），对纯中文文本打分区分度有限甚至可能弱于融合原序。换用多语模型属配置切换（见 §2.2 Out of Scope），CER3 在 USAGE_GUIDE 中显式提醒。

## 4. 测试方案

- **离线确定性测试**（进 `pytest tests`，零网络零真实模型零 torch）：
  - `tests/unit/test_cross_encoder_scorer.py`（新增）：fake `loader` 注入——构造零 IO（不 import `sentence_transformers`、不调 loader）、`ensure_loaded()` / 首次 `__call__` 才构建且只构建一次（二次调用复用缓存）、`device`/`batch_size` 透传断言、`model_name` 为 `None` 或空白时抛可读 `RuntimeError`、loader 抛 `ImportError`/任意异常时包装为带 `pip install -e ".[rerank]"` / `HF_ENDPOINT` 提示的 `RuntimeError`、返回分数原样透传。
  - `tests/unit/test_cross_encoder_reranker.py`（新增，不改既有用例）：默认装配三段优先级（显式 `scorer` > 显式 `scorer_factory` > 工厂 `_ctor` 挂载的默认惰性工厂，工厂只调一次并缓存）；直接构造 `CrossEncoderReranker(rc)` 仍抛 `RuntimeError(match="scorer")`；`scorer_factory` / 加载失败在 `_ensure_scorer()` 抛 `RuntimeError`（配合 Core `Reranker` 验证走 D6 通道①：`last_fallback=True` 且 `last_error` 与 trace `details["error"]` 保留 `pip install -e ".[rerank]"` 提示）；模型加载后推理期抛错仍走 B7.8 内部回退（通道②）。
  - 既有测试语义（不得破坏）：`test_cross_encoder_reranker.py` 的 scorer 注入契约 / `test_no_scorer_raises_on_rerank` / `test_factory_without_scorer_still_creates` / `top_m` 截断 / scorer 异常内部回退 / 平分稳定排序、`test_reranker_fallback.py` 的双通道回退与标记透传、`test_query_entry.py` 的 rerank fallback 路径与退出码矩阵、`test_hybrid_search.py` 的 rerank trace 阶段断言——全部不变；全量离线 `pytest tests -q`、CI 禁网等价命令、`python scripts/check_redlines.py` 全绿。
- **真实依赖冒烟（门控）**：`RAG_RERANKER_INTEGRATION=1 pytest tests/e2e/test_cross_encoder_integration.py -v`（新增文件，默认 skip）：真模型（本地缓存命中则零下载）加载成功；相关性 sanity——默认 `ms-marco-MiniLM-L-6-v2` 选用稳定的**英文**样例验证相关文本得分 > 无关文本（该模型为英文词表，不对纯中文强制断言；切换多语模型时可按需扩展）；`Reranker(settings)`（真配置 tmp 化）端到端 rerank 后 `fallback=false`、trace `rerank` 阶段 `backend=cross_encoder`；记录 top_m=30 CPU 单次精排耗时到 §5 进度备注。不进 `pytest tests`。
- **真实用户流程与环境共存验收**：
  - 装依赖后 `python scripts/query.py --query ... --verbose` rerank 段无 fallback（记录于 §5）；
  - **已装依赖离线共存**：在本机 `.venv` 已安装 `[rerank]` 且存在本地模型缓存的情况下，复跑 CI 禁网等价命令（`NO_PROXY='*' pytest tests -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1`）依然 100% 全绿，确认离线测试零误触真模型或联网；
  - **未装依赖回退复验**：在未装 `[rerank]` 的干净环境或屏蔽 `sentence_transformers` 导入的隔离检查中复跑，确认构造合法、rerank 走 D6 通道①回退且错误信息含 `pip install -e ".[rerank]"`（不要求在已装 ~2 GB 依赖的主 `.venv` 中卸载重装）。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| CER1 | `src/libs/reranker/cross_encoder_scorer.py` 适配层（§3.1：惰性加载、`ensure_loaded()`、`loader` 注入点、`model_name` 缺失校验、device/batch_size 透传、失败包装为可读 RuntimeError）+ `test_cross_encoder_scorer.py` | 新单测全绿（构造零 IO / 惰性单次构建 / `model_name` 缺失报错 / 透传 / 失败包装含 `pip install -e ".[rerank]"` 提示）；适配层不 import `sentence_transformers` 于模块顶层（供 CI 无 torch 环境导入 `libs.reranker` 包不炸）；既有 reranker 单测全绿 | [x] 2026-11-27 完成：适配层 + 14 个新单测全绿（Round 0 零修复）；`test_cross_encoder_reranker.py` / `test_reranker_fallback.py` 既有用例零改动全绿（46 passed）；`check_redlines.py` 0 违规；无 torch 环境导入 `libs.reranker` 实证通过 |
| CER2 | 工厂 `_ctor` 默认惰性装配（§3.2：默认 `scorer_factory` 挂于 `_ctor`、三段优先级、`rerank.model` 缺失与加载失败走 D6 通道①、`extra.device` / `extra.batch_size` 透传、缓存）+ 既有文件新增用例 | 显式注入优先与直接构造 `CrossEncoderReranker(rc)` 抛错语义不变（`test_cross_encoder_reranker.py` 既有用例零改动全绿）；新用例锁定三段优先级、通道①诊断信息保留与通道②推理回退；`test_reranker_fallback.py` / `test_query_entry.py` / `test_hybrid_search.py` 全绿；装配点三处源码零 diff | [x] 2026-11-27 完成：`_ctor` 默认惰性工厂 + `_ensure_scorer` 三段优先级落地；新增 7 用例（构造零 IO / 显式 scorer 与显式 factory 优先级 / 加载失败通道①诊断保留 / extra device+batch_size 透传 / 非法 batch_size 回退 / 推理失败通道②）全绿；相关 5 套件 101 passed（Round 1 修复 1 处新用例自身设计错误，既有用例零改动）；`check_redlines.py` 0 违规；装配点三处零 diff |
| CER3 | `pyproject.toml` 可选依赖组 `rerank` + `config/settings.yaml` 注释示例 + `USAGE_GUIDE.md` 第 13 章「本地 Cross-Encoder 精排」 + README 特性句提及（如有必要） | `pip install -e ".[rerank]"` 可安装成功（本机实测记录）；主依赖、`dev` 组与 `requirements.txt` diff 为零；USAGE_GUIDE 第 13 章及目录已同步，含安装（含 Linux CPU wheel 提示）/ 下载与 `HF_ENDPOINT` 镜像 / device / 默认模型英文适用范围与中文换模指引 / 关闭方式；MOC / README 导航已随立项同步（见 §7） | [x] 2026-11-27 完成：`rerank = ["sentence-transformers>=3.0"]` 已入 pyproject（主依赖 / dev / requirements.txt 零 diff）；`pip install -e ".[rerank]"` 本机安装成功（torch 2.15.0 / sentence-transformers 6.3.0 / transformers 5.24.1）；`config/settings.yaml` rerank 节补 extra 注释示例（仅注释不改值）；USAGE_GUIDE 第 13 章 + 目录同步；README Cross-Encoder 特性行更新并链接第 13 章；`test_docs_links.py` / `test_spec_structure.py` 全绿 |
| CER4 | （门控）`tests/e2e/test_cross_encoder_integration.py` + `RAG_RERANKER_INTEGRATION=1` 本机执行 + 延迟实测与共存复验记录 | 门控默认 skip（离线全套件不受影响）；门控开启时 §4 英文 sanity 与端到端断言全过；`python scripts/query.py --verbose` rerank 段 `backend=cross_encoder` 无 fallback（记录于进度备注）；CPU top_m=30 单次精排延迟数值写入进度备注；已装 `[rerank]` 的本机 `.venv` 复跑 CI 禁网命令全绿；未装依赖隔离复验走 D6 通道①并保留安装指引 | [x] 2026-11-27 完成：门控测试 3 用例（真模型加载与缓存 / 英文 sanity / 端到端 fallback=false + trace `backend=cross_encoder`）默认 skip、门控开启全过；隔离复验（屏蔽导入）确认未装依赖行为与立项前一致（通道① + `pip install -e ".[rerank]"` 指引保留）；已装依赖下 CI 禁网等价命令 `PYTEST_EXIT=0` 全绿；延迟与用户流程记录见 §5.1 |

依赖顺序：CER1 → CER2（backend 引用适配层）；CER3 可与 CER1/CER2 同 PR；CER4 依赖 CER1–CER3 合入且本机完成模型下载。

### 5.1 进度备注

- **安装实测（CER3）**：`pip install -e ".[rerank]"` 本机安装成功（torch 2.15.0 / sentence-transformers 6.3.0 / transformers 5.24.1，CPU 版）。
- **门控冒烟（CER4，2026-11-27 本机 macOS CPU）**：3 passed。
  - 模型加载：首次 `ensure_loaded()` 46.8 s（含 HF 下载），二次调用命中缓存（同对象）；已缓存后冷进程加载 5.9 s。
  - 英文 sanity：相关句 8.2457 / 相关变体 −1.7965 / 无关句 −11.2214（相关 > 无关断言通过）。
  - 端到端：`Reranker(settings)`（工厂默认惰性装配）top_m=30 真实精排 `fallback=false`，trace `rerank` 阶段 `backend=cross_encoder` 且无 `error`；最相关候选从第 15 位升至第 1。
- **延迟实测（top_m=30，macOS CPU）**：热模型单次 predict 11–12 ms（首次预热调用 234 ms）；含首次模型加载的整次 rerank 调用 3124.8 ms（门控测试） / 4485.2 ms（CLI 首查，含加载）。远低于 §3.4 预设的 >1 s 再议阈值，无需降 top_m。
- **用户流程**：`python scripts/query.py --query ... --verbose` rerank 段 `backend=cross_encoder, fallback=false`（2026-11-27 实测）。
- **已装依赖离线共存**：CI 禁网等价命令（`NO_PROXY='*' pytest tests -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1`）复跑 `PYTEST_EXIT=0` 全绿（门控用例默认 skip）。
- **未装依赖隔离复验**：屏蔽 `sentence_transformers` 导入后构造合法、rerank 走 D6 通道①、`last_error` 与 trace `details["error"]` 均含 `pip install -e ".[rerank]"` 指引（未卸载主 venv 依赖，符合 §4 口径）。
- **附带发现与修复（不属 CER 交付物但为共存验收所必需）**：
  1. 安装 `[rerank]` 后，`langchain_text_splitters` 1.1.2 在自身模块级**传递性 import `sentence_transformers`（连带 torch）**（`langchain_text_splitters/__init__.py:39` → `sentence_transformers.py:11`，受 try/except 保护，未装时静默跳过）——任何经 `core.query_engine` / `ingestion` 链路的进程在已装环境下都会加载 torch（首次 import 数秒级）。功能无损、属可选依赖的已知代价，记入本备注；`test_constructor_is_zero_io` 原以「`sys.modules` 无该键」判「构造未 import」，在该环境下不再成立（与构造无关的传递导入先污染了 sys.modules），已改为 meta_path 屏蔽导入的强证明（构造若触发 import 即抛 ImportError），断言语义（构造零 IO）不变。
  2. `tests/unit/test_loader_pdf_contract.py` 两条「未装 markitdown」用例自 2026-10-07 本机 venv 装 `markitdown 0.1.8` 起即无法通过（`delitem(sys.modules)` 后重新 import 成功，模拟失效）——先于本规格存在的环境漂移，非 CER 引入；已把「模拟缺失」机制改为 meta_path 屏蔽导入（清缓存 + 屏蔽），测试语义（无 markitdown 时 auto 回退 pypdf / engine=markitdown 抛错）不变，两种环境（装/未装）均确定性通过。

## 6. 验收清单

- [x] 装依赖后精排真实生效：`scripts/query.py --verbose` rerank 段 `backend=cross_encoder`、无 fallback；trace `rerank` 阶段 `fallback=false`（2026-11-27 实测，见 §5.1）
- [x] 未装依赖时（干净环境或屏蔽导入的隔离复验）行为与立项前一致：构造合法、rerank 走 D6 通道①回退（完整原序 + `last_fallback=True` + `last_error` / trace `error` 含 `pip install -e ".[rerank]"` 提示）、无 crash（屏蔽导入隔离复验通过）
- [x] 显式 scorer 注入契约与既有测试语义不变（`test_cross_encoder_reranker.py` 含 `test_no_scorer_raises_on_rerank` 等既有用例零改动全绿）
- [x] 全量离线 pytest、CI 禁网等价命令（已装 `[rerank]` 真实环境 `PYTEST_EXIT=0`；未装环境以屏蔽导入的隔离复验覆盖其行为面，未做卸载重装）、`check_redlines.py`、`test_docs_links.py`、`test_spec_structure.py` 全绿
- [x] 门控冒烟通过并记录（真模型英文排序 sanity + 端到端 fallback=false + CPU 精排延迟实测值，见 §5.1）
- [x] 回写完成（§7；USAGE_GUIDE 第 13 章 / MOC / README）

## 7. 实现合入后的回写清单

- `docs/MOC.md`（specs 分区条目 + 谱系表行）与根目录 `README.md`（specs 导航行）：**随本规格立项同步完成**，不属后续回写。
- `docs/reference/USAGE_GUIDE.md`：第 13 章「本地 Cross-Encoder 精排」及顶部目录（CER3 合入时）。
- `config/settings.yaml`：`rerank:` 节补 `extra` 注释示例（CER3 合入时，仅注释不改值）。
- 冻结时：按 docs-governance 冻结清单办理（编号、冻结记录头、MOC / README 同步；AGENTS.md 按「只进不出」规则判断是否新增条目——预计需要：cross_encoder 默认惰性装配与失败回退语义对改 reranker 链路者构成必读契约）。
- DEV_SPEC 不回写：D6 / B7.8 契约不变，本规格是其 scorer 注入契约的生产侧补全；`RerankConfig` 无字段变化。

## 8. 隐私注记

不适用外发第三方服务：模型推理在本地进程内完成，查询文本与文档内容不离开本机。首次使用从 Hugging Face 下载公开模型权重（约 90 MB）属公开资源拉取，不携带任何用户数据；模型缓存于 HF 标准缓存目录，与项目数据目录（`data/`）无关。不新增路由、不落库个人内容。
