import pytest
import asyncio
from uuid import uuid4, UUID
from decimal import Decimal

from app.config import get_settings
from app.idempotency import canonical_hash, IdempotencyMiddleware
from app.rate_limit import RateLimitMiddleware

settings = get_settings()

@pytest.fixture
def mock_redis():
    from unittest.mock import AsyncMock, MagicMock
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=True)
    return redis

@pytest.fixture
def mock_pg_pool():
    from unittest.mock import AsyncMock, MagicMock
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire = AsyncMock()
    pool.acquire.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.__aexit__ = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    return pool

@pytest.mark.asyncio
async def test_full_idempotency_flow(mock_redis, mock_pg_pool):
    """Test complete idempotency flow: first request -> cache -> replay"""
    middleware = IdempotencyMiddleware(None, mock_redis, mock_pg_pool)
    
    request_body = {
        "transaction_type": "PAYMENT",
        "reference_id": "order_123",
        "entries": [
            {"account_id": str(uuid4()), "direction": "DEBIT", "amount_minor": 10000, "currency": "INR"},
            {"account_id": str(uuid4()), "direction": "CREDIT", "amount_minor": 10000, "currency": "INR"}
        ],
        "narrative": "Test payment"
    }
    
    idempotency_key = "idem_test_123"
    req_hash = canonical_hash(request_body)
    
    mock_redis.get.return_value = None
    mock_pg_pool.acquire().__aenter__().fetchrow.return_value = None
    
    mock_request = MagicMock()
    mock_request.method = "POST"
    mock_request.headers = {"Idempotency-Key": idempotency_key}
    mock_request.json = AsyncMock(return_value=request_body)
    
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.body_iterator = iter([b'{"transaction_id": "test-id", "status": "POSTED"}'])
    mock_response.headers = {}
    mock_response.media_type = "application/json"
    
    mock_call_next = AsyncMock(return_value=mock_response)
    
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    assert response.status_code == 201
    mock_redis.set.assert_called()
    mock_pg_pool.acquire().__aenter__().execute.assert_called()

@pytest.mark.asyncio
async def test_rate_limit_enforcement(mock_redis):
    """Test rate limiting blocks after threshold"""
    mock_redis.incr.side_effect = [1, 2, 3, 4, 5, 6, 51]
    mock_redis.expire.return_value = True
    
    middleware = RateLimitMiddleware(None, mock_redis)
    
    mock_request = MagicMock()
    mock_request.headers = {"X-Merchant-ID": "merchant_1"}
    
    mock_call_next = AsyncMock(return_value=MagicMock(headers={}))
    
    for _ in range(5):
        await middleware.dispatch(mock_request, mock_call_next)
    
    with pytest.raises(Exception) as exc_info:
        await middleware.dispatch(mock_request, mock_call_next)
    
    assert exc_info.value.status_code == 429

def test_canonical_hash_properties():
    """Test canonical hash is deterministic and order-independent"""
    body1 = {"a": 1, "b": {"c": 2, "d": [3, 4]}}
    body2 = {"b": {"d": [3, 4], "c": 2}, "a": 1}
    body3 = {"a": 1, "b": {"c": 2, "d": [3, 5]}}
    
    hash1 = canonical_hash(body1)
    hash2 = canonical_hash(body2)
    hash3 = canonical_hash(body3)
    
    assert hash1 == hash2
    assert hash1 != hash3
    assert len(hash1) == 64

def test_idempotency_conflict_detection():
    """Test that same key with different body is rejected"""
    body1 = {"amount": 100, "currency": "INR"}
    body2 = {"amount": 200, "currency": "INR"}
    
    assert canonical_hash(body1) != canonical_hash(body2)

if __name__ == "__main__":
    pytest.main([__file__, "-v"])