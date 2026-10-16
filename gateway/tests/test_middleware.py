import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.idempotency import IdempotencyMiddleware, canonical_hash, scoped_key
from app.rate_limit import RateLimitMiddleware


def _post_request(body_bytes: bytes, headers=None):
    req = MagicMock()
    req.method = "POST"
    req.url.path = "/api/v1/transactions"
    req.state.merchant_id = "merchant_test"
    req.headers = {"Idempotency-Key": "test-key", **(headers or {})}
    req.body = AsyncMock(return_value=body_bytes)
    return req


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

    mock_redis.get.return_value = json.dumps(
        {
            "request_hash": req_hash,
            "response_body": {"transaction_id": "test-id", "status": "POSTED"},
            "status_code": 201,
        }
    )

    middleware = IdempotencyMiddleware(None, mock_redis, mock_pg_pool)

    mock_request = _post_request(json.dumps(request_body).encode())
    mock_request.json = AsyncMock(return_value=request_body)

    mock_call_next = AsyncMock()

    response = await middleware.dispatch(mock_request, mock_call_next)

    assert response.status_code == 201
    mock_call_next.assert_not_called()
    mock_redis.get.assert_called_once_with(
        "idem:" + scoped_key("merchant_test", "POST", "/api/v1/transactions", "test-key")
    )


@pytest.mark.asyncio
async def test_idempotency_middleware_non_json_body():
    """A multipart / raw POST (e.g. settlement file upload) must not crash.

    The body is JSON-invalid, so the middleware hashes the raw bytes instead
    and still honours the cached idempotency response.
    """
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_redis.set.return_value = True
    mock_redis.delete.return_value = True
    mock_pg_pool = AsyncMock()
    mock_pg_pool.acquire.side_effect = RuntimeError("no pg pool needed")

    middleware = IdempotencyMiddleware(
        None,
        redis_client=mock_redis,
        pg_pool=object(),
    )

    raw = (
        b"--boundary\r\n"
        b'Content-Disposition: form-data; name="file"; filename="x.txt"\r\n'
        b"\r\n"
        b"FILEBYTES\r\n"
        b"--boundary--\r\n"
    )
    mock_request = _post_request(raw)
    mock_request.form = MagicMock()
    mock_request.state.merchant_id = "merchant_test"

    cached = {
        "idem:" + scoped_key("merchant_test", "POST", "/api/v1/transactions", "test-key"): json.dumps(
            {
                "request_hash": hashlib.sha256(raw).hexdigest(),
                "response_body": {"raw": "cached"},
                "status_code": 202,
            }
        )
    }
    mock_redis.get.side_effect = lambda k: cached.get(k)

    mock_call_next = AsyncMock()

    response = await middleware.dispatch(mock_request, mock_call_next)

    assert response.status_code == 202
    mock_call_next.assert_not_called()


@pytest.mark.asyncio
async def test_idempotency_middleware_conflict():
    mock_redis = AsyncMock()
    mock_pg_pool = AsyncMock()

    request_body = {"amount": 100, "currency": "INR"}
    req_hash = canonical_hash(request_body)

    mock_redis.get.return_value = json.dumps(
        {
            "request_hash": req_hash,
            "response_body": {"transaction_id": "test-id"},
            "status_code": 201,
        }
    )

    middleware = IdempotencyMiddleware(None, mock_redis, mock_pg_pool)

    mock_request = _post_request(json.dumps({"amount": 200, "currency": "INR"}).encode())
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

    mock_request = _post_request(json.dumps({"amount": 100}).encode(), headers={})
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


@pytest.mark.asyncio
async def test_retry_cache_cannot_cross_tenants_or_operations():
    from fastapi import Request
    from starlette.responses import StreamingResponse

    cached = {}
    redis = AsyncMock()
    redis.get.side_effect = lambda key: cached.get(key)

    async def store(key, value, **kwargs):
        cached[key] = value
        return True

    redis.set.side_effect = store
    middleware = IdempotencyMiddleware(None, redis, object())
    middleware.fetch_idempotency_record = AsyncMock(return_value=None)

    async def cache_response(key, req_hash, body, status_code):
        cached["idem:" + key] = json.dumps(
            {"request_hash": req_hash, "response_body": body, "status_code": status_code}
        )

    middleware.cache_response = cache_response
    calls = []

    async def next_handler(request):
        body = {"merchant": request.state.merchant_id, "path": request.url.path}
        calls.append(body)

        async def stream():
            yield json.dumps(body).encode()

        return StreamingResponse(stream(), status_code=201, media_type="application/json")

    for merchant, path in (("a", "/api/v1/a"), ("b", "/api/v1/a"), ("a", "/api/v1/b"), ("a", "/api/v1/a")):

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [(b"idempotency-key", b"shared-key")],
                "state": {"merchant_id": merchant},
            },
            receive,
        )
        response = await middleware.dispatch(request, next_handler)
        assert json.loads(response.body) == {"merchant": merchant, "path": path}
    assert len(calls) == 3
    assert redis.eval.await_count == 3
    for call in redis.eval.await_args_list:
        assert "ARGV[1]" in call.args[0]
        assert len(call.args[3]) == 32
