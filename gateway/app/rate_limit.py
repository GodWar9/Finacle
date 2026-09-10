import time
import redis.asyncio as redis
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import get_settings
from app.metrics import RATE_LIMIT_EXCEEDED

settings = get_settings()

# Lua script for atomic token bucket: refill + consume in one round-trip.
# KEYS[1] = bucket key, ARGV[1] = max tokens, ARGV[2] = refill rate (tokens/sec),
# ARGV[3] = now (unix seconds as float), ARGV[4] = ttl seconds
_TOKEN_BUCKET_LUA = """
local key       = KEYS[1]
local max_tokens = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now        = tonumber(ARGV[3])
local ttl        = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens     = tonumber(data[1])
local last_refill = tonumber(data[2])

if tokens == nil then
    -- first request: bucket starts full, consume one token
    tokens = max_tokens - 1
    last_refill = now
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
    redis.call('EXPIRE', key, ttl)
    return {1, max_tokens - 1}
end

-- refill based on elapsed time
local elapsed = now - last_refill
tokens = math.min(max_tokens, tokens + elapsed * refill_rate)
last_refill = now

if tokens < 1 then
    -- no token available
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
    redis.call('EXPIRE', key, ttl)
    return {0, math.floor(tokens)}
end

-- consume one token
tokens = tokens - 1
redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
redis.call('EXPIRE', key, ttl)
return {1, math.floor(tokens)}
"""

_sha = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_client: redis.Redis = None):
        super().__init__(app)
        self._redis = redis_client
        self.rate_limit = settings.rate_limit_per_sec  # tokens per second (= burst capacity)
        self._script = None

    @property
    def redis(self):
        if self._redis is not None:
            return self._redis
        import app.main as main_module
        return main_module.redis_client

    async def _ensure_script(self):
        if self._script is None:
            global _sha
            if _sha is None:
                _sha = await self.redis.script_load(_TOKEN_BUCKET_LUA)
            self._script = _sha

    async def dispatch(self, request: Request, call_next):
        merchant_id = getattr(request.state, "merchant_id", None)
        if not merchant_id:
            merchant_id = request.headers.get("X-Merchant-ID", "anonymous")

        if merchant_id == "anonymous":
            return await call_next(request)

        await self._ensure_script()

        key = f"ratelimit:{merchant_id}"
        now = time.time()
        # ttl = 2x the time it takes to fully refill from empty → avoids key expiry during bursts
        ttl = max(10, int(2 * self.rate_limit / 1))  # refill_rate = 1 token per sec? No — rate = rate_limit

        # refill_rate = rate_limit tokens/sec, burst = rate_limit tokens
        result = await self.redis.evalsha(
            self._script,
            1,               # number of keys
            key,             # KEYS[1]
            str(self.rate_limit),    # ARGV[1] max_tokens
            str(self.rate_limit),    # ARGV[2] refill_rate (tokens/sec = sustained rate)
            str(now),               # ARGV[3]
            str(ttl),               # ARGV[4]
        )

        allowed, remaining = int(result[0]), int(result[1])

        if not allowed:
            RATE_LIMIT_EXCEEDED.inc()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded"
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
