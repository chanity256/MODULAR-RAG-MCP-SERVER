"""LLM 抽象基类与统一消息/响应类型（阶段 B1）。

定义全链路共用的 ``BaseLLM`` 接口与 ``ChatMessage`` / ``ChatResponse`` 数据契约，
屏蔽不同 Provider（Azure / OpenAI / Ollama / DeepSeek）的认证与请求格式差异。

设计要点：
- ``chat(messages)`` 为统一调用入口，返回 ``ChatResponse``。
- 预留 ``trace: TraceContext | None`` 参数（阶段 F 打点时使用，当前仅占位）。
- 不在此处做任何网络/IO 的"业务初始化"，仅定义契约。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

__all__ = ["ChatMessage", "ChatResponse", "BaseLLM"]


@dataclass
class ChatMessage:
    """单条对话消息。

    Attributes:
        role: 消息角色，``system`` / ``user`` / ``assistant``。
        content: 消息文本内容。
    """

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class ChatResponse:
    """LLM 聊天调用的统一响应对象。

    Attributes:
        content: 模型生成的文本。
        model: 实际生成该响应的模型名（便于追踪/计费）。
        usage: 可选的 token 用量统计（如 ``{"prompt_tokens":..,"completion_tokens":..}``）。
        raw: 原始 provider 响应（便于调试，不参与序列化契约）。
    """

    content: str
    model: str | None = None
    usage: dict[str, Any] | None = None
    raw: Any = field(default=None, repr=False)


class BaseLLM(ABC):
    """LLM 抽象基类，所有 provider 实现需继承并实现 ``chat``。"""

    #: 最近一次 ``chat_stream`` 完成后的 token 用量（provider 未返回则为 ``None``）。
    last_usage: dict[str, Any] | None = None

    @abstractmethod
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """根据消息列表生成回复。

        Args:
            messages: 对话消息序列。
            trace: 可选的 ``TraceContext``，用于阶段 F 打点（当前占位）。
            **kwargs: provider 特定的扩展参数（如 ``temperature``/``max_tokens`` 覆盖）。

        Returns:
            统一的 ``ChatResponse``。
        """
        raise NotImplementedError

    def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """流式生成回复，逐段 yield 文本增量。

        默认实现回退到同步 ``chat()`` 并整块 yield 一次，因此不支持流式的
        后端无需改动即可被流式调用方使用；真流式后端覆盖本方法。

        约定：
        - 中途失败抛 ``LLMRequestError``（调用方据 ``status`` 分类）。
        - 流结束后 ``last_usage`` 为 provider 返回的用量（无则 ``None``）。
        """
        self.last_usage = None
        response = self.chat(messages, trace=trace, **kwargs)
        self.last_usage = response.usage
        if response.content:
            yield response.content

    def chat_text(
        self,
        text: str,
        *,
        system: str | None = None,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> str:
        """便捷方法：把单条文本（可选 system prompt）转为消息并返回纯文本。"""
        msgs: list[ChatMessage] = []
        if system:
            msgs.append(ChatMessage("system", system))
        msgs.append(ChatMessage("user", text))
        return self.chat(msgs, trace=trace, **kwargs).content
