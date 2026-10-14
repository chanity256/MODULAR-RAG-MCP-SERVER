"""WC6.1: build_history 纯函数（双预算、边界、空历史）。"""

from __future__ import annotations

import pytest

from core.chat.history import build_history, estimate_tokens, split_turns
from libs.llm.base_llm import ChatMessage


def _conv(n: int, text: str = "x") -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    for i in range(1, n + 1):
        msgs.append({"role": "user", "content": f"q{i}{text}"})
        msgs.append({"role": "assistant", "content": f"a{i}{text}"})
    return msgs


def _contents(msgs):
    return [m.content for m in msgs]


def test_estimate_tokens_is_len_over_3_5_ceiling():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("a" * 7) == 2
    assert estimate_tokens("a" * 8) == 3


def test_empty_history():
    assert build_history([], max_turns=10, max_tokens=1000) == []


def test_returns_chat_messages_oldest_first():
    out = build_history(_conv(2, ""), max_turns=10, max_tokens=1000)
    assert all(isinstance(m, ChatMessage) for m in out)
    assert _contents(out) == ["q1", "a1", "q2", "a2"]


def test_max_turns_keeps_most_recent():
    out = build_history(_conv(5, ""), max_turns=2, max_tokens=10_000)
    assert _contents(out) == ["q4", "a4", "q5", "a5"]


def test_max_turns_zero_and_exact_boundary():
    assert build_history(_conv(3, ""), max_turns=0, max_tokens=10_000) == []
    assert len(build_history(_conv(3, ""), max_turns=3, max_tokens=10_000)) == 6


def test_max_tokens_trims_oldest_whole_turns():
    # 每轮 "qNxxxxxx"(8 字符 -> 3) + "aNxxxxxx"(8 -> 3) = 6 token
    msgs = _conv(4, "xxxxxx")
    out = build_history(msgs, max_turns=10, max_tokens=12)
    assert _contents(out) == ["q3xxxxxx", "a3xxxxxx", "q4xxxxxx", "a4xxxxxx"]
    out_exact = build_history(msgs, max_turns=10, max_tokens=18)
    assert len(out_exact) == 6
    assert build_history(msgs, max_turns=10, max_tokens=5) == []


def test_token_budget_stops_at_first_overflow_even_if_older_turn_is_small():
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "q" * 700},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    out = build_history(msgs, max_turns=10, max_tokens=50)
    assert _contents(out) == ["q", "a"]


def test_both_budgets_apply_whichever_is_tighter():
    msgs = _conv(10, "xxxxxx")
    assert len(build_history(msgs, max_turns=2, max_tokens=10_000)) == 4
    assert len(build_history(msgs, max_turns=10, max_tokens=6)) == 2


def test_accepts_attribute_objects_and_ignores_other_roles_and_blank():
    msgs = [
        ChatMessage("system", "sys"),
        ChatMessage("user", "hi"),
        {"role": "assistant", "content": "  "},
        ChatMessage("assistant", "hello"),
    ]
    assert _contents(build_history(msgs, max_turns=5, max_tokens=100)) == ["hi", "hello"]


def test_leading_orphan_assistant_is_dropped():
    msgs = [{"role": "assistant", "content": "orphan"}, *_conv(1, "")]
    assert _contents(build_history(msgs, max_turns=5, max_tokens=100)) == ["q1", "a1"]


def test_user_without_reply_is_its_own_turn():
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    assert [len(t) for t in split_turns(msgs)] == [1, 2]
    assert _contents(build_history(msgs, max_turns=1, max_tokens=100)) == ["q2", "a2"]


@pytest.mark.parametrize("kwargs", [{"max_turns": -1, "max_tokens": 1}, {"max_turns": 1, "max_tokens": -1},
                                     {"max_turns": True, "max_tokens": 1}, {"max_turns": 1.5, "max_tokens": 1}])
def test_invalid_budgets_rejected(kwargs):
    with pytest.raises(ValueError):
        build_history([], **kwargs)


def test_does_not_mutate_input():
    msgs = _conv(3)
    snapshot = [dict(m) for m in msgs]
    build_history(msgs, max_turns=1, max_tokens=5)
    assert msgs == snapshot
