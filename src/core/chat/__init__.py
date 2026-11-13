"""Chat 核心（WC5+）：有据回答生成、多轮上下文处理与会话编排。

分层纪律：本包不得 import ``mcp_server`` 与 ``observability``；检索能力经协议注入。
"""

from core.chat.answer_generator import AnswerGenerator
from core.chat.context import ContextProcessor
from core.chat.engine import ChatEngine, classify_error
from core.chat.history import build_history
from core.chat.prompts import load_prompt
from core.chat.retriever import ChatRetrievalResult, ChatRetriever, DeepDisabledError
from core.chat.session_store import SessionNotFoundError, SessionStore

__all__ = [
    "AnswerGenerator",
    "ChatEngine",
    "ChatRetrievalResult",
    "ChatRetriever",
    "ContextProcessor",
    "DeepDisabledError",
    "SessionNotFoundError",
    "SessionStore",
    "build_history",
    "classify_error",
    "load_prompt",
]
