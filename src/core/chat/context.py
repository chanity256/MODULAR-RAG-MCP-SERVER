"""多轮上下文处理（WC6.2）：话题相关性过滤 + 查询改写。

两步都是「尽力而为」的优化，失败绝不阻断一轮对话：
- 过滤 fail-open：LLM 异常或输出无法解析 -> 保留全部历史；
- 改写失败 -> 使用原句。

LLM 由调用方注入；历史为空时两步都不发起 LLM 调用。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.chat.history import split_turns
from core.chat.prompts import PROMPT_RELEVANCE, PROMPT_REWRITE, load_prompt
from libs.llm.base_llm import BaseLLM, ChatMessage

__all__ = ["ContextProcessor", "FilterOutcome", "RewriteOutcome"]

logger = logging.getLogger(__name__)

_SNIPPET_CHARS = 500
_JSON_ARRAY = re.compile(r"\[[^\[\]]*\]")
#: 改写结果超过原句该倍数 + 常量视为模型在「作答」而不是改写。
_REWRITE_MAX_FACTOR = 4
_REWRITE_MAX_EXTRA = 200


@dataclass
class FilterOutcome:
    """相关性过滤结果。"""

    history: list[ChatMessage]
    #: 被丢弃的轮数。
    dropped_turns: int = 0
    #: LLM 调用或解析失败（已按 fail-open 保留历史）。
    failed: bool = False
    #: 是否真正发起了 LLM 调用（空历史/开关关闭时为 False）。
    called: bool = False


@dataclass
class RewriteOutcome:
    """查询改写结果。"""

    query: str
    rewritten: bool = False
    failed: bool = False
    called: bool = False


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _SNIPPET_CHARS else text[:_SNIPPET_CHARS] + "..."


def _render_turns(turns: Sequence[Sequence[ChatMessage]], *, numbered: bool) -> str:
    lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        parts = [f"{'User' if m.role == 'user' else 'Assistant'}: {_snippet(m.content)}" for m in turn]
        if numbered:
            lines.append(f"[{index}] " + " | ".join(parts))
        else:
            lines.extend(parts)
    return "\n".join(lines)


def _fill(template: str, **values: str) -> str:
    # 逐个 replace 而非 format：历史原文里的花括号不应被当作占位符。
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def _parse_turn_numbers(text: str, turn_count: int) -> list[int] | None:
    """解析 ``[1, 3]`` 形式的输出；无法解析返回 ``None``。

    越界编号被忽略；非空数组但没有任何有效编号视为解析失败（避免误丢全部历史）。
    """
    for match in _JSON_ARRAY.finditer(text):
        try:
            value = json.loads(match.group(0))
        except ValueError:
            continue
        if not isinstance(value, list):
            continue
        if any(isinstance(v, bool) or not isinstance(v, int) for v in value):
            return None
        if not value:
            return []
        valid = sorted({v for v in value if 1 <= v <= turn_count})
        return valid or None
    return None


class ContextProcessor:
    """相关性过滤与查询改写。

    Args:
        llm: 同步 ``chat()`` 的 LLM（两步各一次轻量调用）。
        relevance_template / rewrite_template: 覆盖 ``config/prompts`` 下的默认模板。
        llm_kwargs: 透传给 ``llm.chat`` 的参数（缺省 ``temperature=0``）。
    """

    def __init__(
        self,
        llm: BaseLLM,
        *,
        relevance_template: str | None = None,
        rewrite_template: str | None = None,
        llm_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        self._llm = llm
        self._relevance_template = (
            relevance_template if relevance_template is not None else load_prompt(PROMPT_RELEVANCE)
        )
        self._rewrite_template = rewrite_template if rewrite_template is not None else load_prompt(PROMPT_REWRITE)
        self._llm_kwargs: dict[str, Any] = dict(llm_kwargs) if llm_kwargs is not None else {"temperature": 0.0}

    def _ask(self, prompt: str) -> str:
        response = self._llm.chat([ChatMessage("user", prompt)], **self._llm_kwargs)
        return response.content or ""

    def filter_history(self, history: Sequence[ChatMessage], question: str) -> FilterOutcome:
        """丢弃与新问题话题无关的历史轮次；失败时保留全部历史（fail-open）。"""
        turns = split_turns(history)
        if not turns:
            return FilterOutcome(history=[])
        kept_all = [m for turn in turns for m in turn]
        prompt = _fill(
            self._relevance_template,
            history=_render_turns(turns, numbered=True),
            question=question,
        )
        try:
            raw = self._ask(prompt)
        except Exception as exc:  # noqa: BLE001 - fail-open：任何 LLM 异常都不应中断对话
            logger.warning("chat relevance filter failed, keeping history: %s", exc)
            return FilterOutcome(history=kept_all, failed=True, called=True)
        numbers = _parse_turn_numbers(raw, len(turns))
        if numbers is None:
            logger.warning("chat relevance filter output unparseable, keeping history: %r", raw[:200])
            return FilterOutcome(history=kept_all, failed=True, called=True)
        selected = [turns[n - 1] for n in numbers]
        return FilterOutcome(
            history=[m for turn in selected for m in turn],
            dropped_turns=len(turns) - len(selected),
            called=True,
        )

    def rewrite_query(self, question: str, history: Sequence[ChatMessage]) -> RewriteOutcome:
        """结合历史把问题改写为独立检索查询；无历史或失败时返回原句。"""
        turns = split_turns(history)
        if not turns:
            return RewriteOutcome(query=question)
        prompt = _fill(
            self._rewrite_template,
            history=_render_turns(turns, numbered=False),
            question=question,
        )
        try:
            raw = self._ask(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat query rewrite failed, using original question: %s", exc)
            return RewriteOutcome(query=question, failed=True, called=True)
        candidate = self._clean_rewrite(raw)
        if not candidate or len(candidate) > len(question) * _REWRITE_MAX_FACTOR + _REWRITE_MAX_EXTRA:
            logger.warning("chat query rewrite output unusable, using original question: %r", raw[:200])
            return RewriteOutcome(query=question, failed=True, called=True)
        return RewriteOutcome(query=candidate, rewritten=candidate != question, called=True)

    @staticmethod
    def _clean_rewrite(raw: str) -> str:
        for line in raw.splitlines():
            line = line.strip().strip("`").strip()
            if line:
                return line.strip("\"'“”‘’").strip()
        return ""
