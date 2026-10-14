"""Chat 协议模型：WS 上行帧与会话 REST 响应（WEBUI_CHAT_SPEC §4.7）。

上行帧一律 ``extra="forbid"``：FastAPI/Pydantic 默认静默丢弃未知键（SX 规格 §4.7 教训），
对协议帧而言拼错字段名必须被拒绝而不是被忽略。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, TypeAdapter

__all__ = [
    "CancelFrame",
    "ChatCapabilities",
    "ChatMessageOut",
    "ChatSessionDetail",
    "ChatSessionSummary",
    "UpstreamFrame",
    "UserMessageFrame",
    "parse_upstream_frame",
]

RetrievalMode = Literal["hybrid", "agentic_fast", "agentic_deep"]


class UserMessageFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["user_message"]
    message: StrictStr = Field(min_length=1)
    session_id: StrictStr | None = None
    retrieval: RetrievalMode | None = None
    collection: StrictStr | None = None
    top_k: StrictInt | None = Field(default=None, ge=1)


class CancelFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["cancel"]


UpstreamFrame = Annotated[Union[UserMessageFrame, CancelFrame], Field(discriminator="type")]

_UPSTREAM = TypeAdapter(UpstreamFrame)


def parse_upstream_frame(payload: Any) -> UserMessageFrame | CancelFrame:
    """校验一个已 JSON 解码的上行帧；非法时抛 ``pydantic.ValidationError``。"""
    return _UPSTREAM.validate_python(payload)


class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    settings: dict[str, Any] = Field(default_factory=dict)


class ChatMessageOut(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    sources: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ChatSessionDetail(ChatSessionSummary):
    messages: list[ChatMessageOut] = Field(default_factory=list)


class ChatCapabilities(BaseModel):
    enabled: bool = Field(description="chat.enabled")
    retrievals: list[RetrievalMode] = Field(description="Retrieval modes the server can serve now")
    default_retrieval: RetrievalMode
    agentic_enabled: bool = Field(description="agentic_search.enabled")
    allow_deep: bool = Field(description="agentic_search.allow_deep; false -> agentic_deep fails with DEEP_DISABLED")
    progress_stream: bool = Field(description="chat.agentic_progress_stream")
    max_connections: int
