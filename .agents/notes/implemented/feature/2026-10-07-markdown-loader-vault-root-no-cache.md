# Agent Note: MarkdownLoader 的 vault_root、缓存与降级口径

Status: implemented

## Problem

IR7.1/IR7.2 落地 `src/libs/loader/markdown_loader.py` 时，规格 3.8.1 有几处留白需要拍板：`vault_root` 在管线里取什么值；MarkdownLoader 是否像 `.pdf`/markitdown 那样进 per-suffix loader 缓存；空 Markdown 文件报不报错；外链图片怎么处理。这些选择影响图片解析正确性与摄取行为一致性。

## Decision

- **`vault_root` = 源文件所在目录**（`Path(source).parent`，在 `_resolve_loader` 构造时传入）。`pipeline.run` 恒以单文件为源（目录扫描在 CLI 层已拆成逐文件），笔记内相对图片引用按「笔记旁边」解析。
- **MarkdownLoader 不进 per-suffix loader 缓存**。`.pdf`/markitdown 的 loader 无状态可缓存；MarkdownLoader 持有随源变化的 `vault_root`，缓存复用会让第二个目录的笔记按第一个目录解析图片。
- **空 Markdown / 纯空白文件返回空 `Document`**（title 兜底到文件名 stem），不报错。与 `MarkitdownLoader` 的「空文本抛错」刻意不同：空 Markdown 是合法文档，markitdown 空文本则意味着解析器失败、静默入库无法诊断。
- **外部 URL 图片引用（含 `://`）不解析、保留原文**，不生成占位符也不落 `missing_image_refs`。只处理本地相对引用。
- **坏 frontmatter 降级**：行级解析失败且 PyYAML 也失败时，frontmatter 原样拼回正文 + `metadata["frontmatter_parse_error"]=True`，不阻塞摄取（规格既定，与全 Loader 的降级纪律一致）。

## Alternatives considered

- **`vault_root` 传扫描根目录**：最强论据是 Obsidian 常见布局「笔记在 `notes/` 子目录、图片在 vault 根 `assets/`」，按根解析命中率更高。被否：需要把扫描根穿透到 `_resolve_loader` 签名，且单文件 `--path x.md` 摄取时「根」语义不明；当前口径下跨目录引用走 `missing_image_refs` 降级，可诊断不阻塞。
- **进 per-suffix 缓存**：最强论据是与既有 loader 行为一致、省对象构造。被否：构造是纯 Python 轻量操作（无模型无 IO），省不了什么；而错误缓存图片解析根是正确性问题。
- **空文件对齐 markitdown 报错**：最强论据是全 Loader 行为统一。被否：两者「空」的语义不同（合法空文档 vs 解析失败），统一反而制造误报。
- **URL 图片走本地下载后落盘**：直接违反离线确定性测试纪律（摄取期零网络），不进入比较。

## Consequences

- **收益**：图片解析根确定可预测（笔记旁）；测试零网络纪律无例外；坏输入全部降级可诊断（`frontmatter_parse_error` / `missing_image_refs`），不阻塞批摄取。
- **代价**：Obsidian 的跨目录引用（子目录笔记 → vault 根 assets）会命中 `missing_image_refs` 降级，占位符不生成，这部分图片不可检索；要覆盖该布局需要未来给 pipeline 增加 vault 根透传（`--vault-root` 类参数），当前未做。
- **代价**：每文件新建 loader 实例（无缓存），批摄取大量 `.md` 时多一次轻量构造，实测不可见。

## Verification

- `tests/unit/test_markdown_loader.py`（29 例）：embed 存在/缺失两路、确定性、坏 frontmatter、空文件、URL 保留。
- `tests/unit/test_ingestion_loader_routing.py`：`.md` 路由与单文件摄取。
- 治理背景见 [Markdown 摄取承接进 IR 规格](../process/2026-10-07-markdown-ingestion-in-ir-spec.md)。
