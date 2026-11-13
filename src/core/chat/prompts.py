"""Chat prompt 模板加载（``config/prompts/chat_*.txt``）。

模板以文件形式维护，便于不改代码调整措辞。相对路径锚定仓库根而非 cwd，
这样 API 服务从任意目录启动都能找到默认模板。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["PROMPT_ANSWER", "PROMPT_RELEVANCE", "PROMPT_REWRITE", "PROMPTS_DIR", "load_prompt"]

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "config" / "prompts"

#: chat_answer.txt 占位符：``{context}``（编号上下文）。
PROMPT_ANSWER = "chat_answer"
#: chat_relevance.txt 占位符：``{history}``、``{question}``。
PROMPT_RELEVANCE = "chat_relevance"
#: chat_rewrite.txt 占位符：``{history}``、``{question}``。
PROMPT_REWRITE = "chat_rewrite"


def load_prompt(name: str, *, prompts_dir: str | Path | None = None) -> str:
    """读取 ``<prompts_dir>/<name>.txt``。

    Raises:
        FileNotFoundError: 模板文件不存在。
    """
    path = Path(prompts_dir) / f"{name}.txt" if prompts_dir else PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"chat prompt file not found: {path}")
    return path.read_text(encoding="utf-8")
