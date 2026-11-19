"""mode_router 装配工厂（MODE_ROUTER_SPEC MR2）。"""

from __future__ import annotations

from core.settings import ConfigError, Settings
from libs.mode_router.base_mode_router import BaseModeRouter
from libs.mode_router.system_one_router import CloudflareWorkersAIRouter

__all__ = ["create_mode_router"]


def create_mode_router(settings: Settings) -> BaseModeRouter | None:
    """按 ``mode_router`` 配置装配路由器；``enabled=false`` 返回 None（零加载）。"""
    cfg = settings.mode_router
    if not cfg.enabled:
        return None
    if cfg.backend == "cloudflare_workers_ai":
        return CloudflareWorkersAIRouter(
            account_id=cfg.account_id or "",
            api_token=cfg.api_token or "",
            model=cfg.model,
            base_url=cfg.base_url,
            timeout_s=cfg.timeout_s,
            min_confidence=cfg.min_confidence,
        )
    # settings 校验已拦截未交付 backend；此分支防御配置被程序化改写后的漏网值。
    raise ConfigError(f"mode_router backend {cfg.backend!r} is not implemented yet")
