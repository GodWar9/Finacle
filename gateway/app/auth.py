import hashlib
import hmac
import time
import structlog
from typing import Optional
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_keys: dict = None):
        super().__init__(app)
        self.api_keys = api_keys or {}
        self._load_api_keys()

    def _load_api_keys(self):
        import os
        keys_str = os.getenv("API_KEYS", "")
        for pair in keys_str.split(","):
            if ":" in pair:
                key, merchant_id = pair.split(":", 1)
                self.api_keys[key.strip()] = merchant_id.strip()

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ["/health", "/health/ready", "/metrics", "/"]:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-API-Key header is required"
            )

        merchant_id = self.api_keys.get(api_key)
        if not merchant_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key"
            )

        request.state.merchant_id = merchant_id
        request.state.api_key = api_key
        
        return await call_next(request)

async def get_current_merchant(request: Request) -> str:
    return getattr(request.state, "merchant_id", "anonymous")

def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)