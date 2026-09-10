import asyncpg
import redis.asyncio as redis
import structlog
from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.models.schemas import HealthResponse

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter(tags=["health"])

@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="healthy", service="gateway")

@router.get("/health/ready")
async def readiness_check():
    checks = {}

    try:
        conn = await asyncpg.connect(settings.database_url)
        await conn.execute("SELECT 1")
        await conn.close()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    try:
        r = redis.from_url(settings.redis_url)
        await r.ping()
        await r.close()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())

    if not all_ok:
        raise HTTPException(status_code=503, detail={"status": "not ready", "checks": checks})

    return {"status": "ready", "checks": checks}
