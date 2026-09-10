import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.auth import AuthMiddleware, verify_webhook_signature


@pytest.fixture
def mock_redis():
    return AsyncMock()


@pytest.mark.asyncio
async def test_auth_middleware_allows_health_endpoints():
    middleware = AuthMiddleware(None)
    middleware.api_keys = {"test-key": "merchant_1"}

    mock_request = MagicMock()
    mock_request.url.path = "/health"
    mock_request.headers = {}

    mock_call_next = AsyncMock(return_value=MagicMock())

    await middleware.dispatch(mock_request, mock_call_next)
    mock_call_next.assert_called_once()


@pytest.mark.asyncio
async def test_auth_middleware_rejects_missing_key():
    middleware = AuthMiddleware(None)
    middleware.api_keys = {"test-key": "merchant_1"}

    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/transactions"
    mock_request.headers = {}

    mock_call_next = AsyncMock()

    with pytest.raises(Exception) as exc_info:
        await middleware.dispatch(mock_request, mock_call_next)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_middleware_rejects_invalid_key():
    middleware = AuthMiddleware(None)
    middleware.api_keys = {"test-key": "merchant_1"}

    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/transactions"
    mock_request.headers = {"X-API-Key": "invalid-key"}

    mock_call_next = AsyncMock()

    with pytest.raises(Exception) as exc_info:
        await middleware.dispatch(mock_request, mock_call_next)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_middleware_allows_valid_key():
    middleware = AuthMiddleware(None)
    middleware.api_keys = {"test-key": "merchant_1"}

    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/transactions"
    mock_request.headers = {"X-API-Key": "test-key"}
    mock_request.state = MagicMock()

    mock_call_next = AsyncMock(return_value=MagicMock())

    await middleware.dispatch(mock_request, mock_call_next)
    assert mock_request.state.merchant_id == "merchant_1"
    assert mock_request.state.api_key == "test-key"


def test_verify_webhook_signature_valid():
    payload = b'{"test": "data"}'
    secret = "webhook-secret"
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(payload, expected, secret) is True


def test_verify_webhook_signature_invalid():
    payload = b'{"test": "data"}'
    secret = "webhook-secret"
    assert verify_webhook_signature(payload, "invalid-signature", secret) is False
