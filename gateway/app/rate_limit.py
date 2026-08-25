import redis.asyncio as redis
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import get_settings

settings = get_settings()

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_client: redis.Redis):
        super().__init__(app)
        self.redis = redis_client
        self.limit_per_sec = settings.rate_limit_per_sec

    async def dispatch(self, request: Request, call_next):
        merchant_id = request.headers.get("X-Merchant-ID", "anonymous")
        
        if merchant_id == "anonymous":
            return await call_next(request)
        
        key = f"ratelimit:{merchant_id}"
        current = await self.redis.incr(key)
        
        if current == 1:
            await self.redis.expire(key, 1)
        
        if current > self.limit_per_sec:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded"
            )
        
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limit_per_sec)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self.limit_per_sec - current))
        return response