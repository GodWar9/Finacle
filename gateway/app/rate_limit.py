import redis.asyncio as redis
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import get_settings
from app.metrics import RATE_LIMIT_EXCEEDED

settings = get_settings()

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_client: redis.Redis = None):
        super().__init__(app)
        self._redis = redis_client
        self.limit_per_sec = settings.rate_limit_per_sec

    @property
    def redis(self):
        # Fall back to the app's live client when wired before lifespan startup.
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
        current = await self.redis.incr(key)
        
        if current == 1:
            await self.redis.expire(key, 1)
        
        if current > self.limit_per_sec:
            RATE_LIMIT_EXCEEDED.inc()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded"
            )
        
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limit_per_sec)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self.limit_per_sec - current))
        return response