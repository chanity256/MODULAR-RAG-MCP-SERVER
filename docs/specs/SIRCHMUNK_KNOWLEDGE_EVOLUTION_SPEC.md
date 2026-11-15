# Sirchmunk 知识演化与知识簇复用规格 (SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC)

> 状态：**开发中**（KE 系列，KE0 已交付 2026-11-15，KE1–KE4 未交付；复用与演化默认均保持关闭）｜上游基线：`b314e11`（KE0 已将 editable 实际检出钉回并验证）｜决策依据：由 `sirchmunk-evolution-reuse_backlog.md` 于 2026-11-15 经用户明确指令单文件晋升，原提案见 git 历史
>
> 起源：[sirchmunk-extension-followups_backlog.md](../backlog/sirchmunk-extension-followups_backlog.md) 第 1 类 #1「知识演化（evolver）」与第 2.2 节「知识簇复用」。2026-11-15 源码复核确认：evolver 不必改本机上游源码，在 sidecar venv 内用 SDK 启动器即可开启；[SX 规格](../frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md) 文件头已同步勘误。
>
> 关联：[SIRCHMUNK_INTEGRATION_SPEC_frozen](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md)（第 4 章副作用实证、3.1 sidecar 架构决策）、[SIRCHMUNK_CAPABILITY_GAP_ANALYSIS](../analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md)（历史判断以文件头勘误为准）、[USAGE_GUIDE](../reference/USAGE_GUIDE.md) 4.3（复用开启须知）。
>
> 决策边界：原提案第 1 问仍只能部分回答，本项目尚无依赖复用或演化的用户流程。本次立项授权的是**技术验证与可证伪实验**，不授权默认开启；KE4 还须由多轮正面结果、可接受成本和实际使用场景共同触发。

---

## 1. 勘误与实施边界：evolver 不需要改本机上游源码

台账原文：「`api/search.py` 与 CLI 均不传 `True`——HTTP/CLI 均不可达，开启必须改上游源码，同时违反本项目与上游双方纪律」。前半句（经现有 `sirchmunk serve` 默认入口不可达）成立，后半句不成立：

- 本规格实验阶段遵守两条边界：**主 venv 永不 `import sirchmunk`**；**不在本仓库或本机上游检出中维护源码改动或私有分叉**（[AGENTS.md](../../AGENTS.md)「Sirchmunk sidecar 进程隔离红线」、SX 规格文件头）。KE4 的独立上游 PR 是门控例外，届时另按上游审批与贡献流程执行，不属于本地方案 B 的实施内容。
- 在 sidecar 独立 venv 内运行一个调用 SDK 的启动器，不碰上述边界：主项目照旧只经 `http_backend.py` 走 HTTP；本机上游一行不改。同类先例：`scripts/sirchmunk/preflight.sh` 已在 sidecar venv 内 `from sirchmunk...`，[check_redlines.py](../../scripts/check_redlines.py) 文件头注明其为合法白名单。
- 它触碰的是 S 系列 3.1「sidecar = 上游 `sirchmunk serve`」这一**架构决策**，KE3 必须回写实现注记，而非进程隔离红线问题。

## 2. 现状事实（源码复核）

复核基线：本机 sirchmunk 检出 `2a9b98e`（Merge PR #186），`.venv-sirchmunk` 是对该检出的 editable 安装（`sirchmunk.__file__` 指向检出目录），而 S 系列 4.8、`setup_venv.sh` 与 SX4.2 的锁定 commit 是 `b314e11`。两者在 `api/search.py`、`search.py` 上合计有 +78/-36 行差异。已确认两版的 `scheduler/evolver.py` 相同，构造开关、evolver 初始化、统一 `step()` 调用点、API 两处不传参以及 DEEP direct-document / 复用提前返回均一致；这不等于两版的全部搜索、保存与复用语义相同。KE0 必须将实际运行版本钉回 `b314e11`，不得只检查安装元数据。

### 2.1 知识簇复用：已接入，默认关闭

- 开关：`.env-sirchmunk` 的 `SIRCHMUNK_ENABLE_CLUSTER_REUSE`，上游 `api/search.py` 两处实例构造读取后传入 `AgenticSearch(reuse_knowledge=...)`。**不可删行**：上游对未设置按 `true` 处理。开启 = 改为 `true` + 跑 `scripts/sirchmunk/preflight.sh` 预下载 embedding 模型（约 470 MB）+ 重启 sidecar，主项目零代码改动。
- SX4.2 实测（`scripts/sirchmunk/sx42_reuse_results.md`）：只有 **FAST 原题逐字重问**命中（3/3，相似度 1.0、约 30 ms、0 token）；改写查询 0/3、干扰题 0/3（无误复用）。常规 DEEP agentic-loop 新检索路径在 loop 后把 `cluster` 置 `None`，已实测的 DEEP 复用是消费 FAST 存下的簇；该结论不覆盖 DEEP direct-document 等提前返回路径。
- 回归风险：复用开启下非命中查询来源命中数 FAST 13/30 → 8/30、DEEP 23/30 → 18/30（0 错误，硬/软复用 0 次触发）。±5/30 只是 n=3 既有实验的**经验波动**，不是置信区间或可用于新实验的合格回归门。
- 适用场景已写入 USAGE_GUIDE 4.3：语料稳定 + FAST 高频原题重问才有收益；开启须知含「改写不命中、常规 DEEP 新检索不存簇、改文件须手动清 `.cache/knowledge/`」。
- 相关上游缺陷仍记在台账第 3 章：#2 复用命中无 telemetry、#3 复用无过期机制、#4 FAST 质量闸门可能拒存正确答案。sidecar 日志可用于 FAST 硬复用判定，但候选相似簇、实际复用、来源命中与答案正确是四个不同指标。

### 2.2 知识演化：SDK 可开，现有 HTTP/CLI 默认不传

- 开关是构造参数 `AgenticSearch(enable_knowledge_evolution=False)`（`search.py`），无环境变量；`api/search.py` 的同步端点单例在首次请求时懒创建，SSE 流式端点每请求创建实例，两处构造都不传；CLI 同样不传。app startup 预热的 Chat 单例也构造 `AgenticSearch`，且构造异常可能被吞掉。
- **依赖复用**：只有 `enable_knowledge_evolution=True` 且 `embedding_client` 非空时才创建 `KnowledgeEvolver`，即必须同时开复用，2.1 的全部约束一并继承。
- **触发点**：`search()` 在 FAST/DEEP 返回前以 `asyncio.create_task()` fire-and-forget 调度 `knowledge_evolver.step(cluster)`，不等待完成。常规 DEEP agentic-loop 返回 `cluster=None`，但 DEEP direct-document 会创建 `"DQ"` cluster，DEEP 复用命中也会返回非空 reused cluster，二者都可能触发 evolver；不能概括为「只有 FAST 喂给 evolver」。客户端断连与进程退出时的后台任务 drain 需另行处理。
- **状态落盘**：进度写 `<work_path>/.cache/evolve/manifest.json`（`current_step`、`cluster_ids_buffer` 等），每次 `step()` 都保存。严格串行的 SSE 请求可由下一实例重新读盘而续接；并发实例只在构造时读取一次，锁与 semaphore 均为实例级，且共用 `manifest.json.tmp`，存在丢失更新和临时文件竞争，不能保证并发累积正确。
- **阶段门槛**（`scheduler/evolver.py`）：连接/合并需缓冲 ≥ 5 个新簇（合并相似度 ≥ 0.90，建边 ≥ 0.60）；边刷新 ≥ 20 次；元聚类发现需距上次 ≥ 10 步，且非 meta 簇数量相对上次基线的绝对变化 ≥ 100；全局更新要求相应变化 ≥ 500。首次运行基线为 0 时才近似等于总量门槛；本仓库小语料上只有「连接/合并」现实可达。
- **额外成本**：Phase 1 连接/合并不调用 LLM，主要增加 embedding、存储和后台任务成本；Phase 3/4 才调用 LLM。所谓并发上限 5 是每个 `KnowledgeEvolver` 实例的上限，SSE 多实例可叠加，没有 sidecar 全局演化并发上限。当前实现中 evolver task 在 DEEP 请求 token budget reset 之后创建，不归属该请求硬预算；响应 telemetry 也形成于演化完成前，必须独立计量后台成本。

## 3. 技术选型与模块设计

| 方案 | 做法 | 改动量 | 取舍 |
|------|------|--------|------|
| A. 只开复用 | 2.1 的三步 | 一行配置 | 已有实测：收益只覆盖 FAST 原题重问 |
| **B. 启动器（本规格采用）** | 新增 sidecar 侧启动脚本：读 `SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION`，先复现 CLI 的 work path 解析与 `.env` 加载，再包装 `AgenticSearch.__init__`，最后以同一 ASGI app 启动 uvicorn；`start_sidecar.sh` 按开关选择入口 | 单个 sidecar 启动器 + shell 分支 + 测试 | 零本地上游改动，复用上游鉴权 / 路由 / 响应格式 / SSE；代价是依赖内部构造签名与 import 顺序，全局补丁还会影响 Chat，需 fail-fast 和双端点运行验证 |
| C. 自写完整 FastAPI 服务 | 用 SDK 重写 search 端点 | 大 | **否决**：需重做 Bearer 鉴权、`allowed_paths`、并发信号量、四种响应格式、SSE，且长期跟随上游内部 API |
| D. 上游 PR | 给 `api/search.py` 加 `SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION` 环境变量，与复用开关同构 | 上游几行 | 最干净的长期形态，但依赖上游接受；仅在 B 的多轮实验正面、成本可接受且本项目出现实际使用流程后再提 |
| E. 主 venv 直接 `import sirchmunk` | — | — | **否决**（红线）：stdout 污染 MCP、事件循环绑定、构造期联网/下载、依赖污染、故障不隔离、多进程写冲突，见 S 系列第 4 章 |

方案 B 落地时的配套约束：

- 启动器不是调用上游 CLI 的「同一入口」，而是复用 `sirchmunk serve` 的**同一 ASGI 应用**。必须先完成 work path 绝对化、环境回写和 `work_path/.env` 加载，再 import / patch；显式传递与 shell 分支一致的 `host`、`port`、`log_level` 和 `reload=False`，避免 import 期配置冻结或监听地址漂移。
- 在导入 `sirchmunk.api.main` 前完成补丁，可使同步与 SSE 构造路径经过同一已补丁类；`reload=False` 保证不另起 reloader 进程。启动时只做签名 / 补丁 / 开关组合 fail-fast；同步与 SSE 是否真正创建非空 evolver，必须由各自首个门控 FAST 请求验证。Chat 预热日志不能替代这两项证据。
- 全局类补丁也会开启上游 Chat 单例的演化。KE1 必须明确记录并验证该副作用；若实验不接受 Chat 开启，需缩小补丁作用域，不得把 Chat 的 `Knowledge evolution enabled` 日志误记为 search 启用成功。
- [check_redlines.py](../../scripts/check_redlines.py) 的 `no-sirchmunk-import` 扫描 `scripts/`，启动器 `.py` 放在 `scripts/sirchmunk/` 会被拦下。需新增一条**按文件路径、带本规格引用**的 sidecar 侧白名单（与 `LLM_FALLBACK_EXEMPTIONS` 同一审批口径），不得放宽到整个目录，并测试同目录其他 `.py` 仍被拦截。
- 主 venv 的离线测试不能 import 启动器（会 import sirchmunk），只能做 AST / import-order 静态检查与 `start_sidecar.sh` 分支的 fake 可执行文件单测。真实 uvicorn、模型与双端点检查只在 `RAG_AGENTIC_INTEGRATION=1` 下运行。
- 手动与 `start_api.py --manage-sidecar` 冷启动都要覆盖。已有 sidecar 只复用、不校验配置，修改 `.env-sirchmunk` 后必须重启 sidecar；复用 / 演化开启前必须成功运行 preflight。sidecar 启动失败仍保持 API 降级运行的既有契约，并验证 TERM → wait → KILL 回收、外部实例不被误杀及关闭开关后的回滚。
- 启动器只影响 sidecar 进程；MCP stdout 纪律不受影响（sidecar 的 stdout 是日志，不是 JSON-RPC 通道）。

## 4. 决策依据（原提案三问）

1. **解决了什么已观察到的问题？**——**部分**。已观察到的是复用收益面过窄：改写查询 0/3 命中（SX4.2）。evolver 的合并后联合查询集 embedding 与语义边机制正对应「改写不命中」，是一个可证伪的假设；但本项目目前没有依赖复用或演化的用户流程，问题本身不紧迫，因此本规格只授权技术验证，不授权默认开启。
2. **有无更简单方案？**——只开复用（A）最简单，但收益已知很窄；B 是已核实的最小本地方案（单个 sidecar 启动器，零本地上游改动）；D 更干净但依赖外部，且需要正向实验与真实使用场景共同支撑。
3. **如何确认有效？**——复用 SX4.2 的 seed → repeat → reword → distractor 探针思想和 FAST 日志标记，但使用新的 KE2 专用 runner，不能原样复用 `baseline_round.sh`。实验须满足：
   - **固定与配对**：KE0 钉住 `b314e11`、不可变语料快照、fixture、模型与配置；A 臂为「复用开、演化关」，B 臂为「复用开、演化开」，使用独立临时 `work_path`，执行相同预热与探针负载。若 KE0 无法钉回 `b314e11`，任务阻塞并上报，不得改用其他 commit 后直接与 SX4.2 旧绝对数作差。
   - **状态隔离**：每轮明确初始化或恢复 `knowledge` / `spec` / `evolve` 状态；预热由指定 seed、与正式探针不重合的训练改写及语义独立的补足查询组成，训练改写用于验证目标簇合并机制，独立补足用于达到阶段门槛，正式 reword / distractor 不参与预热。预热后保存快照，每个正式语义组从同一快照的独立副本开始，避免 reword 新簇影响 distractor。
   - **目标机制生效**：逐簇确认 ≥ 5 个有效新簇，而非只发 5 个请求；目标 seed 所属簇完成相关连接 / 合并，联合查询集或 embedding 确实更新，且阶段完成并持久化。只有无关簇触发、质量闸门导致簇不足、后台超时或目标簇未变化，均记「不可评估」。
   - **可审计判定**：分记 `source_hit`、`reuse_candidate`、`fast_reused`、`soft_reused`、`answer_correct`，不得统称 `hit`；FAST 正面复用必须由 `fast_reused` 证实，DEEP 只有候选日志时记「实际消费不可判定」。逐题制定完整 gold rubric，保存完整答案、日志对齐和人工裁决；非 200、日志块不对齐或答案无法裁决的轮次无效。
   - **统计与回归**：KE2 执行前预设独立语义组数、每组改写数、重复次数、最小有实际意义的改写复用提升和可接受成本，报告成对差值与区间估计。单次命中或 >0/3 只算探索性信号；非命中回归按两臂同期成对比较，不再使用历史 ±5/30 作为通过门。
   - **完整成本**：以同轮 A 臂为成本基线，分列搜索 / 演化 / 预热 / 无效轮 token 与总成本，记录 HTTP 响应时延、演化 drain 时延、总稳定时延、实际演化 LLM 并发，以及预热后、drain 后和 idle 后的线程数。主实验限定串行同步端点；SSE 并发与线程泄漏另做安全压力测试。
   - **真实门控与上限**：仅在显式 `RAG_AGENTIC_INTEGRATION=1` 下执行，预设最大请求数、token / 费用上限和 drain 超时；归档每轮有效、负面、证据不足或不可评估的原因，禁止对日常默认 work path 做清理。

## 5. 风险与已知约束

- 继承复用的全部缺陷（台账第 3 章 #2–#4）：telemetry 不可观测、文件改动后不失效、质量闸门拒存；日志协议只能补足其中一部分观测。
- 合并会改写已有簇的内容与 embedding，答案随使用漂移，可复现性下降；评估与日常使用须分开 `work_path`，并保存演化前后 manifest 与目标簇快照。
- 小语料只能到达「连接/合并」阶段，元聚类与全局更新在本仓库不可评估；Phase 1 无提升不等于高阶阶段无效。
- 并发 SSE 的 manifest 丢失更新、共享临时文件和每实例并发叠加可能破坏状态与成本上限；主实验必须串行，不能把串行结论外推到并发部署。
- 启动器依赖上游内部构造签名、import 顺序、CLI 初始化语义及 editable 实际版本；全局补丁还影响 Chat。升级时必须重新执行 KE0/KE1，不得只检查安装元数据。
- fire-and-forget 任务可能跨越后续请求、客户端断连或停机，必须 drain 后再测量与清理；演化成本不受原请求硬预算约束。
- 流式端点线程泄漏（台账第 3 章 #1）与本规格无关但叠加；SSE 压测须单独归因，不能把既有泄漏计为演化成本，也不能以「预期不加剧」替代观测。

## 6. 核心目标与范围

### 6.1 In Scope

- 复用开启路径与 `b314e11` 实际运行版本复核；
- sidecar 侧 evolver 启动器（方案 B）、`start_sidecar.sh` 开关及红线脚本精确白名单；
- 手动 / 托管冷启动、preflight、旧进程复用提示、失败降级与回滚；
- 隔离、可 drain、可审计的门控成对实验与裁决；
- USAGE_GUIDE、SX 勘误、能力分析、MOC、README 与来源台账同步；
- 按结论与实际使用场景决定是否提上游 PR（方案 D，KE4 门控）。

### 6.2 Out of Scope

- 本仓库或本机上游检出的源码改动与私有分叉维护（KE4 独立上游 PR 除外）；
- 主 venv import sirchmunk、自写完整服务（方案 C）；
- 主项目侧复用 / 演化 telemetry 改造（依赖上游能力，见台账第 3 章 #2）；
- 默认开启复用或演化；
- 把串行同步实验结论外推为并发 SSE 可用性结论；
- 在 KE2 证据不足或不可评估时作确定性正负裁决。

## 7. 测试方案

### 7.1 离线确定性测试

- 启动器不得在主 venv 测试中 import；以 AST / import-order 静态检查锁定只包装 `enable_knowledge_evolution`、原样转发参数、`reload=False` 及 CLI 初始化顺序。
- `start_sidecar.sh` 使用 fake venv / fake 可执行文件验证开关关闭与开启分支、环境加载、host / port / log level、非法组合 fail-fast 和回滚；`env.example` 必须显式保留 `SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION=false`。
- 红线白名单按相对路径精确匹配并引用本规格，同目录其他 Python 文件仍被拦截；不得放宽整个 `scripts/sirchmunk/`。
- sidecar manager 离线测试覆盖手动 / 托管冷启动、已有进程复用、preflight 前置、sidecar 失败后 API 降级、TERM → wait → KILL 回收及外部实例不被误杀。
- 默认离线套件零网络、零真实模型，保持 MCP stdout 与既有测试语义。

CI 等价检查：

```bash
NO_PROXY='*' pytest tests -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1
python scripts/check_redlines.py
pytest tests/unit/test_docs_links.py tests/unit/test_spec_structure.py -q
```

### 7.2 门控真实集成与实验

- 仅显式设置 `RAG_AGENTIC_INTEGRATION=1` 时运行真实 sidecar / LLM；不得进入默认 `pytest tests`。
- KE1 分别以同步与 SSE FAST 首次请求证明 evolver 非空，并记录 Chat 单例副作用；仅有 Chat 启动日志不算通过。
- KE2 按第 4 章第 3 问执行独立 work path、预热快照、目标机制生效证据、后台 drain、完整答案裁决、成对统计与成本上限；每轮分类为正面、负面、证据不足或不可评估。
- 实验产物归档非敏感版本 / 配置、语料与 fixture 版本、完整答案、日志对齐、manifest 与目标簇差异及成本数据；不得写入密钥或清理日常 work path。

### 7.3 不得破坏的既有契约

- 主 venv 永不 `import sirchmunk`，主项目只经 HTTP 调用 sidecar；关闭开关时仍执行原 `sirchmunk serve --host "$HOST" --port "$PORT"`。
- sidecar 启动失败不使主 API 崩溃，保持既有降级语义；外部已运行实例只复用、不接管、不误杀。
- 复用与演化默认均为 `false`；真实实验不改变默认配置，不将实验缓存带入日常 work path。
- `pytest tests` 离线全绿、`check_redlines.py` 全绿，文档链接与规格结构检查通过。

## 8. 任务排期与进度跟踪

依赖链：**KE0 → KE1 → KE2 → KE3**；KE4 仅由 KE2 多轮正面结果、可接受成本和实际使用场景共同触发。状态标记：`[ ]` 未开始、`[~]` 进行中、`[x]` 已完成。

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| KE0 | 钉住 sidecar、语料与 fixture | 运行时 `sirchmunk.__file__` 指向预期检出，上游 `git rev-parse HEAD` 与 `setup_venv.sh` 的 `PINNED_COMMIT` 均等于 `b314e11`，tracked 工作树无修改，启动日志记录实际 commit；根 README、S 系列 4.8、USAGE_GUIDE 的当前安装口径一致；第 2.2 节源码事实在该 commit 复核通过。固定不可变语料快照，替换已因 Streamlit 退役失效的 P1/Q3，并核验全部 gold rubric；无法钉回时阻塞并上报，不静默换版 | [x] |

KE0 执行记录（2026-11-15）：本机检出已 `git checkout b314e11`（detached HEAD = `b314e11fcea87…`，tracked 工作树干净，仅既有未跟踪 `.archify/`；两 commit 间依赖文件无差异，editable 安装无需重装），`.venv-sirchmunk` 运行时 `sirchmunk.__file__` 指向该检出；`start_sidecar.sh` 启动时回显实际运行 commit（手动与 `--manage-sidecar` 共用该脚本）。§2.2 全部源码事实在 `b314e11` 复核一致（`enable_knowledge_evolution` 构造默认 `False`、`KnowledgeEvolver` 需演化 + embedding 双条件、`step()` fire-and-forget、DEEP agentic-loop `cluster=None` / direct-doc `"DQ"`、manifest 路径与共享 `.json.tmp`、常量 5 / 0.60 / 0.90 / 20 / 10 / 100 / 500、LLM 并发 5，`api/search.py` 两处构造与 `api/chat.py` 均不传演化参数且读取 `SIRCHMUNK_ENABLE_CLUSTER_REUSE` 缺省 `true`）。不可变语料快照机制落地为 `scripts/sirchmunk/corpus_snapshot.sh`（`git archive` tracked 树 + 排序 SHA256 清单，同 commit 逐字节可复现），KE2 语料以其物化结果为准；golden Q3 与探针 P1 seed/repeat/reword 已改写为 README Web UI 启动命令考点（distractor 不变），新增 `tests/unit/test_golden_rubrics.py` 锁全部 expected_sources 与 answer_should_contain；README / S 系列 4.8 / USAGE_GUIDE 三处安装口径复核一致。新 P1 seed 的基线稳定性未经门控复核，由 KE2 预热确认。
| KE1 | evolver 启动器 + `start_sidecar.sh` 开关 | 关闭分支仍执行原命令且不加载启动器；开启分支复现 CLI 初始化并显式传 host / port / log level，签名或开关组合不兼容时 fail-fast，`env.example` 显式默认 `false`。门控同步与 SSE FAST 首次请求分别证明 evolver 非空，Chat 副作用有明确结论；手动 / 托管冷启动、preflight 前置、旧进程复用提示、失败降级、进程回收与回滚均通过。红线脚本按文件精确白名单，离线 AST / import-order / shell 分支测试齐全；`pytest tests` 与 `check_redlines.py` 全绿 | [ ] |
| KE2 | 专用 runner + 门控成对实验 | 仅在 `RAG_AGENTIC_INTEGRATION=1` 下运行；每臂每轮独立临时 work path，隔离 knowledge / spec / evolve，正式语义组从相同预热快照启动，探针前目标演化完成并持久化，停机前 drain。按第 4 章第 3 问产出 `scripts/sirchmunk/` 下 md + json，归档非敏感版本 / 配置、语料与 fixture 版本、预热簇清单、manifest 与目标簇前后差异、逐请求完整答案与裁决、日志对齐、分类原因、token / 时延 / 并发 / 线程及成本上限；无有效目标演化或可审计探针时不得作正负裁决 | [ ] |
| KE3 | 裁决与文档 | USAGE_GUIDE 4.3 更新实际开启方式、结论与限制；本规格决策依据补全或存档负面 / 证据不足 / 不可评估结果；来源台账、能力分析、MOC 与 README 保持一致。若采用方案 B，在 S 系列 3.1 与 SX 架构说明增加带日期的实现注记：入口可为 sidecar venv 内启动器，但仍运行同一上游 ASGI 应用，主项目 HTTP 契约及进程隔离不变 | [ ] |
| KE4（门控） | 上游 PR | 仅当 KE2 多轮正面、成本可接受且本项目出现实际使用流程：经 owner 审批后向上游提交环境变量开关 PR，附 KE2 成对数据、成本与限制；不以单次 >0/3 命中触发 | [ ] |
