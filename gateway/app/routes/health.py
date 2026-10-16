import asyncio

import asyncpg
import redis.asyncio as redis
from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.grpc_client import get_grpc_client
from app.models.schemas import HealthResponse

settings = get_settings()
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="healthy", service="gateway")


async def _database_ready():
    conn = await asyncpg.connect(settings.database_url, timeout=2)
    try:
        # A socket alone is insufficient when migrations have not completed.
        await conn.execute("SELECT merchant_id FROM accounts LIMIT 0")
    finally:
        await conn.close(timeout=1)


async def _redis_ready():
    client = redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _ledger_ready():
    client = await get_grpc_client()
    await client.check_ready()


async def _probe(check):
    try:
        await asyncio.wait_for(check(), timeout=3)
        return "ok"
    except Exception:
        # Readiness is public: do not return DSNs, hostnames or database errors.
        return "unavailable"


@router.get("/health/ready")
async def readiness_check():
    names = ("database", "redis", "ledger")
    results = await asyncio.gather(*(_probe(check) for check in (_database_ready, _redis_ready, _ledger_ready)))
    checks = dict(zip(names, results, strict=True))
    if any(result != "ok" for result in results):
        raise HTTPException(status_code=503, detail={"status": "not ready", "checks": checks})
    return {"status": "ready", "checks": checks}
