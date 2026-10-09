from unittest.mock import AsyncMock

import httpx
import pytest

from app import main


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_unauthenticated_http_requests_cannot_reach_cache(monkeypatch, method):
    redis = AsyncMock()
    monkeypatch.setattr(main, "redis_client", redis)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.request(
            method, "/api/v1/transactions", headers={"Idempotency-Key": "existing"}, json={}
        )
    assert response.status_code == 401
    assert response.json()["detail"] == "X-API-Key header is required"
    redis.get.assert_not_awaited()
    redis.incr.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_key_is_an_http_401_not_a_server_error():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/accounts", headers={"X-API-Key": "invalid-readiness-key"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_portal_assets_do_not_require_browser_api_keys():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get("/css/tokens.css")
    # Native tests do not copy Docker's static directory; 404 is expected there.
    assert response.status_code in (200, 404)


@pytest.mark.asyncio
@pytest.mark.parametrize(("count", "expected"), [(1, 404), (1000000, 429)])
async def test_rate_limit_uses_authenticated_merchant(monkeypatch, count, expected):
    from app.auth import AuthMiddleware

    # Build the same middleware stack used by the deployed application.
    if main.app.middleware_stack is None:
        main.app.middleware_stack = main.app.build_middleware_stack()
    layer = main.app.middleware_stack
    while not isinstance(layer, AuthMiddleware):
        layer = layer.app
    monkeypatch.setattr(layer, "api_keys", {"readiness-key": "merchant_readiness"})
    redis = AsyncMock()
    redis.incr.return_value = count
    monkeypatch.setattr(main, "redis_client", redis)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/nonexistent-readiness-route", headers={"X-API-Key": "readiness-key", "X-Merchant-ID": "spoofed"}
        )
    assert response.status_code == expected
    redis.incr.assert_awaited_once_with("ratelimit:merchant_readiness")
