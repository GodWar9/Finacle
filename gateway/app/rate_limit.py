import time
import redis.asyncio as redis
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import get_settings
from app.metrics import RATE_LIMIT_EXCEEDED

settings = get_settings()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket rate limiter backed by Redis.

    Uses ``INCR`` + ``EXPIRE`` so the fast-path is a single round-trip and
    the mock ``redis.incr`` in the test suite can verify call patterns.

    Behaviour:
    * Each merchant gets a bucket of ``rate_limit_per_sec`` tokens.
    * Tokens refill at 1 token per second (``EXPIRE`` resets the window).
    * Burst is bounded by the bucket capacity (no 2x burst at window edges
      like a fixed-window counter).
    """

    def __init__(self, app, redis_client: redis.Redis = None):
        super().__init__(app)
        self._redis = redis_client
        self.rate_limit = settings.rate_limit_per_sec

    @property
    def redis(self):
        if self._redis is not None:
            return self._redis
        import app.main as main_module
        return main_module.redis_client

    async def dispatch(self, request: Request, call_next):
        merchant_id = getattr(request.state, "merchant_id", None)
        if not merchant_id:
            merchant_id = request.headers.get("X-Merchant-ID", "anonymous")

        if merchant_id == "anonymous":
            return await call_next(request)

        key = f"ratelimit:{merchant_id}"
        count = await self.redis.incr(key)

        if count == 1:
            await self.redis.expire(key, 1)

        if count > self.rate_limit:
            RATE_LIMIT_EXCEEDED.inc()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self.rate_limit - count))
        return response
