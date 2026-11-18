"""libs.mode_router — 检索模式路由器（MODE_ROUTER_SPEC MR2）。

决策模型经进程外 HTTP 端点调用（首期 Cloudflare Clef），主 venv 不加
推理依赖；``enabled=false`` 时工厂返回 None、零加载。组件只做失败分型
（:class:`ModeRouterError`），回退语义归 ChatEngine（MR3）。
"""

from __future__ import annotations

from libs.mode_router.base_mode_router import (
    ROUTER_ERROR_REASONS,
    BaseModeRouter,
    ModeRouterError,
)
from libs.mode_router.mode_router_factory import create_mode_router
from libs.mode_router.routing_question import (
    DEFAULT_WORDING,
    WORDINGS,
    instructions_and_criteria,
)
from libs.mode_router.system_one_router import (
    CLOUDFLARE_ENDPOINT_TEMPLATE,
    QUESTION_KEY,
    CloudflareWorkersAIRouter,
    build_system_one_request,
    parse_system_one_response,
)

__all__ = [
    "ROUTER_ERROR_REASONS",
    "BaseModeRouter",
    "ModeRouterError",
    "create_mode_router",
    "DEFAULT_WORDING",
    "WORDINGS",
    "instructions_and_criteria",
    "CLOUDFLARE_ENDPOINT_TEMPLATE",
    "QUESTION_KEY",
    "CloudflareWorkersAIRouter",
    "build_system_one_request",
    "parse_system_one_response",
]
