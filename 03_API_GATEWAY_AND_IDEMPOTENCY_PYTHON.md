# 03 — API Gateway & Idempotency Middleware (Python)

**Owner:** Agent C
**Owns:** `gateway/app/*.py`, `gateway/tests/*`
**Depends on:** gRPC contract in `05_DATABASE_AND_EVENT_SCHEMA.md` §3
**Does not touch:** Rust core internals, C++ recon internals

---

## 1. Responsibility

The public-facing surface. Everything a merchant/client actually calls. Owns:

1. AuthN/AuthZ (API key or JWT — merchant-scoped)
2. Request validation (Pydantic)
3. **Idempotency middleware** — the piece that makes retries safe end-to-end, not just inside the Rust core
4. Translating REST → gRPC calls into the Rust ledger core
5. Rate limiting per merchant

## 2. Tech stack

- FastAPI + `uvicorn`
- `redis` (idempotency cache, fast path) backed by Postgres `idempotency_records` (durable fallback — see doc 05 §1.6)
- `grpcio`/`grpclib` client stubs generated from `ledger.proto`
- `pydantic` v2 models mirroring the proto messages

## 3. Idempotency middleware — the actual mechanism

This is the single most interview-relevant piece of the whole project. The design, precisely:

```python
# gateway/app/idempotency.py
import hashlib
import json
from fastapi import Request, HTTPException

IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60  # 24h, matches Stripe's convention

def canonical_hash(body: dict) -> str:
    """Deterministic hash so re-sending the SAME payload with the SAME key
    is a safe replay, but re-using a key with a DIFFERENT payload is rejected —
    this second case is what stops a client bug from silently posting a
    different transaction under a key it already used."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def idempotency_middleware(request: Request, call_next):
    key = request.headers.get("Idempotency-Key")
    if request.method == "POST" and not key:
        raise HTTPException(400, "Idempotency-Key header is required for POST")

    if not key:
        return await call_next(request)

    body = await request.json()
    req_hash = canonical_hash(body)
    cache_key = f"idem:{key}"

    # Fast path: Redis
    cached = await redis_client.get(cache_key)
    if cached:
        cached_obj = json.loads(cached)
        if cached_obj["request_hash"] != req_hash:
            raise HTTPException(409, "Idempotency-Key reused with a different request body")
        return JSONResponse(cached_obj["response_body"], status_code=cached_obj["status_code"])

    # Durable fallback: Postgres (covers Redis restarts / cold cache)
    row = await fetch_idempotency_record(key)
    if row:
        if row.request_hash != req_hash:
            raise HTTPException(409, "Idempotency-Key reused with a different request body")
        await redis_client.set(cache_key, row.to_json(), ex=IDEMPOTENCY_TTL_SECONDS)
        return JSONResponse(row.response_body, status_code=row.status_code)

    # First time seeing this key — proceed with the real request, but hold a
    # short-lived Redis LOCK on the key so two concurrent retries of the SAME
    # request don't both fall through to the ledger core simultaneously.
    got_lock = await redis_client.set(f"lock:{cache_key}", "1", nx=True, ex=10)
    if not got_lock:
        raise HTTPException(409, "Request with this Idempotency-Key is already being processed")

    try:
        response = await call_next(request)
        await cache_response(key, req_hash, response)  # write-through Redis + Postgres
        return response
    finally:
        await redis_client.delete(f"lock:{cache_key}")
```

**Why both Redis and Postgres:** Redis gives you sub-millisecond replay for the hot path (a client retrying within seconds, the overwhelmingly common case). Postgres is the durable backstop so a Redis restart doesn't reopen a window for double-processing — this two-tier design is exactly how Razorpay/Stripe-class systems describe their idempotency layer in engineering write-ups.

## 4. Gateway → Rust core call

```python
# gateway/app/routes/transactions.py
from gateway.grpc_client import ledger_stub
from gateway.models import PostTransactionRequest as ApiRequest

@router.post("/api/v1/transactions", status_code=201)
async def post_transaction(req: ApiRequest, idempotency_key: str = Header(...)):
    grpc_req = ledger_pb2.PostTransactionRequest(
        idempotency_key=idempotency_key,
        transaction_type=req.transaction_type,
        reference_id=req.reference_id,
        entries=[
            ledger_pb2.LedgerEntry(
                account_id=e.account_id, direction=e.direction,
                amount_minor=e.amount_minor, currency=e.currency,
            ) for e in req.entries
        ],
        narrative=req.narrative,
    )
    try:
        resp = await ledger_stub.PostTransaction(grpc_req, timeout=2.0)
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.ABORTED:
            # Serializable isolation conflict from the Rust core (see doc 01 §6) —
            # safe to retry because the whole call is idempotent end-to-end.
            resp = await retry_with_backoff(lambda: ledger_stub.PostTransaction(grpc_req, timeout=2.0))
        else:
            raise HTTPException(502, f"ledger core error: {e.details()}")
    return {"transaction_id": resp.transaction_id, "status": resp.status}
```

## 5. Rate limiting (token bucket, per merchant, Redis-backed)

```python
# gateway/app/rate_limit.py
async def check_rate_limit(merchant_id: str, limit_per_sec: int = 50):
    key = f"ratelimit:{merchant_id}"
    current = await redis_client.incr(key)
    if current == 1:
        await redis_client.expire(key, 1)
    if current > limit_per_sec:
        raise HTTPException(429, "rate limit exceeded")
```

## 6. What Agent C ships

1. Pydantic models mirroring `ledger.proto`, FastAPI skeleton, health check
2. Idempotency middleware (Redis-only first, Postgres fallback second) with tests covering: same key+same body (replay), same key+different body (409), concurrent same key (lock contention)
3. gRPC client wiring to Agent A's core, including the ABORTED-retry path
4. AuthN (API key middleware) + rate limiting
5. OpenAPI docs auto-generated, exposed at `/docs`
