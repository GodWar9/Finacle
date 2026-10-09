import logging
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import redis.asyncio as redis
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from app.auth import AuthMiddleware
from app.config import get_settings
from app.grpc_client import get_grpc_client
from app.idempotency import IdempotencyMiddleware
from app.metrics import PrometheusMiddleware, metrics_endpoint
from app.rate_limit import RateLimitMiddleware
from app.routes import accounts, health, reconciliation, settlements, transaction_history, transactions, webhooks

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logging.basicConfig(level=logging.INFO)

settings = get_settings()

pg_pool: asyncpg.Pool = None
redis_client: redis.Redis = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pg_pool, redis_client

    pg_pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=5,
        max_size=20,
    )

    redis_client = redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )

    await get_grpc_client()

    yield

    await pg_pool.close()
    await redis_client.close()
    client = await get_grpc_client()
    await client.close()


app = FastAPI(
    title="Ledger API Gateway",
    version="0.1.0",
    lifespan=lifespan,
)

# Starlette executes the last registered middleware first. Authenticate before
# rate limiting and before any cached response can short-circuit the request.
app.add_middleware(IdempotencyMiddleware, redis_client=redis_client, pg_pool=pg_pool)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client)
app.add_middleware(AuthMiddleware)
app.add_middleware(PrometheusMiddleware)


@app.middleware("http")
async def middleware_errors(request: Request, call_next):
    # HTTPException raised by middleware is outside FastAPI's route handler.
    try:
        return await call_next(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


app.include_router(health.router)
app.include_router(transactions.router)
app.include_router(webhooks.router)
app.include_router(transaction_history.router)
app.include_router(accounts.router)
app.include_router(settlements.router)
app.include_router(reconciliation.router)

app.add_route("/metrics", metrics_endpoint)

# Serve the demo portal. Routes registered above (health, api/v1, metrics)
# take precedence; everything else falls through to the static frontend.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="portal")
