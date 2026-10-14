"""多轮历史裁剪（WC6.1）：纯函数，无 IO。

从新到旧累计，同时受 ``max_turns``（轮数）与 ``max_tokens``（估算 token）两个预算约束；
任一预算被突破即停止，保证保留的是**最近的连续**消息。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from libs.llm.base_llm import ChatMessage

__all__ = ["CHARS_PER_TOKEN", "build_history", "estimate_tokens", "split_turns"]

#: 与上游同款的粗略估算：每 token 约 3.5 个字符。
CHARS_PER_TOKEN = 3.5

_ROLES = ("user", "assistant")


def estimate_tokens(text: str) -> int:
    """估算 token 数（``len/3.5`` 向上取整；空串为 0）。"""
    if not text:
        return 0
    return int(-(-len(text) // CHARS_PER_TOKEN))


def _role_content(message: Any) -> tuple[str, str]:
    if isinstance(message, Mapping):
        return str(message.get("role", "")), str(message.get("content") or "")
    return str(getattr(message, "role", "")), str(getattr(message, "content", "") or "")


def split_turns(messages: Sequence[Any]) -> list[list[ChatMessage]]:
    """把消息（旧 -> 新）分组为轮次：每轮以 user 消息开头，后接其 assistant 回复。

    非 user/assistant 角色与空内容被忽略；开头孤立的 assistant 消息（历史被外部截断）丢弃。
    """
    turns: list[list[ChatMessage]] = []
    for message in messages:
        role, content = _role_content(message)
        if role not in _ROLES or not content.strip():
            continue
        item = ChatMessage(role, content)
        if role == "user" or not turns:
            turns.append([item])
        else:
            turns[-1].append(item)
    if turns and turns[0][0].role != "user":
        turns.pop(0)
    return turns


def build_history(
    messages: Sequence[Any],
    *,
    max_turns: int,
    max_tokens: int,
) -> list[ChatMessage]:
    """把会话消息裁剪为送入 LLM 的历史（旧 -> 新顺序）。

    一轮 = 一条 user 消息及其后的 assistant 回复；``max_turns`` 按 user 消息计数，
    因此被保留的最老一轮始终以 user 消息开头（孤立的 assistant 消息不会成为开头）。
    token 预算按整轮计：放不下的整轮不保留，且更老的轮次也一并舍弃。

    Args:
        messages: ``{"role","content"}`` 映射或带 ``role``/``content`` 属性的对象，旧 -> 新。
            非 user/assistant 角色与空内容被忽略。
        max_turns: 最多保留的轮数（>= 0）。
        max_tokens: 估算 token 上限（>= 0）。

    Raises:
        ValueError: 预算为负或非整数。
    """
    for name, value in (("max_turns", max_turns), ("max_tokens", max_tokens)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer, got {value!r}")

    turns = split_turns(messages)

    kept: list[list[ChatMessage]] = []
    used = 0
    for turn in reversed(turns):
        if len(kept) >= max_turns:
            break
        cost = sum(estimate_tokens(m.content) for m in turn)
        if used + cost > max_tokens:
            break
        kept.append(turn)
        used += cost

    return [m for turn in reversed(kept) for m in turn]
