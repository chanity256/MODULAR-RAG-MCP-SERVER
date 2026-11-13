"""DRILL (do not merge): a unit test making a real HTTP request (AI_NATIVE_SDLC_SPEC SD1.5)."""

import httpx


def test_planted_real_http_request():
    httpx.get("https://example.com", timeout=10)
