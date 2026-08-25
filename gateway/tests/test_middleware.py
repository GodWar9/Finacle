import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.idempotency import canonical_hash, IdempotencyMiddleware
from app.rate_limit import RateLimitMiddleware
from app.config import Settings

def test_canonical_hash_deterministic():
    body = {"a": 1, "b": "test", "c": [1, 2, 3]}
    hash1 = canonical_hash(body)
    hash2 = canonical_hash(body)
    assert hash1 == hash2
    assert len(hash1) == 64

def test_canonical_hash_different_order():
    body1 = {"a": 1, "b": 2}
    body2 = {"b": 2, "a": 1}
    assert canonical_hash(body1) == canonical_hash(body2)

def test_canonical_hash_different_values():
    body1 = {"amount": 100}
    body2 = {"amount": 200}
    assert canonical_hash(body1) != canonical_hash(body2)

@pytest.mark.asyncio
async def test_idempotency_middleware_replay_redis():
    mock_redis = AsyncMock()
    mock_pg_pool = AsyncMock()
    
    request_body = {"amount": 100, "currency": "INR"}
    req_hash = canonical_hash(request_body)
    
    mock_redis.get.return_value = json.dumps({
        "request_hash": req_hash,
        "response_body": {"transaction_id": "test-id", "status": "POSTED"},
        "status_code": 201
    })
    
    middleware = IdempotencyMiddleware(None, mock_redis, mock_pg_pool)
    
    mock_request = MagicMock()
    mock_request.method = "POST"
    mock_request.headers = {"Idempotency-Key": "test-key"}
    mock_request.json = AsyncMock(return_value=request_body)
    
    mock_call_next = AsyncMock()
    
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    assert response.status_code == 201
    mock_call_next.assert_not_called()
    mock_redis.get.assert_called_once_with("idem:test-key")

@pytest.mark.asyncio
async def test_idempotency_middleware_conflict():
    mock_redis = AsyncMock()
    mock_pg_pool = AsyncMock()
    
    request_body = {"amount": 100, "currency": "INR"}
    req_hash = canonical_hash(request_body)
    
    mock_redis.get.return_value = json.dumps({
        "request_hash": req_hash,
        "response_body": {"transaction_id": "test-id"},
        "status_code": 201
    })
    
    middleware = IdempotencyMiddleware(None, mock_redis, mock_pg_pool)
    
    mock_request = MagicMock()
    mock_request.method = "POST"
    mock_request.headers = {"Idempotency-Key": "test-key"}
    # Different body than cached
    mock_request.json = AsyncMock(return_value={"amount": 200, "currency": "INR"})
    
    mock_call_next = AsyncMock()
    
    with pytest.raises(Exception) as exc_info:
        await middleware.dispatch(mock_request, mock_call_next)
    
    assert exc_info.value.status_code == 409

@pytest.mark.asyncio
async def test_idempotency_middleware_missing_key():
    mock_redis = AsyncMock()
    mock_pg_pool = AsyncMock()
    
    middleware = IdempotencyMiddleware(None, mock_redis, mock_pg_pool)
    
    mock_request = MagicMock()
    mock_request.method = "POST"
    mock_request.headers = {}
    mock_request.json = AsyncMock(return_value={"amount": 100})
    
    mock_call_next = AsyncMock()
    
    with pytest.raises(Exception) as exc_info:
        await middleware.dispatch(mock_request, mock_call_next)
    
    assert exc_info.value.status_code == 400

@pytest.mark.asyncio
async def test_rate_limit_middleware():
    mock_redis = AsyncMock()
    mock_redis.incr.return_value = 1
    mock_redis.expire.return_value = True
    
    middleware = RateLimitMiddleware(None, mock_redis)
    
    mock_request = MagicMock()
    mock_request.headers = {"X-Merchant-ID": "merchant_1"}
    mock_request.state = MagicMock()
    mock_request.state.merchant_id = "merchant_1"
    
    mock_call_next = AsyncMock()
    mock_call_next.return_value = MagicMock(headers={})
    
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    mock_redis.incr.assert_called_once_with("ratelimit:merchant_1")
    assert "X-RateLimit-Limit" in response.headers