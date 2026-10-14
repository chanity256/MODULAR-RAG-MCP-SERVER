"""Chat 核心（WC5+）：有据回答生成、多轮上下文处理与会话编排。

分层纪律：本包不得 import ``mcp_server`` 与 ``observability``；检索能力经协议注入。
"""

from core.chat.answer_generator import AnswerGenerator
from core.chat.prompts import load_prompt

__all__ = ["AnswerGenerator", "load_prompt"]
