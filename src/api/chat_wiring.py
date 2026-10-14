"""Chat 的服务装配（WC7.1）：SessionStore / 检索器 / 每轮引擎 / capabilities。

与 ``ServiceContainer`` 解耦：容器只持有一个 :class:`ChatServices` 句柄；所有成员都可注入，
测试直接给 fake 引擎或引擎工厂，生产路径按 ``Settings`` 懒构建（首次使用才触碰存储/LLM）。
"""

from __future__ import annotations

import threading
from functools import partial
from typing import Any, Callable, Mapping

from core.chat import ChatEngine, SessionStore
from core.settings import ChatSettings, Settings
from core.trace.persist import persist_trace

__all__ = ["ChatServices", "compute_capabilities"]

_ALL_MODES = ("hybrid", "agentic_fast", "agentic_deep")


def compute_capabilities(
    settings: Settings | None,
    available: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """``GET /chat/capabilities`` 的载荷；``available`` 缺省按配置推断。"""
    chat: ChatSettings = settings.chat if settings is not None else ChatSettings()
    agentic = getattr(settings, "agentic_search", None)
    agentic_enabled = bool(getattr(agentic, "enabled", False))
    # agentic 整体关闭时 DEEP 自然不可用，不泄露 allow_deep 的原始配置值。
    allow_deep = agentic_enabled and bool(getattr(agentic, "allow_deep", False))
    if available is None:
        available = _ALL_MODES if agentic_enabled else ("hybrid",)
    return {
        "enabled": chat.enabled,
        "retrievals": [m for m in _ALL_MODES if m in available],
        "default_retrieval": chat.default_retrieval,
        "agentic_enabled": agentic_enabled,
        "allow_deep": allow_deep,
        "progress_stream": chat.agentic_progress_stream,
        "max_connections": chat.max_connections,
    }


class ChatServices:
    """Chat 相关单例与每轮引擎工厂。

    Args:
        settings: 全局配置（注入全部成员时可省略）。
        session_store: 会话存储（缺省按 ``chat.session_db`` 懒建）。
        engine: 固定引擎（测试 fake）；给定时 :meth:`new_engine` 恒返回它。
        engine_factory: 无参工厂，每轮调用一次（测试可注入；优先级低于 ``engine``）。
        retrievers: 检索器字典（缺省按配置经 ``build_chat_retrievers`` 懒建并复用）。
        llm_factory: ``settings -> BaseLLM``，缺省走项目的 ``LLMFactory``。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session_store: SessionStore | None = None,
        engine: Any | None = None,
        engine_factory: Callable[[], Any] | None = None,
        retrievers: Mapping[str, Any] | None = None,
        llm_factory: Callable[[Settings], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._store = session_store
        self._engine = engine
        self._engine_factory = engine_factory
        self._retrievers = dict(retrievers) if retrievers is not None else None
        self._llm_factory = llm_factory
        self._lock = threading.Lock()

    @property
    def chat_settings(self) -> ChatSettings:
        return self._settings.chat if self._settings is not None else ChatSettings()

    @property
    def session_store(self) -> SessionStore:
        with self._lock:
            if self._store is None:
                if self._settings is None:
                    raise RuntimeError("chat session store requires settings or an injected SessionStore")
                self._store = SessionStore(self._settings.chat.session_db)
            return self._store

    def new_engine(self) -> Any:
        """返回本轮使用的引擎。

        ``ChatEngine`` 读取 ``llm.last_usage``（实例级共享状态），所以生产路径**每轮新建**
        LLM 与引擎（构造廉价；检索器与 store 共享），避免并发轮次的 usage 互相串线。
        """
        if self._engine is not None:
            return self._engine
        if self._engine_factory is not None:
            return self._engine_factory()
        settings = self._settings
        if settings is None:
            raise RuntimeError("chat engine requires settings or an injected engine")
        if self._llm_factory is not None:
            llm = self._llm_factory(settings)
        else:
            import libs.llm  # noqa: F401  # 触发 provider 注册
            from libs.llm.llm_factory import LLMFactory

            llm = LLMFactory.create(settings)
        return ChatEngine(
            retrievers=self._get_retrievers(),
            llm=llm,
            session_store=self.session_store,
            settings=settings.chat,
            persist_trace=partial(persist_trace, log_file=settings.observability.log_file),
        )

    def capabilities(self) -> dict[str, Any]:
        available: tuple[str, ...] | None = None
        if self._engine is not None:
            available = tuple(getattr(self._engine, "available_retrievals", ()) or ("hybrid",))
        elif self._retrievers is not None:
            available = tuple(self._retrievers)
        return compute_capabilities(self._settings, available)

    def _get_retrievers(self) -> Mapping[str, Any]:
        with self._lock:
            if self._retrievers is None:
                from observability.dashboard.services.chat_retrievers import build_chat_retrievers

                self._retrievers = build_chat_retrievers(self._settings)
            return self._retrievers
