# Sirchmunk 知识演化与知识簇复用开启提案（草案）

> 起源：从 [sirchmunk-extension-followups_backlog.md](sirchmunk-extension-followups_backlog.md) 拆出：第 1 类 #1「知识演化（evolver）」与第 2.2 节「知识簇复用」。拆分动因是 2026-11-15 会话内的源码复核：evolver 不必改上游源码，在 sidecar venv 内用 SDK 写一个启动器就能开启，台账与 [SX 规格](../frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md) 2.2 中「开启须改上游源码」的判断不准确。
>
> 状态：**草案，不进入排期**。三问中第 1 问只能部分回答（见第 4 章）：两项都是 sirchmunk 的头号卖点（README 标题「自进化搜索引擎」、徽标「知识聚类」「自进化知识库」），但本项目尚无依赖它们的用户流程；本提案的价值是给出一个零上游改动、可证伪的实验方案。
>
> 关联：[SIRCHMUNK_INTEGRATION_SPEC_frozen](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md)（第 4 章副作用实证、3.1 sidecar 架构决策）、[SIRCHMUNK_CAPABILITY_GAP_ANALYSIS](../analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md)（第 3 节演化可达性判断，勘误见本文第 1 章）、[USAGE_GUIDE](../reference/USAGE_GUIDE.md) 4.3（复用开启须知）。

---

## 1. 勘误：evolver 不需要改上游源码

台账原文：「`api/search.py` 与 CLI 均不传 `True`——HTTP/CLI 均不可达，开启必须改上游源码，同时违反本项目与上游双方纪律」。前半句（经 `sirchmunk serve` 不可达）成立，后半句不成立：

- 本项目红线只有两条：**主 venv 永不 `import sirchmunk`**；**不改上游源码**（[AGENTS.md](../../AGENTS.md)「Sirchmunk sidecar 进程隔离红线」、SX 规格文件头）。
- 在 sidecar 独立 venv 内运行一个调用 SDK 的启动器，两条都不碰：主项目照旧只经 `http_backend.py` 走 HTTP；上游一行不改。同类先例：`scripts/sirchmunk/preflight.sh` 已在 sidecar venv 内 `from sirchmunk...`，[check_redlines.py](../../scripts/check_redlines.py) 文件头注明其为合法白名单。
- 它触碰的是 S 系列 3.1「sidecar = 上游 `sirchmunk serve`」这一**架构决策**，按流程需立项变更，而非红线问题。

## 2. 现状事实（源码复核）

复核基线：本机 sirchmunk 检出 `2a9b98e`（Merge PR #186）。**注意**：`.venv-sirchmunk` 是对该检出的 editable 安装（`sirchmunk.__file__` 指向检出目录），而 S 系列 4.8 记录的锁定 commit 是 `b314e11`；两者在 `api/search.py`、`search.py` 上有差异（+78/-36 行），evolver 相关逻辑在两版中一致（构造参数、`step()` 调用点、`api/search.py` 均不传参）。立项时先把 sidecar 版本钉回明确 commit（见 KE0）。

### 2.1 知识簇复用：已接入，默认关闭

原台账 2.2 节内容移至此处：

- 开关：`.env-sirchmunk` 的 `SIRCHMUNK_ENABLE_CLUSTER_REUSE`，上游 `api/search.py` 两处实例构造读取后传入 `AgenticSearch(reuse_knowledge=...)`。**不可删行**：上游对未设置按 `true` 处理。开启 = 改为 `true` + 跑 `scripts/sirchmunk/preflight.sh` 预下载 embedding 模型（约 470 MB）+ 重启 sidecar，主项目零代码改动。
- SX4.2 实测（`scripts/sirchmunk/sx42_reuse_results.md`）：只有 **FAST 原题逐字重问**命中（3/3，相似度 1.0、约 30 ms、0 token）；改写查询 0/3、干扰题 0/3（无误复用）；**只有 FAST 保存知识簇**（DEEP 在 agentic loop 后把 `cluster` 置 `None`），DEEP 只能复用 FAST 存下的簇。
- 回归风险：复用开启下非命中查询命中数 FAST 13/30 → 8/30、DEEP 23/30 → 18/30（0 错误，硬/软复用 0 次触发，差值归为 n=3 的 LLM 跑间方差 ±5/30）。
- 适用场景已写入 USAGE_GUIDE 4.3：语料稳定 + FAST 高频原题重问才有收益；开启须知含「改写不命中、DEEP 只复用 FAST 簇、改文件须手动清 `.cache/knowledge/`」。
- 相关上游缺陷仍记在台账第 3 类：#2 复用命中不可观测、#3 复用无过期机制、#4 FAST 质量闸门可能拒存正确答案。

### 2.2 知识演化：SDK 可开，HTTP 服务不传

- 开关是构造参数 `AgenticSearch(enable_knowledge_evolution=False)`（`search.py`），无环境变量；`api/search.py` 的单例（同步端点）与每请求实例（SSE 流式端点）两处构造都不传；CLI 同样不传。
- **依赖复用**：只有 `enable_knowledge_evolution=True` 且 `embedding_client` 非空时才创建 `KnowledgeEvolver`，即必须同时开复用，2.1 的全部约束一并继承。
- **触发点**：`search()` 在 FAST/DEEP 返回后，若有 `cluster` 则 fire-and-forget 调 `knowledge_evolver.step(cluster)`。结合 2.1「DEEP 返回 `cluster=None`」，**实际只有 FAST 查询喂给 evolver**。
- **状态落盘**：进度写 `<work_path>/.cache/evolve/manifest.json`（`current_step`、`cluster_ids_buffer` 等），每次 `step()` 都保存，因此流式端点「每请求新实例」也能跨实例累积步数。
- **阶段门槛**（`scheduler/evolver.py` 常量）：连接/合并需缓冲 ≥ 5 个新簇（合并相似度 ≥ 0.90，建边 ≥ 0.60）；边刷新 ≥ 20 次；元聚类发现需 ≥ 100 个簇且距上次 ≥ 10 步；全局更新需 ≥ 500 个簇。本仓库小语料上只有「连接/合并」现实可达。
- **额外成本**：演化阶段并发调 LLM（上限 5），发生在请求返回之后；是否计入请求的 token 预算待核实（预算在 `search()` 内绑定与重置）。

## 3. 方案与取舍

| 方案 | 做法 | 改动量 | 取舍 |
|------|------|--------|------|
| A. 只开复用 | 2.1 的三步 | 一行配置 | 已有实测：收益只覆盖 FAST 原题重问 |
| **B. 启动器（推荐实验形态）** | 新增 sidecar 侧启动脚本：读 `SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION`，在进程内包装 `AgenticSearch.__init__` 的该参数默认值，再走上游 `sirchmunk serve` 同一入口（`uvicorn.run("sirchmunk.api.main:app", reload=False)`，同进程保证补丁生效）；`start_sidecar.sh` 按开关选择入口 | 约 30 行脚本 + 脚本开关 | 零上游改动，鉴权 / 路径 / 响应格式 / SSE 全部沿用上游；代价是依赖上游内部构造签名，升级时可能静默失效，需启动自检兜底（日志须出现 `Knowledge evolution enabled`） |
| C. 自写完整 FastAPI 服务 | 用 SDK 重写 search 端点 | 大 | **否决**：需重做 Bearer 鉴权、`allowed_paths`、并发信号量、四种响应格式、SSE，且长期跟随上游内部 API |
| D. 上游 PR | 给 `api/search.py` 加 `SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION` 环境变量，与复用开关同构 | 上游几行 | 最干净的长期形态，但依赖上游接受；建议在 B 的实验结论为正面后再提 |
| E. 主 venv 直接 `import sirchmunk` | — | — | **否决**（红线）：stdout 污染 MCP、事件循环绑定、构造期联网/下载、依赖污染、故障不隔离、多进程写冲突，见 S 系列第 4 章 |

方案 B 落地时的配套约束：

- [check_redlines.py](../../scripts/check_redlines.py) 的 `no-sirchmunk-import` 扫描 `scripts/`，启动器 `.py` 放在 `scripts/sirchmunk/` 会被拦下。需新增一条**按文件路径、带规格引用**的 sidecar 侧白名单（与 `LLM_FALLBACK_EXEMPTIONS` 同一审批口径），不得放宽到整个目录。
- 主 venv 的离线测试不能 import 启动器（会 import sirchmunk），只能做静态检查（AST：只包装该一个参数、`reload` 恒为 `False`）与 `start_sidecar.sh` 开关分支的离线单测。
- 启动器只影响 sidecar 进程；MCP stdout 纪律不受影响（sidecar 的 stdout 是日志，不是 JSON-RPC 通道）。

## 4. 决策三问

1. **解决了什么已观察到的问题？**——**部分**。已观察到的是复用收益面过窄：改写查询 0/3 命中（SX4.2）。evolver 的上游宣称机制（合并后按联合查询集重算 embedding、语义边）正对应「改写不命中」，是一个可证伪的假设；但本项目目前没有依赖复用或演化的用户流程，问题本身不紧迫。
2. **有无更简单方案？**——只开复用（A）最简单，但收益已知很窄；B 是开 evolver 的最小改动（零上游改动，约 30 行）；D 更干净但依赖外部。
3. **如何确认有效？**——沿用 SX4.2 协议与工具（`tests/fixtures/agentic/reuse_probe_questions.json` 的 seed → repeat → reword → distractor 探针、`scripts/sirchmunk/reuse_log_markers.py`、`baseline_round.sh` 冷启动轮次），演化开 vs 关对照。判据：
   - 先预热 ≥ 5 个 FAST 新簇，sidecar 日志确认 `[Evolve]` 连接/合并阶段**实际触发**（未触发则结论无效，而非负结果）；
   - 改写探针命中 > 0/3 且答案正确，干扰题误复用保持 0；
   - 非命中查询命中数回退不超出 ±5/30 方差带；
   - 记录演化阶段额外 token 与 sidecar 线程数，与 SX5.3 基线对比。

## 5. 风险与已知约束

- 继承复用的全部缺陷（台账 3.2–3.4）：命中不可观测、文件改动后不失效、质量闸门拒存。
- 合并会改写已有簇的内容与 embedding，答案随使用漂移，可复现性下降；评估与日常使用须分开 `work_path`。
- 小语料只能到达「连接/合并」阶段，元聚类与全局更新在本仓库不可评估。
- 启动器依赖上游内部构造签名（方案 B 取舍），以及 sidecar 版本漂移（第 2 章 editable 安装问题）。
- 流式端点线程泄漏（台账 3.1）与本提案无关但叠加：演化不新增实例构造，预期不加剧，需在实验中顺带观测。

## 6. 范围

**In Scope**：复用开启路径的复核；sidecar 侧 evolver 启动器（方案 B）；红线脚本的精确白名单；门控实验与裁决；USAGE_GUIDE 文档更新；按结论决定是否提上游 PR（方案 D）。

**Out of Scope**：任何上游源码改动；主 venv import sirchmunk；自写完整服务（方案 C）；主项目侧复用/演化可观测性改造（依赖上游 telemetry，见台账 3.2）；默认开启（实验结论正面也只改文档推荐，默认值另议）。

## 7. 候选任务（立项时改写为规格任务表，前缀暂定 KE）

| 编号 | 任务 | 验收标准 |
|------|------|----------|
| KE0 | 钉住 sidecar 版本 | `.venv-sirchmunk` 安装来源为明确 commit，S 系列 4.8 / USAGE_GUIDE 记录一致；evolver 相关源码事实（第 2.2 节）在该 commit 复核通过 |
| KE1 | evolver 启动器 + `start_sidecar.sh` 开关 | 开关关闭时行为与 `sirchmunk serve` 逐字一致；开启时日志出现 `Knowledge evolution enabled`，否则启动失败；红线脚本精确白名单 + 离线静态单测；`pytest tests` 与 `check_redlines.py` 全绿 |
| KE2 | 门控实验（复用开 / 复用 + 演化开对照） | 按第 4 章第 3 问判据产出结果文件（`scripts/sirchmunk/` 下 md + json），含 `[Evolve]` 触发证据与 token / 线程数 |
| KE3 | 裁决与文档 | USAGE_GUIDE 4.3 更新开启方式与结论；本提案三问补全或存档负结果；台账同步 |
| KE4（门控） | 上游 PR | 仅当 KE2 结论正面：向上游提交环境变量开关 PR，附 KE2 数据 |
