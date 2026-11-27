<!-- 开发中规格：本地 Cross-Encoder 精排生效（CER 系列，2026-11-27 立项）。让 settings.yaml 已配置的 rerank.backend: cross_encoder（cross-encoder/ms-marco-MiniLM-L-6-v2）从「配置空转、每次回退」变为真实生效：补 sentence-transformers CrossEncoder scorer 适配层 + 工厂默认惰性装配 + 可选依赖登记 + 门控真实模型冒烟。契约依据：01_DEV_SPEC_frozen.md D6（Core 层编排与回退）/ B7.8（CrossEncoder scorer 注入契约）。不改既有测试语义、不改 fallback 契约。 -->

# 本地 Cross-Encoder 精排生效规格 (CROSS_ENCODER_RERANKER_SPEC)

> 版本：0.1 — v0.1 立项（跳过提案：用户在会话中直接拍板「直接立项 spec」，决策依据见第 1、2 章，无 backlog 草案阶段）
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
| 定位是什么？ | 补齐「配置承诺与运行时行为」之间的缺口：① `CrossEncoderScorer` 适配层（包装 `sentence_transformers.CrossEncoder`，惰性加载、构建过程可注入）；② cross_encoder backend 在未注入 scorer 时的默认惰性装配（显式注入优先，测试与既有契约不变）；③ `sentence-transformers` 进可选依赖组（不进主依赖，未安装时行为与现状一致）；④ 门控真实模型冒烟。不改 D6 回退契约、不改既有测试语义 |
| 实施范围 | CER1–CER3 为承诺任务；CER4（真实模型冒烟）为门控任务（需下载模型并本机执行） |
| 主要代价 | `sentence-transformers` 连带 torch（约 2 GB 磁盘占用，CPU 推理即可）；模型首次使用从 HF 下载（约 90 MB，之后离线缓存）；默认 CPU 推理 top_m=30 候选的单次精排延迟需在冒烟中实测记录 |

### 1.2 决策依据（三问）

**1. 解决哪个已经观察到的问题？**

- **配置空转（2026-11-27 会话核查实录）**：`config/settings.yaml:121-124` 配置了 cross_encoder 后端与模型名，但全仓 `src/**/*.py` 中 `CrossEncoder` / `sentence_transformers` 零匹配——`src/libs/reranker/cross_encoder_reranker.py:44-51` 的 `_ensure_scorer()` 在 scorer 为 None 时抛 `RuntimeError`（该调用点在内部 try 之外，异常直接冒出 backend），`src/core/query_engine/reranker.py:109-113` 捕获后走 D6 回退（输入原序 + `last_fallback=True`）。即：**backend 配置为 cross_encoder 的每一次查询，精排都实际未执行**。装配点共四处（`src/mcp_server/tools/query_knowledge_hub.py:115`、`src/observability/dashboard/services/query_service.py:109`、`scripts/query.py:218`、Chat 链路同源），全部不传 scorer。
- **模型未就位**：`~/.cache/huggingface` 与 `~/.cache/torch/sentence_transformers` 均不存在；`sentence-transformers` 不在 requirements.txt / pyproject.toml / 主 venv 中。配置里的模型名当前是死数据。
- **trace 噪声**：每次查询 rerank 阶段 `fallback=true`、`last_error=RuntimeError`，检索 Trace 挖掘（retrieval-trace-mining 台账）等下游消费会被持续性假回退污染。

**2. 是否有更简单、改动更小的方案？**

| 方案 | 改动 | 结论 |
|------|------|------|
| **backend 改回 `none`** | 配置一行 | 否决：诚实但放弃精排能力；CrossEncoder 精排是 D6 / B7.8 的既定设计目标，`top_m` 截断、fallback 契约、trace 打点都已就位，只缺 scorer 一环 |
| **保持现状，只写文档说明** | 文档 | 否决：配置继续误导使用者；trace 假回退持续产生 |
| **在各装配点显式构建并注入 scorer** | 4 处调用点 + 新适配层 | 否决：装配点各自 import 重依赖、各自处理降级，重复且易漏；且 MCP tools / services 层的懒初始化路径会因此提前触模型 IO |
| **在 backend 构造函数内同步加载模型** | 1 处 | 否决：违反「构造零 IO」惯例（MCP tools 懒初始化先例、M3 提案「惰性加载：构造零 IO，首次 embed 才加载模型」），`Reranker(settings)` 在无数据查询路径上也会被创建 |
| **默认 scorer 由 backend 惰性构建（显式注入优先）** | 1 处 + 新适配层文件 | **采用**：调用点零改动；构造零 IO；首次 rerank 才加载模型；依赖未装/加载失败 → RuntimeError（带 pip install 提示）→ 既有 D6 回退语义原样兜底，行为严格不劣于现状 |

**3. 如何通过测试、指标或用户流程确认它确实有效？**

- **离线确定性主证据**：新单测以注入的 fake `scorer_factory` 锁定惰性装配语义（构造零 IO、首次 rerank 构建、显式注入优先、构建失败回退）；既有 `test_cross_encoder_reranker.py` / `test_reranker_fallback.py` / `test_query_entry.py` 语义全部不破坏。
- **门控真实冒烟**：`RAG_RERANKER_INTEGRATION=1` 下真模型加载 + 相关性排序 sanity 断言（query 命中文本得分 > 无关文本）+ `Reranker(settings).rerank` 端到端 `fallback=false`，并记录 top_m=30 CPU 单次精排延迟。
- **用户流程**：`pip install -e ".[rerank]"` 后 `python scripts/query.py --query "..." --verbose` 的 rerank 段显示 `backend=cross_encoder` 且无 `fallback`；未安装依赖时输出与现状完全一致。

### 1.3 背景与现状

- **B7.8 契约（不得破坏）**：`CrossEncoderReranker` 的 `scorer` 协议为 `(query, texts) -> scores`（越高越相关）；scorer 异常 → 返回截断后原序 + `last_fallback=True`（不抛错）；`top_m` 截断合法（长度 ≤ 输入）；显式 `kwargs["scorer"]` 可在单次 rerank 临时覆盖构造时注入。本规格的全部改动必须在此契约内进行。
- **D6 契约（不得破坏）**：backend `rerank` 抛异常 → Core 层返回输入原序原分 + `last_fallback=True` + `last_error` 记录；内部回退标记透传；每次调用重置。
- **现状时序**：`_ensure_scorer()` 的 `RuntimeError` 发生在 `cross_encoder_reranker.py:71`（内部 try 之前），因此走的是 D6 回退通道①（异常回退）而非通道②（内部回退）；两种通道对调用方可见结果一致（原序 + fallback 标记），本规格不改变这一划分。
- **既有先例对齐**：可选依赖降级对齐 RagasEvaluator / M3 提案口径（未装依赖构造成功、使用时抛带 `pip install` 提示的可读错误）；惰性加载对齐 MCP tools 懒初始化先例；门控冒烟对齐 `RAG_LLM_INTEGRATION` / `RAG_AGENTIC_INTEGRATION` / `RAG_PAGEINDEX_INTEGRATION` 先例。
- **DEV_SPEC 9.5 纪律（继承）**：cross-encoder 分数是未校准 logits，`retrieval.trigger_score_threshold: 0.5` 仅是占位；阈值校准必须用独立校准集、禁止用评估 golden set 调参——本规格只让打分真实发生，**不做阈值校准**（见 §2.2）。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| libs/reranker | CrossEncoderScorer 适配层 | 承诺（CER1） | 新增 `src/libs/reranker/cross_encoder_scorer.py`：包装 `sentence_transformers.CrossEncoder` 为 B7.8 `Scorer` 协议（`(query, texts) -> list[float]`）；构造零 IO，首次调用惰性加载模型；模型构建过程经注入点替换（离线测试用 fake）；依赖缺失 / 模型加载失败抛带 `pip install ".[rerank]"` 提示的可读 `RuntimeError` |
| libs/reranker | 工厂默认惰性装配 | 承诺（CER2） | cross_encoder backend ctor 未收到显式 scorer 时登记默认 scorer 工厂（惰性：登记零 IO，首次 `_ensure_scorer` 才构建）；`rerank.model` 传给 CrossEncoder、`rerank.extra.device` / `rerank.extra.batch_size` 可选透传；显式注入（构造 kwarg 或 rerank kwargs）恒优先，既有测试语义不变 |
| 打包与文档 | 可选依赖登记 + 使用文档 | 承诺（CER3） | pyproject 新增可选依赖组 `rerank = ["sentence-transformers>=3.0"]`（不进主依赖、不进 CI）；USAGE_GUIDE 补精排启用小节（安装命令、首次下载与 `HF_ENDPOINT` 镜像、离线缓存语义、关闭方式 `backend: none`） |
| 门控冒烟 | 真实模型集成验证 | 门控（CER4） | 新增 `tests/e2e/test_cross_encoder_integration.py`（默认 skip，`RAG_RERANKER_INTEGRATION=1` 触发）：真模型加载与缓存、相关性排序 sanity、端到端 `fallback=false`、CPU 精排延迟实测记录 |

### 2.2 Out of Scope（明确不做）

- **触发阈值校准**（`trigger_score_threshold: 0.5` 占位值）：DEV_SPEC 9.5 纪律要求在独立校准集上标定、禁止用评估 golden set 调参；本规格不碰。校准需求若出现，走 backlog 提案。
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
        model_name: str,
        *,
        loader: Callable[[str], ScorerLike] | None = None,   # 注入点：离线测试替换
        device: str | None = None,                            # 透传 CrossEncoder(device=...)
        batch_size: int = 32,                                 # 透传 predict(batch_size=...)
    ) -> None: ...          # 构造零 IO：只存参数，不 import、不加载

    def __call__(self, query: str, texts: Sequence[str]) -> list[float]:
        ...                   # 首次调用惰性 import + 加载；失败抛 RuntimeError（带 pip 提示）
```

- **`Scorer` 协议不变**：`(query, texts) -> scores`，float 化由 backend 既有逻辑处理；空 `texts` 由 backend 在上游截断层挡掉（len==0 早退），适配层不重复处理。
- **`loader` 注入点**：默认实现为「`from sentence_transformers import CrossEncoder` → `CrossEncoder(model_name, device=device)` → 返回具备 `.predict(pairs) -> scores` 的对象」；单测注入 fake loader 零网络零 torch。
- **失败语义**：`ImportError`（未安装）、模型下载/加载异常，统一包装为带修复指引的 `RuntimeError`（含 `pip install ".[rerank]"` 与 `HF_ENDPOINT` 镜像提示）；异常冒出 backend 后由 B7.8 内部契约（scorer 异常 → 原序 + fallback）或 D6 通道兜底——**适配层自己不吞异常、不做重试**。
- **`CrossEncoder.predict` 输入形态**：按上游签名传 `(query, doc)` 对列表；返回分数序列与输入等长（长度不符属上游契约破坏，交由 backend 既有「长度不符 → fallback」分支处理，适配层不静默补齐）。

### 3.2 工厂默认惰性装配（CER2）

改动集中在 `src/libs/reranker/cross_encoder_reranker.py`：

```
_ctor（现签名不变，新增可选 kwarg）
    scorer=None, scorer_factory=None
    └─ CrossEncoderReranker(rc, scorer=scorer, scorer_factory=scorer_factory, **kwargs)

CrossEncoderReranker
    __init__: self._scorer_factory = scorer_factory   # 登记零 IO
    _ensure_scorer():
        self._scorer（显式注入）is not None → 直接返回（现状，不变）
        self._scorer_factory is not None → 构建并缓存（测试注入点）
        否则 → 构建 CrossEncoderScorer(rerank_config.model, **extra 透传) 并缓存
```

- **三段优先级**：显式 scorer（构造或单次 rerank kwargs，现状不变）> 注入的 `scorer_factory`（离线测试锁语义用）> 默认 `CrossEncoderScorer`（生产路径）。缓存后整个进程只加载一次模型。
- **`extra` 透传**：`rerank.extra` 中识别 `device`（str | None）与 `batch_size`（正 int，默认 32）传给适配层；未知键忽略（`extra` 现有语义即自由 dict）。不新增 `RerankConfig` 字段——`backend/model/top_m/extra` 四字段已覆盖需要。
- **装配点零改动确认**：`query_knowledge_hub.py` / `query_service.py` / `scripts/query.py` / Chat 链路均经 `Reranker(settings)`，不修改；`scripts/query.py` D7 记录的「cross_encoder 无 scorer 时构造合法、rerank 期异常由 fallback 回退」行为在依赖缺失时原样保留。
- **红线核对**：本改动不触碰 `sirchmunk` import、MCP stdout 纪律（reranker 在 QueryEngine 进程内，不经 stdout）与 `llm_fallback` 禁令；`check_redlines.py` 预期零违规。

### 3.3 可选依赖与配置（CER3）

- **pyproject**：`[project.optional-dependencies]` 新增 `rerank = ["sentence-transformers>=3.0"]`，注释说明 torch 连带体积与「不装则 cross_encoder 后端回退融合排名」；主 `dependencies` 与 `dev` 组不动（CI 禁网环境不装 torch，既有测试全部走 fake）。
- **USAGE_GUIDE**：新增小节「本地 Cross-Encoder 精排」：安装命令、首次使用自动从 HF 下载模型（约 90 MB，国内可设 `HF_ENDPOINT` 镜像）、下载后完全离线、`rerank.extra.device` 指定 CPU/MPS、关闭方式（`rerank.backend: none` 或卸载依赖）。
- **`config/settings.yaml` 不改**：`backend: cross_encoder` / `model: cross-encoder/ms-marco-MiniLM-L-6-v2` / `top_m: 30` 即交付形态；`extra` 示例键以注释形式补充。

### 3.4 已知代价与风险

1. **体积与安装成本**：`sentence-transformers` 连带 torch（CPU 版约 2 GB 磁盘）。可选项不装不占；装了之后 `cross_encoder` 才是真实路径。
2. **首次使用延迟**：模型加载（秒级）+ 首次下载（网络）；发生在首次 rerank 而非进程启动，首次查询可感知变慢——文档写明。
3. **CPU 精排延迟**：top_m=30、MiniLM 6 层，单次预测预计百毫秒量级；CER4 实测记录，若超预期（>1 s）再议降 top_m 或换小模型，不在本规格内预防性优化。
4. **行为可见变化**：装依赖后查询结果顺序可能改变（精排生效的本意）；对既有用户这是「配置首次兑现」而非回归，验收清单单列一条。
5. **Windows/macOS device 探测**：默认 None 交由上游自动选择；`extra.device` 提供显式覆盖逃生口，不做平台探测逻辑。

## 4. 测试方案

- **离线确定性测试**（进 `pytest tests`，零网络零真实模型零 torch）：
  - `tests/unit/test_cross_encoder_scorer.py`（新增）：fake `loader` 注入——构造零 IO（不 import sentence_transformers、不调 loader）、首次 `__call__` 才构建且只构建一次（二次调用复用缓存）、`device`/`batch_size` 透传断言、loader 抛 `ImportError`/任意异常时包装为带 `pip install ".[rerank]"` 提示的 `RuntimeError`、返回分数原样透传。
  - `tests/unit/test_cross_encoder_reranker.py`（新增，不改既有用例）：默认装配三段优先级——显式 scorer 存在时不构建默认、`scorer_factory` 注入生效并缓存（工厂只调一次）、无注入时经 monkeypatch 的默认构建被调用、构建失败 → `last_fallback=True` 原序返回（B7.8 内部回退契约不破坏）。
  - 既有测试语义（不得破坏）：`test_cross_encoder_reranker.py` 的 scorer 注入契约 / `top_m` 截断 / scorer 异常内部回退 / 平分稳定排序、`test_reranker_fallback.py` 的双通道回退与标记透传、`test_query_entry.py` 的 rerank fallback 路径与退出码矩阵、`test_hybrid_search.py` 的 rerank trace 阶段断言——全部不变；全量离线 `pytest tests -q`、CI 禁网等价命令、`python scripts/check_redlines.py` 全绿。
- **真实依赖冒烟（门控）**：`RAG_RERANKER_INTEGRATION=1 pytest tests/e2e/test_cross_encoder_integration.py -v`（新增文件，默认 skip）：真模型（本地缓存命中则零下载）加载成功；相关性 sanity——同一 query 下相关文本得分 > 无关文本（选稳定的中英文样例各一组）；`Reranker(settings)`（真配置 tmp 化）端到端 rerank 后 `fallback=false`、trace `rerank` 阶段 `backend=cross_encoder`；记录 top_m=30 CPU 单次精排耗时到 §5 进度备注。不进 `pytest tests`。
- **真实用户流程验收**：装依赖后 `python scripts/query.py --query ... --verbose` rerank 段无 fallback（记录于 §5）；卸载依赖复跑确认回退行为与现状一致。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| CER1 | `src/libs/reranker/cross_encoder_scorer.py` 适配层（§3.1：惰性加载、`loader` 注入点、device/batch_size 透传、失败包装为可读 RuntimeError）+ `test_cross_encoder_scorer.py` | 新单测全绿（构造零 IO / 惰性单次构建 / 透传 / 失败包装）；适配层不 import sentence_transformers 于模块顶层（供 CI 无 torch 环境导入 `libs.reranker` 包不炸）；既有 reranker 单测全绿 | [ ] |
| CER2 | cross_encoder backend 默认惰性装配（§3.2：三段优先级、`scorer_factory` 注入点、`rerank.model` / `extra.device` / `extra.batch_size` 透传、进程内缓存）+ 既有文件新增用例 | 显式注入优先语义不变（既有用例零改动全绿）；新用例锁定三段优先级与构建失败回退；`test_reranker_fallback.py` / `test_query_entry.py` / `test_hybrid_search.py` 全绿；装配点四处源码零 diff | [ ] |
| CER3 | pyproject 可选依赖组 `rerank` + `config/settings.yaml` 注释示例 + USAGE_GUIDE「本地 Cross-Encoder 精排」小节 + README 特性句提及（如有必要） | `pip install -e ".[rerank]"` 可安装成功（本机实测记录）；主依赖与 dev 组 diff 为零；USAGE_GUIDE 小节含安装 / 下载 / 镜像 / device / 关闭方式；MOC / README 导航已随立项同步（见 §7） | [ ] |
| CER4 | （门控）`tests/e2e/test_cross_encoder_integration.py` + `RAG_RERANKER_INTEGRATION=1` 本机执行 + 延迟实测记录 | 门控默认 skip（离线全套件不受影响）；门控开启时 §4 冒烟断言全过；`python scripts/query.py --verbose` rerank 段 `backend=cross_encoder` 无 fallback（记录于进度备注）；CPU top_m=30 单次精排延迟数值写入进度备注 | [ ] |

依赖顺序：CER1 → CER2（backend 引用适配层）；CER3 可与 CER1/CER2 同 PR；CER4 依赖 CER1–CER3 合入且本机完成模型下载。

### 5.1 进度备注

- （待交付后回填）

## 6. 验收清单

- [ ] 装依赖后精排真实生效：`scripts/query.py --verbose` rerank 段 `backend=cross_encoder`、无 fallback；trace `rerank` 阶段 `fallback=false`
- [ ] 未装依赖时行为与立项前完全一致：构造合法、rerank 异常走 D6 回退（原序 + `last_fallback=True`）、无 crash
- [ ] 显式 scorer 注入契约与既有测试语义不变（`test_cross_encoder_reranker.py` 等既有用例零改动全绿）
- [ ] 全量离线 pytest、CI 禁网等价命令、`check_redlines.py`、`test_docs_links.py`、`test_spec_structure.py` 全绿
- [ ] 门控冒烟通过并记录（真模型排序 sanity + 端到端 fallback=false + CPU 精排延迟实测值）
- [ ] 回写完成（§7；USAGE_GUIDE / MOC / README）

## 7. 实现合入后的回写清单

- `docs/MOC.md`（specs 分区条目 + 谱系表行）与根目录 `README.md`（specs 导航行）：**随本规格立项同步完成**，不属后续回写。
- `docs/reference/USAGE_GUIDE.md`：精排启用小节（CER3 合入时）。
- `config/settings.yaml`：`rerank:` 节补 `extra` 注释示例（CER3 合入时，仅注释不改值）。
- 冻结时：按 docs-governance 冻结清单办理（编号、冻结记录头、MOC / README 同步；AGENTS.md 按「只进不出」规则判断是否新增条目——预计需要：cross_encoder 默认惰性装配与失败回退语义对改 reranker 链路者构成必读契约）。
- DEV_SPEC 不回写：D6 / B7.8 契约不变，本规格是其 scorer 注入契约的生产侧补全；`RerankConfig` 无字段变化。

## 8. 隐私注记

不适用外发第三方服务：模型推理在本地进程内完成，查询文本与文档内容不离开本机。首次使用从 Hugging Face 下载公开模型权重（约 90 MB）属公开资源拉取，不携带任何用户数据；模型缓存于 HF 标准缓存目录，与项目数据目录（`data/`）无关。不新增路由、不落库个人内容。
