# Agent Note: 图片占位符原子保护（G15，混合式守卫）

Status: implemented

## Problem

`[IMAGE: {id}]` 占位符是 pdf_loader 写入正文、chunk_refiner / ImageCaptioner 依赖的契约 token。当 `chunk_size` 很小（e2e 用的 60，占位符本身常超 30 字符）时，切分器会把占位符从中间切断（如 `[IMAGE:abc` / `[IM`），导致 `image_refs` 分发静默丢失；占位符也可能独占一个 chunk（正文为空，检索价值为零）。e2e 套件此前不知情地依赖这个碎片化 bug 才通过，说明缺口是真实的。不做防护，多模态检索链路在小 chunk 配置下无报错地退化。

## Decision

`DocumentChunker._split_preserving_placeholders` 采用混合守卫：默认原样切分、零行为变化；仅当检测结果命中碎片化（某 chunk 以未闭合 `[` 片段结尾，命中 `_HEAD_FRAGMENT_RE`；或存在占位符独占的 chunk）时，退化为提取式切分——占位符先从全文提取（切分器看不到它，不可能切断），切分后按文本段归属回填（附着到前邻文本段的最后一个 chunk，图随文走；前邻为空时顺延后邻；全文只有占位符时独立成 chunk）。

守卫放在 `DocumentChunker` 而非 `libs.splitter`：后者是纯文本工具（`str -> List[str]`），不认识占位符契约；业务适配层是唯一同时知道占位符语义和 chunk 元数据分发的地方。

## Alternatives considered

- **纯后修补（post-repair + 后缀去重合并）**：最强论据是对切分器零侵入、正常输入路径绝对不变。被否：在重复字符边界（如同字符连排行）去重合并时会把真实正文连同碎片一起静默丢掉——用内容损失换实现简单，比修的 bug 更糟。
- **无条件提取式切分**：最强论据是逻辑单一、不依赖检测正则。被否：改变了干净输入的既有行为（inline 多占位符段落被强制拆散），且对绝大多数文档是徒功；检测误报的代价仅为切分方式变化，而误漏报的代价才是丢引用，混合式的风险面更小。
- **检测下沉到 `libs.splitter`**：最强论据是所有切分调用方统一受益。被否：占位符是业务契约不是文本切分概念，下沉会让纯文本工具背上业务知识，破坏 C4 阶段划定的职责边界。

## Consequences

- 收益：`image_refs` 分发在任何 `chunk_size` 下保持完整；e2e 不再依赖碎片化 bug；检测基于占位符格式规范（`[IMAGE: {id}]`，id 无空格与 `]`），正则可覆盖任意切点。
- 代价：提取式切分下附着占位符的 chunk 可能超过 `chunk_size`（语义完整性优先于长度约束，与代码块不打断的取舍一致）。
- 代价：`_HEAD_FRAGMENT_RE` 会把 Markdown 未闭合的 `[引用` 也判为碎片，误触发提取式切分——代价仅是切分方式变化，无内容损失，但切分结果与旧版本不可比。
- 已知上限：检测只覆盖"切断"与"独占"两种碎片形态；若未来占位符格式放宽（允许 id 含 `]`），`_HEAD_FRAGMENT_RE` 与 `IMAGE_PLACEHOLDER_PATTERN` 必须同步修改。

## Verification

- 8 个新单测，含真实 `RecursiveSplitter` 在 `chunk_size=30` 下的端到端断言。
- `with_images.pdf` 在 `chunk_size=60` 由 5 个碎片变为 2 个完整 chunk，`image_refs` 保留。
- 全量测试 1066 passed（原 1058 + 8）。
