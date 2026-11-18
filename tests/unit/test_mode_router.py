"""MR2: libs/mode_router 组件离线单测（httpx.MockTransport，零网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from core.chat.retriever import RETRIEVAL_AGENTIC_FAST, RETRIEVAL_HYBRID
from core.settings import ConfigError, load_settings
from libs.mode_router import (
    BaseModeRouter,
    CloudflareWorkersAIRouter,
    ModeRouterError,
    build_system_one_request,
    create_mode_router,
    parse_system_one_response,
)
from libs.mode_router.routing_question import instructions_and_criteria

_MINIMAL_YAML = """\
llm:
  provider: azure
  model: gpt-4o
embedding:
  provider: openai
  model: text-embedding-3-small
vision_llm:
  provider: azure
  model: gpt-4o
vector_store:
  backend: chroma
retrieval:
  sparse_backend: bm25
  fusion_algorithm: rrf
rerank:
  backend: none
evaluation:
  backends: [custom]
"""

MODES = (RETRIEVAL_HYBRID, RETRIEVAL_AGENTIC_FAST, "agentic_deep")
URL = (
    "https://api.cloudflare.com/client/v4/accounts/acct-test/ai/run/@cf/cloudflare/clef-flash"
)


def _wrapped_answer(
    choice: str = "hybrid",
    *,
    confidence: float | None = 0.9,
    probabilities: dict | None = None,
    input_tokens: int = 200,
) -> dict:
    answer: dict = {"type": "choice", "choice": choice}
    if confidence is not None:
        answer["confidence"] = confidence
    if probabilities is not None:
        answer["probabilities"] = probabilities
    return {
        "result": {
            "model": "clef-flash",
            "answers": {"retrieval_mode": answer},
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
        "success": True,
        "errors": [],
        "messages": [],
    }


def _write_yaml(tmp_path: Path, section: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML + section, encoding="utf-8")
    return path


def _router(handler, **overrides) -> CloudflareWorkersAIRouter:
    defaults = dict(
        account_id="acct-test",
        api_token="tok-test",
        model="clef-flash",
        transport=httpx.MockTransport(handler),
    )
    defaults.update(overrides)
    return CloudflareWorkersAIRouter(**defaults)


class TestRoutingQuestion:
    def test_criteria_keys_exactly_candidates_in_order(self):
        instructions, criteria = instructions_and_criteria(("agentic_fast", "hybrid"))
        assert list(criteria) == ["agentic_fast", "hybrid"]
        assert instructions

    def test_unknown_candidate_rejected(self):
        with pytest.raises(ValueError, match="criteria text"):
            instructions_and_criteria(("hybrid", "auto"))

    def test_unknown_lang_rejected(self):
        with pytest.raises(ValueError, match="wording lang"):
            instructions_and_criteria(MODES, lang="fr")


class TestSystemOneContract:
    def test_request_shape(self):
        body = build_system_one_request(
            "什么是 LoRA", model="clef-flash", candidates=MODES
        )
        assert body["model"] == "clef-flash"
        assert body["state"] == "什么是 LoRA"
        question = body["questions"]["retrieval_mode"]
        assert question["type"] == "choice"
        assert set(question["criteria"]) == set(MODES)

    def test_parse_wrapped_and_unwrapped(self):
        answer, usage = parse_system_one_response(_wrapped_answer(input_tokens=210))
        assert answer["choice"] == "hybrid"
        assert usage["input_tokens"] == 210
        # 无包装形态（如 MR7 本地 System One 端点）同样解析，usage 若存在也一并返回。
        unwrapped, usage2 = parse_system_one_response(_wrapped_answer(input_tokens=7)["result"])
        assert unwrapped["choice"] == "hybrid"
        assert usage2["input_tokens"] == 7
        bare, no_usage = parse_system_one_response(
            {"answers": {"retrieval_mode": {"type": "choice", "choice": "hybrid"}}}
        )
        assert bare["choice"] == "hybrid"
        assert no_usage is None

    def test_parse_success_false_is_error(self):
        payload = {"result": None, "success": False, "errors": [{"code": 1}], "messages": []}
        with pytest.raises(ModeRouterError, match="failure") as exc_info:
            parse_system_one_response(payload)
        assert exc_info.value.reason == "error"

    @pytest.mark.parametrize(
        "payload",
        [
            {"answers": {}},
            {"answers": {"retrieval_mode": {"type": "boolean", "value": True}}},
            "not a dict",
            None,
        ],
    )
    def test_parse_invalid_output(self, payload):
        with pytest.raises(ModeRouterError) as exc_info:
            parse_system_one_response(payload)
        assert exc_info.value.reason == "invalid_output"


class TestCloudflareWorkersAIRouter:
    def test_decide_returns_routing_decision(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_wrapped_answer(
                    "agentic_deep",
                    confidence=0.74,
                    probabilities={"hybrid": 0.1, "agentic_fast": 0.1, "agentic_deep": 0.8},
                    input_tokens=205,
                ),
            )

        router = _router(handler)
        decision = router.decide("compare three files", MODES)
        assert decision.mode == "agentic_deep"
        assert decision.confidence == pytest.approx(0.74)
        assert decision.probabilities is not None
        assert decision.probabilities["agentic_deep"] == pytest.approx(0.8)
        assert router.last_input_tokens == 205
        router.close()

    def test_request_url_headers_and_body(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=_wrapped_answer())

        router = _router(handler)
        router.decide("什么是 MCP", MODES)
        assert seen["url"] == URL
        assert seen["auth"] == "Bearer tok-test"
        assert seen["body"]["state"] == "什么是 MCP"
        assert set(seen["body"]["questions"]["retrieval_mode"]["criteria"]) == set(MODES)
        router.close()

    def test_base_url_override(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=_wrapped_answer())

        router = _router(handler, base_url="http://127.0.0.1:9999/gateway")
        router.decide("q", MODES)
        assert seen["url"] == "http://127.0.0.1:9999/gateway"
        assert router.endpoint == "http://127.0.0.1:9999/gateway"
        router.close()

    def test_candidates_subset_restricts_criteria(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=_wrapped_answer("hybrid"))

        router = _router(handler)
        router.decide("q", (RETRIEVAL_HYBRID, RETRIEVAL_AGENTIC_FAST))
        assert set(seen["body"]["questions"]["retrieval_mode"]["criteria"]) == {
            RETRIEVAL_HYBRID,
            RETRIEVAL_AGENTIC_FAST,
        }
        router.close()

    @pytest.mark.parametrize(
        "payload, reason",
        [
            ({"result": None, "success": False, "errors": [{"code": 1}], "messages": []}, "error"),
            ("not json", "invalid_output"),
        ],
    )
    def test_error_classification(self, payload, reason):
        def handler(request: httpx.Request) -> httpx.Response:
            if payload == "not json":
                return httpx.Response(200, text="not json")
            return httpx.Response(200, json=payload)

        router = _router(handler)
        with pytest.raises(ModeRouterError) as exc_info:
            router.decide("q", MODES)
        assert exc_info.value.reason == reason
        router.close()

    def test_http_error_is_error(self):
        router = _router(lambda request: httpx.Response(500, text="boom"))
        with pytest.raises(ModeRouterError, match="HTTP 500") as exc_info:
            router.decide("q", MODES)
        assert exc_info.value.reason == "error"
        router.close()

    def test_timeout_and_unreachable(self):
        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        router = _router(timeout_handler)
        with pytest.raises(ModeRouterError) as exc_info:
            router.decide("q", MODES)
        assert exc_info.value.reason == "timeout"
        router.close()

        def connect_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        router = _router(connect_handler)
        with pytest.raises(ModeRouterError) as exc_info:
            router.decide("q", MODES)
        assert exc_info.value.reason == "unreachable"
        router.close()

    @pytest.mark.parametrize(
        "answer_kwargs, reason",
        [
            ({"choice": "auto", "confidence": 0.9}, "invalid_output"),  # 候选外
            ({"choice": "hybrid", "confidence": "high"}, "invalid_output"),  # confidence 非数值
            ({"choice": "hybrid", "confidence": 0.9, "probabilities": "high"}, "invalid_output"),
            ({"choice": "hybrid", "confidence": 0.9, "probabilities": {"hybrid": "x"}}, "invalid_output"),
        ],
    )
    def test_decide_invalid_outputs(self, answer_kwargs, reason):
        router = _router(lambda request: httpx.Response(200, json=_wrapped_answer(**answer_kwargs)))
        with pytest.raises(ModeRouterError) as exc_info:
            router.decide("q", MODES)
        assert exc_info.value.reason == reason
        router.close()

    def test_low_confidence_fallback_reason(self):
        router = _router(
            lambda request: httpx.Response(200, json=_wrapped_answer("hybrid", confidence=0.2)),
            min_confidence=0.3,
        )
        with pytest.raises(ModeRouterError) as exc_info:
            router.decide("q", MODES)
        assert exc_info.value.reason == "low_confidence"
        router.close()

    def test_min_confidence_none_does_not_judge(self):
        router = _router(
            lambda request: httpx.Response(200, json=_wrapped_answer("hybrid", confidence=0.05)),
        )
        assert router.decide("q", MODES).confidence == pytest.approx(0.05)
        router.close()

    def test_empty_candidates_rejected(self):
        router = _router(lambda request: httpx.Response(200, json=_wrapped_answer()))
        with pytest.raises(ModeRouterError) as exc_info:
            router.decide("q", ())
        assert exc_info.value.reason == "invalid_output"
        router.close()


class TestFactory:
    def test_disabled_returns_none(self, tmp_path: Path):
        settings = load_settings(_write_yaml(tmp_path, "mode_router:\n  enabled: false\n"))
        assert create_mode_router(settings) is None

    def test_missing_section_returns_none(self, tmp_path: Path):
        settings = load_settings(_write_yaml(tmp_path, ""))
        assert create_mode_router(settings) is None

    def test_enabled_builds_cloudflare_router(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-1")
        monkeypatch.setenv("MODE_ROUTER_API_TOKEN", "tok-1")
        yaml = (
            "mode_router:\n"
            "  enabled: true\n"
            "  model: clef\n"
            "  timeout_s: 5\n"
            "  min_confidence: 0.3\n"
            "  account_id: ${CLOUDFLARE_ACCOUNT_ID}\n"
            "  api_token: ${MODE_ROUTER_API_TOKEN}\n"
        )
        router = create_mode_router(load_settings(_write_yaml(tmp_path, yaml)))
        assert isinstance(router, CloudflareWorkersAIRouter)
        assert isinstance(router, BaseModeRouter)
        assert router.model == "clef"
        assert router.timeout_s == 5.0
        assert router.min_confidence == pytest.approx(0.3)
        assert router.endpoint.endswith("/accounts/acct-1/ai/run/@cf/cloudflare/clef")
        router.close()
