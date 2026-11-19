"""System One 请求契约与 Cloudflare Workers AI（Clef）后端（MODE_ROUTER_SPEC MR2 / §3.6）。

请求体（Clef 与 Jev 相同，见 §3.6）::

    { model, state: <改写后的 search_query>,
      questions: { retrieval_mode: { type: "choice", instructions, criteria: {候选 → 描述} } } }

答案取 ``answers.retrieval_mode``：``{type, choice, probabilities, confidence}``。
响应外层包装由后端决定：``cloudflare_workers_ai`` 带 Workers AI 的
``{result, success, errors, messages}`` 包装（2026-11-18 实测确认）；解析
同时接受有包装与无包装两种形态。``typesafe``（MR6）复用本模块的构造与解析，
只换端点、鉴权与无包装响应。
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

from libs.mode_router.base_mode_router import BaseModeRouter, ModeRouterError
from libs.mode_router.routing_question import DEFAULT_WORDING, instructions_and_criteria

__all__ = [
    "QUESTION_KEY",
    "CLOUDFLARE_ENDPOINT_TEMPLATE",
    "build_system_one_request",
    "parse_system_one_response",
    "CloudflareWorkersAIRouter",
]

QUESTION_KEY = "retrieval_mode"
CLOUDFLARE_ENDPOINT_TEMPLATE = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/{model}"
)


def build_system_one_request(
    query: str,
    *,
    model: str,
    candidates: tuple[str, ...],
    lang: str = DEFAULT_WORDING,
) -> dict[str, Any]:
    """构造 System One ``choice`` 请求；``criteria`` 的键恰为候选集。"""
    instructions, criteria = instructions_and_criteria(tuple(candidates), lang)
    return {
        "model": model,
        "state": query,
        "questions": {
            QUESTION_KEY: {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria,
            }
        },
    }


def parse_system_one_response(payload: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """从响应体提取 ``(answers.retrieval_mode, usage | None)``。

    - 带 Workers AI 包装（``result`` + ``success``/``errors``）：``success: false``
      → ``ModeRouterError("error")``，否则解包 ``result``；
    - 无包装形态直接取 ``answers.retrieval_mode``；
    - 缺答案、类型不是 ``choice`` → ``ModeRouterError("invalid_output")``。
    """
    body = payload
    if (
        isinstance(payload, dict)
        and "result" in payload
        and ("success" in payload or "errors" in payload)
    ):
        if payload.get("success") is False:
            errors = payload.get("errors") or payload.get("messages") or "unknown error"
            raise ModeRouterError("error", f"decision service reported failure: {errors}")
        body = payload.get("result")
    if not isinstance(body, dict):
        raise ModeRouterError("invalid_output", "decision response body is not an object")

    usage = body.get("usage")
    answers = body.get("answers")
    answer = answers.get(QUESTION_KEY) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ModeRouterError(
            "invalid_output", f"missing answers.{QUESTION_KEY} of type 'choice'"
        )
    return answer, usage if isinstance(usage, dict) else None


class CloudflareWorkersAIRouter(BaseModeRouter):
    """Cloudflare Workers AI 上的 Clef（同步 httpx 客户端）。

    - 端点按 ``account_id`` 与 ``model`` 拼接（``base_url`` 覆盖，如 AI Gateway）；
    - ``Authorization: Bearer <Workers AI API token>``；
    - 客户端实例级懒建并复用（MR0.2 实测复用连接可把 p50 从 ~1 s 降到
      ~0.35 s），线程安全；``close()`` 释放。
    """

    backend_name = "cloudflare_workers_ai"

    def __init__(
        self,
        *,
        account_id: str,
        api_token: str,
        model: str = "clef-flash",
        base_url: str | None = None,
        timeout_s: float = 3.0,
        min_confidence: float | None = None,
        wording_lang: str = DEFAULT_WORDING,
        transport: Any | None = None,
    ) -> None:
        """Args:
            account_id: Cloudflare Account ID（配置已校验非空）。
            api_token: Workers AI API token（配置已校验非空）。
            model: ``clef`` | ``clef-flash``。
            base_url: 可选端点覆盖（AI Gateway）。
            timeout_s: 单次决策超时（秒），超时抛 ``timeout``。
            min_confidence: D8 低置信度阈值；None 不判。
            wording_lang: criteria 文案语言（``routing_question.WORDINGS`` 键）。
            transport: 可注入 httpx transport（测试 MockTransport 用）。
        """
        super().__init__(
            model=model, timeout_s=timeout_s, min_confidence=min_confidence, wording_lang=wording_lang
        )
        self._url = base_url or CLOUDFLARE_ENDPOINT_TEMPLATE.format(
            account_id=account_id, model=model
        )
        self._headers = {"Authorization": f"Bearer {api_token}"}
        self._transport = transport
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()

    @property
    def endpoint(self) -> str:
        return self._url

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(timeout=self.timeout_s, transport=self._transport)
            return self._client

    def _answers(self, query: str, candidates: tuple[str, ...]) -> dict[str, Any]:
        body = build_system_one_request(
            query, model=self.model, candidates=candidates, lang=self.wording_lang
        )
        try:
            response = self._get_client().post(self._url, json=body, headers=self._headers)
        except httpx.TimeoutException as exc:
            raise ModeRouterError(
                "timeout", f"decision request timed out after {self.timeout_s}s: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise ModeRouterError("unreachable", f"decision service unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ModeRouterError(
                "error",
                f"decision service returned HTTP {response.status_code}: {response.text[:200]}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModeRouterError("invalid_output", "decision response is not JSON") from exc
        answer, usage = parse_system_one_response(payload)
        tokens = usage.get("input_tokens") if usage else None
        if isinstance(tokens, int) and not isinstance(tokens, bool):
            self.last_input_tokens = tokens
        return answer
