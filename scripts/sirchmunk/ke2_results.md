# KE2 成对实验结果（知识演化 vs 只开复用）

- 日期：2026-11-15 17:50:13｜语料 commit：`a98d388ccdfe75658e6e04e569e65842079734bc`｜上游：`b314e11fcea87cf8146b2844cf2f991208c021d9`
- 预设：['P1', 'P2'] 组 × 3 改写 × 2 重复；提升门 = B−A ≥ 2 且 A 臂 reword = 0
- 成本：A 332000 tok / B 343979 tok（上限 4000000）

| 臂 | 角色 | n | source_hit | reuse_candidate | fast_reused | answer_correct | tokens |
|---|---|---|---|---|---|---|---|
| A(演化关) | seed | 4 | 4 | 3 | 3 | 4 | 37542 |
| A(演化关) | repeat | 4 | 4 | 4 | 4 | 4 | 0 |
| A(演化关) | reword | 12 | 8 | 6 | 6 | 10 | 187398 |
| A(演化关) | distractor | 4 | 0 | 1 | 1 | 2 | 107060 |
| B(演化开) | seed | 4 | 2 | 3 | 3 | 4 | 37517 |
| B(演化开) | repeat | 4 | 2 | 4 | 4 | 4 | 0 |
| B(演化开) | reword | 12 | 12 | 6 | 6 | 12 | 194759 |
| B(演化开) | distractor | 4 | 3 | 1 | 1 | 4 | 111703 |

## 判定

- reword fast_reused：A 6/12 vs B 6/12，差值 0，bootstrap 95% CI [-5, 5]
- **verdict: insufficient_evidence**（门：B−A ≥ 2 且 A reword = 0）

无效轮与原因、逐请求完整答案与日志对齐见 ke2_results.json。
