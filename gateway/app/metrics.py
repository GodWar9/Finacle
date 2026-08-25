from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response
from starlette.middleware.base import BaseHTTPMiddleware
import time

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"]
)

IDEMPOTENCY_REPLAYS = Counter(
    "idempotency_replays_total",
    "Total idempotency replays",
    ["source"]
)

IDEMPOTENCY_CONFLICTS = Counter(
    "idempotency_conflicts_total",
    "Total idempotency conflicts"
)

RATE_LIMIT_EXCEEDED = Counter(
    "rate_limit_exceeded_total",
    "Total rate limit exceeded events"
)

ACTIVE_CONNECTIONS = Gauge(
    "active_connections",
    "Number of active connections"
)

GRPC_CALLS = Counter(
    "grpc_calls_total",
    "Total gRPC calls",
    ["method", "status"]
)

GRPC_LATENCY = Histogram(
    "grpc_call_duration_seconds",
    "gRPC call latency in seconds",
    ["method"]
)

class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        ACTIVE_CONNECTIONS.inc()
        start_time = time.time()
        
        try:
            response = await call_next(request)
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=request.url.path,
                status=response.status_code
            ).inc()
            REQUEST_LATENCY.labels(
                method=request.method,
                endpoint=request.url.path
            ).observe(time.time() - start_time)
            return response
        finally:
            ACTIVE_CONNECTIONS.dec()

def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)