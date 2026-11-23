"""原 Markdown 标题索引：标题路径 → 行区间（D12 证据回定位，PI1）。

纯标准库实现（规格 §3.2）。行号 1 起计、闭区间；``line_end`` 为下一个同级
或更高级标题的前一行，末个标题延伸到文件尾。上游 Flash 树的两类合成节点
按规则映射：``Preface`` → 首个标题前正文；``<parent title> (intro)`` → 父
标题行到首个子标题前区间。回定位失败返回 ``None``，调用方退化只给
``section``（telemetry 计 ``unlocated``）。
"""

from __future__ import annotations

import re
import unicodedata

ATX_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*#*\s*$")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
SETEXT_EQ_RE = re.compile(r"=+")
SETEXT_DASH_RE = re.compile(r"-{2,}")

# PDF 文本抽取会把部分汉字映射为 CJK 部件 Supplement 码位（PI0.4a 实测：
# 见→⻅ 门→⻔ 风→⻛ 长→⻓ 等），标题归一化需先映射回正字。
RADICAL_FORM_MAP = str.maketrans({
    "⻅": "见", "⻆": "角", "⻉": "贝", "⻋": "车", "⻑": "长", "⻓": "长",
    "⻔": "门", "⻗": "雨", "⻘": "青", "⻙": "韦", "⻚": "页", "⻛": "风",
    "⻜": "飞", "⻝": "食", "⻞": "食", "⻢": "马", "⻣": "骨", "⻤": "鬼",
    "⻥": "鱼", "⻦": "鸟", "⻨": "麦", "⻬": "齐", "⻭": "齿", "⻮": "齿",
})


def normalize_title(s: str) -> str:
    """标题归一化：NFKC（全半角统一）+ 部件形映射 + 去强调符与空白。"""
    s = unicodedata.normalize("NFKC", s).translate(RADICAL_FORM_MAP)
    s = re.sub(r"[*_`~\[\]]", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def build_heading_index(md_text: str) -> list[dict]:
    """解析 ATX + Setext 标题，跳过围栏代码块与 front matter、空标题。

    返回按行序的 ``{"level", "title", "line", "parents": [祖先标题...]}``
    （``line`` 为标题所在行，1 起计；Setext 标题 ``line`` 记下划线行，
    与 POC 口径一致）。
    """
    lines = md_text.split("\n")
    # front matter：文件开头 --- 包围的 YAML 块整体跳过（PI0.4a：其中
    # `---` 会被误判为 Setext 下划线）
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    heads: list[dict] = []
    fence = None
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if fence:
            if re.match(r"^" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}\s*$",
                        stripped):
                fence = None
            i += 1
            continue
        m = FENCE_RE.match(stripped)
        if m:
            fence = (m.group(1)[0], len(m.group(1)))
            i += 1
            continue
        m = ATX_RE.match(line)
        if m:
            title = m.group(2).strip()
            if title:  # 空标题跳过（PI0.4a：3 条空标题页映射 miss）
                heads.append({"level": len(m.group(1)), "title": title, "line": i + 1})
            i += 1
            continue
        # Setext：当前行是上一非空行的下划线（= 一级 / - 二级）
        if stripped and i > start and lines[i - 1].strip():
            prev = lines[i - 1].strip()
            level = None
            if SETEXT_EQ_RE.fullmatch(stripped):
                level = 1
            elif SETEXT_DASH_RE.fullmatch(stripped) and len(prev) <= 200 \
                    and not prev.startswith(("#", "-", "*", "+", ">")):
                level = 2
            if level and not ATX_RE.match(prev) and prev:
                heads.append({"level": level, "title": prev, "line": i})
            i += 1
            continue
        i += 1
    # 祖先路径（同级或更高级标题截断栈）
    out: list[dict] = []
    stack: list[dict] = []
    for h in heads:
        while stack and stack[-1]["level"] >= h["level"]:
            stack.pop()
        h = {**h, "parents": [x["title"] for x in stack]}
        out.append(h)
        stack.append(h)
    return out


class HeadingIndex:
    """标题索引：``locate(title, parent_path) -> (line_start, line_end) | None``。

    匹配顺序：归一化精确匹配（同名按父路径尾部对齐消歧）→ 包含关系模糊
    兜底（D12③：上游 LLM 重写标题后按包含关系回收）→ ``None``。
    """

    def __init__(self, md_text: str) -> None:
        self.heads = build_heading_index(md_text)
        self.first_heading_line = self.heads[0]["line"] if self.heads else None
        pre = md_text.split("\n")[
            : (self.first_heading_line - 1) if self.first_heading_line else None]
        self.pre_first_content = any(s.strip() for s in pre)
        self.line_count = len(md_text.split("\n"))

    def _span(self, idx: int) -> tuple[int, int]:
        h = self.heads[idx]
        for j in range(idx + 1, len(self.heads)):
            if self.heads[j]["level"] <= h["level"]:
                return (h["line"], self.heads[j]["line"] - 1)
        return (h["line"], self.line_count)

    def _candidates(self, base_key: str, parent_path: list[str] | None) -> list[int]:
        cands = [i for i, h in enumerate(self.heads)
                 if normalize_title(h["title"]) == base_key]
        if not cands:
            # 模糊兜底：归一化后包含关系（上游 merge 档 ~12% 节点标题被
            # LLM 重写，只能按包含关系回收；歧义即放弃）
            fuzzy = []
            for i, h in enumerate(self.heads):
                ht = normalize_title(h["title"])
                if ht and base_key and (base_key in ht or ht in base_key):
                    fuzzy.append(i)
            if len(fuzzy) == 1:
                cands = fuzzy
            elif len(fuzzy) > 1:
                return []
        if len(cands) > 1:
            if not parent_path:
                return []  # 同名且无父路径：歧义返回 None
            pk = [normalize_title(p) for p in parent_path if normalize_title(p)]
            if not pk:
                return []
            narrowed = []
            for i in cands:
                parents = [normalize_title(p) for p in self.heads[i]["parents"]]
                # 父路径尾部对齐（Flash 树可能跳层）
                n = min(len(parents), len(pk))
                if n and parents[-n:] == pk[-n:]:
                    narrowed.append(i)
            if not narrowed:
                return []  # 消歧失败仍歧义：返回 None
            cands = narrowed
        return cands

    def locate(self, title: str, parent_path: list[str] | None = None) -> tuple[int, int] | None:
        """返回 ``(line_start, line_end)``（1 起计、闭区间）；失败返回 ``None``。"""
        if not title or not title.strip():
            return None
        if title.strip() == "Preface":
            if self.pre_first_content and self.first_heading_line:
                return (1, self.first_heading_line - 1)
            return None
        key = normalize_title(title)
        if not key:
            return None
        is_intro = key.endswith("(intro)") or title.rstrip().endswith("(intro)")
        base_key = key[:-len("(intro)")] if is_intro else key
        cands = self._candidates(base_key, parent_path)
        if not cands:
            return None
        idx = cands[0]
        if is_intro:
            # <parent title> (intro)：父标题行到首个子标题前
            parent_level = self.heads[idx]["level"]
            for j in range(idx + 1, len(self.heads)):
                if self.heads[j]["level"] > parent_level:
                    return (self.heads[idx]["line"], self.heads[j]["line"] - 1)
                if self.heads[j]["level"] <= parent_level:
                    break
            return self._span(idx)
        return self._span(idx)
