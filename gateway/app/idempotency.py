import hashlib
import json
from typing import Any

import asyncpg
import redis.asyncio as redis
import structlog
from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import get_settings
from app.metrics import IDEMPOTENCY_CONFLICTS, IDEMPOTENCY_REPLAYS

logger = structlog.get_logger()
settings = get_settings()

IDEMPOTENCY_TTL_SECONDS = settings.idempotency_ttl_seconds


def canonical_hash(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_client: redis.Redis = None, pg_pool: asyncpg.Pool = None):
        super().__init__(app)
        self._redis = redis_client
        self._pg_pool = pg_pool

    @property
    def redis(self):
        # Fall back to the app's live client when wired before lifespan startup
        # (main.py registers this middleware at import time while the module
        # globals are still None placeholders).
        if self._redis is not None:
            return self._redis
        import app.main as main_module

        return main_module.redis_client

    @property
    def pg_pool(self):
        if self._pg_pool is not None:
            return self._pg_pool
        import app.main as main_module

        return main_module.pg_pool

    async def dispatch(self, request: Request, call_next):
        if request.method != "POST":
            return await call_next(request)

        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key header is required for POST"
            )

        # The Idempotency-Key must be computed over the exact request the
        # client sent. JSON bodies are hashed canonically (after parsing);
        # any other body (multipart settlement uploads, raw bytes) is hashed
        # as the raw payload instead of failing on request.json().
        raw_body = await request.body()
        try:
            body = json.loads(raw_body) if raw_body else {}
            req_hash = canonical_hash(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            req_hash = hashlib.sha256(raw_body).hexdigest()
        cache_key = f"idem:{idempotency_key}"

        cached = await self.redis.get(cache_key)
        if cached:
            cached_obj = json.loads(cached)
            if cached_obj["request_hash"] != req_hash:
                IDEMPOTENCY_CONFLICTS.inc()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="Idempotency-Key reused with a different request body"
                )
            IDEMPOTENCY_REPLAYS.labels(source="redis").inc()
            logger.info("idempotency_replay_redis", key=idempotency_key)
            return JSONResponse(cached_obj["response_body"], status_code=cached_obj["status_code"])

        row = await self.fetch_idempotency_record(idempotency_key)
        if row:
            if row["request_hash"] != req_hash:
                IDEMPOTENCY_CONFLICTS.inc()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="Idempotency-Key reused with a different request body"
                )
            await self.redis.set(cache_key, json.dumps(row), ex=IDEMPOTENCY_TTL_SECONDS)
            IDEMPOTENCY_REPLAYS.labels(source="postgres").inc()
            logger.info("idempotency_replay_postgres", key=idempotency_key)
            return JSONResponse(row["response_body"], status_code=row["status_code"])

        lock_key = f"lock:{cache_key}"
        got_lock = await self.redis.set(lock_key, "1", nx=True, ex=10)
        if not got_lock:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Request with this Idempotency-Key is already being processed",
            )

        try:
            response = await call_next(request)

            if response.status_code < 500:
                response_body = b""
                async for chunk in response.body_iterator:
                    response_body += chunk

                try:
                    response_data = json.loads(response_body)
                except json.JSONDecodeError:
                    response_data = {"raw": response_body.decode()}

                await self.cache_response(idempotency_key, req_hash, response_data, response.status_code)

                return Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            return response
        finally:
            await self.redis.delete(lock_key)

    async def fetch_idempotency_record(self, key: str) -> dict[str, Any] | None:
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT request_hash, response_body, status_code FROM idempotency_records "
                "WHERE idempotency_key = $1 AND expires_at > NOW()",
                key,
            )
            if row:
                return dict(row)
        return None

    async def cache_response(self, key: str, req_hash: str, response_body: dict, status_code: int):
        cache_data = {"request_hash": req_hash, "response_body": response_body, "status_code": status_code}

        await self.redis.set(f"idem:{key}", json.dumps(cache_data), ex=IDEMPOTENCY_TTL_SECONDS)

        async with self.pg_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO idempotency_records
                   (idempotency_key, request_hash, response_body,
                    status_code, expires_at)
                   VALUES ($1, $2, $3, $4, NOW() + INTERVAL '24 hours')
                   ON CONFLICT (idempotency_key) DO UPDATE SET
                   request_hash = EXCLUDED.request_hash,
                   response_body = EXCLUDED.response_body,
                   status_code = EXCLUDED.status_code,
                   expires_at = EXCLUDED.expires_at""",
                key,
                req_hash,
                json.dumps(response_body),
                status_code,
            )
