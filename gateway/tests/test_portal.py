from unittest.mock import AsyncMock

import httpx
import pytest

from app import main, portal


@pytest.fixture
def configured(monkeypatch):
    for key, value in {
        "PORTAL_PASSWORD": "local-test-workspace-password",
        "PORTAL_SESSION_SECRET": "local-test-session-secret-with-32-characters",
        "PORTAL_MERCHANT_ID": "merchant_a",
        "PORTAL_ORIGIN": "http://127.0.0.1:8000",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(portal, "_failures", 0)
    redis = AsyncMock()
    redis.incr.return_value = 1
    monkeypatch.setattr(main, "redis_client", redis)
    return redis


@pytest.mark.asyncio
async def test_real_http_session_origin_tamper_and_logout(configured):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://127.0.0.1:8000"
    ) as client:
        login = {"password": "local-test-workspace-password"}
        response = await client.post("/portal/login", json=login, headers={"origin": "https://evil.example"})
        assert response.status_code == 403
        response = await client.post(
            "/portal/login", json={"password": "wrong"}, headers={"origin": "http://127.0.0.1:8000"}
        )
        assert response.status_code == 401
        response = await client.post("/portal/login", json=login, headers={"origin": "http://127.0.0.1:8000"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert (await client.get("/portal/session")).json()["authenticated"] is True
        assert (await client.get("/api/v1/nonexistent")).status_code == 404
        assert (
            await client.post("/api/v1/transactions", json={}, headers={"origin": "https://evil.example"})
        ).status_code == 403
        assert (await client.post("/portal/logout", headers={"origin": "http://127.0.0.1:8000"})).status_code == 200
        assert (await client.get("/api/v1/accounts")).status_code == 401
        client.cookies.set(portal.COOKIE, "forged.invalid")
        assert (await client.get("/api/v1/accounts")).status_code == 401


@pytest.mark.asyncio
async def test_missing_portal_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("PORTAL_SESSION_SECRET", raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://127.0.0.1:8000"
    ) as client:
        assert (await client.get("/portal/session")).json() == {"configured": False, "authenticated": False}
        assert (await client.post("/portal/login", json={"password": "anything"})).status_code == 503
