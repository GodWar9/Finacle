# Finacle — Project Explanation & How It Works

> A complete, code-grounded explanation of the Finacle double-entry core-banking
> ledger: what it is, how the pieces fit, how money actually moves through it,
> and how to run and operate it. Written as a standalone, exportable reference.

---

## 1. What this system is

Finacle is a **production-shaped core-banking transaction engine** — the same
category of system that sits behind Razorpay/PayU/Stripe-style payment platforms
and bank cores (Infosys Finacle, Temenos, Flexcube). The guiding guarantees:

- **Every rupee moves as a balanced double-entry.** Debits always equal credits,
  checked in application code *and* enforced by a deferred DB trigger.
- **The ledger is append-only (WORM).** Corrections are new reversed transactions,
  never updates or deletes.
- **Every API write is safely retryable.** An `Idempotency-Key` is mandatory on
  writes; replays return the original response, and key re-use with a different
  body is a `409`.
- **Settlement files are reconciled adversarially.** A C++ engine matches the bank
  settlement file against the internal ledger and flags every drift as an exception —
  nothing is silently written off.
- **An LLM copilot answers grounded questions** about compliance policies and live
  operational events (reconciliation exceptions, transaction narratives).

It is **polyglot by design**, mirroring how real fintech backend teams split work:

| Hot path | Language | Why |
|---|---|---|
| Money movement (post/balance/reverse) | **Rust** (tonic + sqlx) | Memory safety, compile-checked SQL, `SERIALIZABLE` concurrency |
| Batch reconciliation (fixed-width files) | **C++** (libpqxx, OpenMP) | Throughput-bound, mainframe-format parsing |
| Gateway + RAG copilot + indexer + settlement | **Python** (FastAPI, aiokafka) | Fast iteration for business logic and LLM integration |

---

## 2. High-level architecture

```
                            ┌──────────────────────────┐
                            │  Merchant / client / UI   │
                            └────────────┬─────────────┘
                           HTTP  X-API-Key (required)
                                        │  Idempotency-Key (required on POST)
                                        ▼
                     ┌─────────────────────────────────────┐
                     │  API GATEWAY (fastapi, :8000)         │
                     │  PrometheusMiddleware                │
                     │  AuthMiddleware (X-API-Key)          │
                     │  RateLimitMiddleware (Redis token    │
                     │     bucket, default 50 req/s)        │
                     │  IdempotencyMiddleware (Redis hot    │
                     │     path + Postgres durable backstop)│
                     │  Static demo portal at /             │
                     └──────────────┬──────────────────────┘
                                    │ gRPC (:50051, ledger.proto)
                                    ▼
                     ┌─────────────────────────────────────┐
                     │  LEDGER CORE (rust, :50051)           │
                     │  PostTransaction / GetBalance /       │
                     │  ReverseTransaction                   │
                     │  SERIALIZABLE isolation               │
                     │  balance-auditor (scheduled, 24h)     │
                     │  outbox poller -> Kafka               │
                     └──────┬──────────────────┬────────────┘
                            │ sqlx writes      │ publishes
                            ▼                  ▼
                  ┌──────────────────┐  ┌──────────────────────┐
                  │  PostgreSQL      │  │  Kafka               │
                  │  ledger (source  │  │  ledger.transaction.*│
                  │  of truth,       │  │  reconciliation.*    │
                  │  pgvector)       │  └──────────┬───────────┘
                  └──────────────────┘             │
                                      ┌───────────┼──────────────────┐
                                      ▼           ▼                  ▼
                            ┌─────────────┐ ┌─────────────┐  ┌───────────────┐
                            │ RAG Indexer │ │ RAG Copilot │  │ Settlement    │
                            │ (kafka      │ │ (:8001) Q&A │  │ Processor     │
                            │  consumer)  │ │ grounded in │  │ (batch,       │
                            └─────────────┘ │ vector store│  │  poll loop)   │
                                            └─────────────┘  └───────┬───────┘
                                                       runs the C++ recon engine,
                                                       writes exceptions + summary
```

**Concurrency note:** the gateway registers its middleware at import time while
the app globals (`pg_pool`, `redis_client`) are still `None`. Each middleware
resolves the live clients lazily through a property that imports `app.main` at
call time (`gateway/app/idempotency.py:33-49`, `gateway/app/rate_limit.py:30-35`).

---

## 3. Component deep-dive

### 3.1 Ledger Core — Rust (`core/`)

Entry point `core/src/main.rs`:

1. Reads `DATABASE_URL`, `GRPC_ADDR` (default `0.0.0.0:50051`), `RUST_LOG`.
2. Connects to Postgres and runs `sqlx::migrate!("../db/migrations")` at startup —
   the DB schema is owned and applied by the core.
3. Starts the gRPC server (`LedgerCore` service) plus two background tasks:

   - **Outbox poller** (`core/src/ledger/poller.rs`, gated behind the `kafka`
     Cargo feature): every 1s, selects up to 100 unpublished `outbox_events`
     with `FOR UPDATE SKIP LOCKED`, publishes to Kafka, marks `published = true`,
     and commits. Ledger writes never depend on Kafka availability.
   - **Balance auditor** (`core/src/ledger/auditor.rs`): every 24h, re-derives
     each account's balance by summing `ledger_entries` and compares it to the
     materialized `account_balances` table, logging `BALANCE MISMATCH` on drift.
     This is the consistency check between the source of truth and the read cache.

The gRPC contract (`core/proto/ledger.proto`, package `ledger.v1`) has three RPCs:

| RPC | Purpose |
|---|---|
| `PostTransaction` | Atomically write a balanced double-entry txn: header + entries + idempotency record + outbox event |
| `GetBalance` | Read the materialized balance (O(1) — no summing at read time) |
| `ReverseTransaction` | Flip the original entries (DEBIT <-> CREDIT), post as a new `REVERSAL` txn, mark the original `REVERSED`, emit `ledger.transaction.reversed` |

Posting flow (`core/src/ledger/service.rs`):

1. `req.validate_balanced()` — sum of debits == sum of credits (app-level check).
2. Idempotency lookup — same key + same `request_hash` replays the cached
   response; same key + different hash returns `IdempotencyConflict`.
3. `BEGIN` a transaction and `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE`.
4. Insert the `transactions` header, every `ledger_entry`, the idempotency record,
   and the outbox event, then `COMMIT`. The balance-invariant constraint trigger
   and the balance-update trigger fire on `ledger_entries` inserts (DB-level check).
5. If a concurrent writer aborts the serializable transaction, the gateway retries
   the gRPC call (up to 3 attempts on `ABORTED` — `gateway/app/routes/transactions.py:48-71`).

### 3.2 API Gateway — Python/FastAPI (`gateway/`)

App assembly (`gateway/app/main.py`):

- **Lifespan** creates the asyncpg pool (min 5 / max 20), the Redis client, and the
  gRPC channel, closing them all on shutdown.
- **Middleware order** (outermost first):
  1. `PrometheusMiddleware` — request count/latency, active connections.
  2. `AuthMiddleware` — requires `X-API-Key` on everything except `/health`,
     `/health/ready`, `/metrics`, `/`; maps key -> `merchant_id`.
  3. `RateLimitMiddleware` — Redis `INCR` + `EXPIRE` token bucket; default 50 req/s
     per merchant; sets `X-RateLimit-Limit` / `X-RateLimit-Remaining`.
  4. `IdempotencyMiddleware` — see section 4.
- **Routers**: health, transactions, webhooks, transaction history, accounts,
  settlements, reconciliation.
- **Static portal**: `frontend/` is copied into the gateway image as `gateway/static`
  and mounted at `/` (`gateway/Dockerfile:22`); API routes take precedence.

Routes (`*` = requires `Idempotency-Key`):

| Method | Path | Notes |
|---|---|---|
| GET | `/health`, `/health/ready` | liveness / readiness |
| GET | `/metrics` | Prometheus scrape endpoint |
| POST | `/api/v1/accounts` | create account (ASSET/LIABILITY/EQUITY/REVENUE/EXPENSE) |
| GET | `/api/v1/accounts` | list accounts (`owner_ref` filter, paginated) |
| POST | `/api/v1/accounts/{id}/freeze` | `ACTIVE -> FROZEN` |
| POST | `/api/v1/accounts/{id}/unfreeze` | `FROZEN -> ACTIVE` |
| POST | `/api/v1/transactions` | `*` proxy to core `PostTransaction` |
| GET | `/api/v1/transactions` | journal feed (header joined with entries, filters, pagination) |
| GET | `/api/v1/transactions/{id}` | one transaction with its entries |
| GET | `/api/v1/accounts/{id}/balance` | proxy to core `GetBalance` |
| POST | `/api/v1/transactions/{id}/reverse` | `*` proxy to core `ReverseTransaction` |
| POST | `/api/v1/webhooks/payment-gateway` | HMAC-signed; `payment.captured` -> posts a txn |
| POST | `/api/v1/webhooks/reconciliation` | HMAC-signed; acks recon notifications |
| POST | `/api/v1/settlements/upload` | `*` multipart; stores a bank settlement file |
| GET | `/api/v1/settlements/{batch_id}/status` | `UPLOADED -> PROCESSING -> COMPLETED/FAILED` |
| GET | `/api/v1/reconciliation/exceptions` | list exceptions, filter by batch/type |

Webhooks are signed with an HMAC-SHA256 of the raw body using `webhook_secret`
(`gateway/app/auth.py:54-56`), verified in `gateway/app/routes/webhooks.py`.

### 3.3 Settlement Processor — Python (`gateway/app/settlement_processor.py`)

Runs in the `batch` compose profile. Poll loop every `POLL_INTERVAL` (default 10s):

1. Claim one `UPLOADED` batch with `SELECT ... FOR UPDATE SKIP LOCKED`.
2. Mark it `PROCESSING`.
3. **Generate a ledger export**: every `POSTED` transaction row flattened to the
   fixed-width format (reference 20 @0, amount 13 @20, currency 3 @33, status 7 @36).
4. Write the uploaded bank file to a temp file and shell out to the compiled C++
   `recon_engine` with `--ledger-export`, `--bank-file`, `--batch-id`.
5. On success mark the batch `COMPLETED`; on failure mark it `FAILED` with the error
   message (added by migration `005`).
6. Publish Kafka events for whatever the engine wrote: one
   `reconciliation.exception.raised` per exception, plus one
   `reconciliation.batch.completed` with matched/exception counts.

### 3.4 Reconciliation Engine — C++ (`recon/`)

A standalone CLI (`recon/src/main.cpp`):

```
recon_engine --ledger-export=<file> --bank-file=<file>
             [--batch-id=<id>] [--tolerance=<minor_units>]
```

- **Parsing** (`recon/src/file_parser.cpp`; layout in `recon/include/recon/file_parser.hpp`):
  fixed-width, 43-column rows (ref 20 @0, amount 13 @20, currency 3 @33, status 7 @36).
  Amounts are minor units and a row must be `POSTED` status to be eligible to match.
- **Matching** (`recon/src/matcher.cpp`): builds a hash index over the ledger export
  by external reference, then reconciles bank rows **in parallel** (OpenMP
  `parallel for` when compiled with `-DENABLE_OPENMP=ON`, else
  `std::execution::par_unseq`). For each bank row:
  - reference missing from ledger -> `MISSING_IN_LEDGER`
  - `|ledger - bank| > tolerance` -> `AMOUNT_MISMATCH`
  - status differs -> `STATUS_MISMATCH`
  - the same reference appears twice in the bank file -> `DUPLICATE`
  - ledger rows never referenced by the bank file -> `MISSING_IN_BANK_FILE`
- **Writing** (`recon/src/db_writer.cpp`): exceptions land in
  `reconciliation_exceptions` (the FK `ledger_transaction_id` is only set when the
  reference is a real UUID), and one row is upserted into
  `reconciliation_batch_summary` with matched/exception counts.
- **Build**: `cmake -B build -DCMAKE_BUILD_TYPE=Release -DENABLE_OPENMP=ON && cmake --build build`.
- `recon/benchmark_recon.py` measures throughput against a live DB.
- `recon/scripts/make_bank_fixture.py` generates a realistic bank settlement file
  from real posted ledger rows (optionally with an orphan to force an exception).

### 3.5 RAG Copilot — Python/FastAPI (`rag/`)

Two processes share the `rag/` image:

- **Copilot service** (`rag/app/main.py`, `:8001`):
  - On startup it spawns `seed_policies()`, which idempotently indexes the bundled
    `rag/policies/*.txt` files as `POLICY_DOC` (source_ref = file stem, one row per
    400-token chunk with a 50-token overlap). Skipped when `OPENAI_API_KEY` is empty.
  - `POST /api/v1/rag/ask` -> `answer_question` (`rag/app/qa.py`):
    1. Embed the question (`text-embedding-3-small`), embedding dimension 1536.
    2. pgvector cosine search (`1 - (embedding <=> $1)`), optionally scoped
       (`ops` -> TXN narratives + recon exceptions; `compliance` -> policies + circulars).
    3. Build a narrow prompt with inline `[Source N]` citations and ask
       `gpt-4o-mini` (temperature 0.1, max 500 tokens) with explicit "don't guess" rules.
    4. Return the answer plus the citation list. Without a key it says so; without
       relevant docs it says so rather than hallucinating.
  - `POST /api/v1/rag/ingest-policy` ingests an ad-hoc document at runtime.
- **Indexer** (`rag/indexer/kafka_consumer.py`): consumes `ledger.transaction.posted`,
  `ledger.transaction.reversed`, and `reconciliation.exception.raised`, renders
  human-readable narratives, embeds them, and indexes them as `TXN_NARRATIVE` /
  `RECONCILIATION_EXCEPTION`. It intentionally **skips indexing when no API key is
  present** so zero-vectors never become the "most similar" match for every query.

`rag/app/db.py` registers the pgvector codec with asyncpg (`register_vector`) so
`vector` columns round-trip as Python lists.

### 3.6 Demo portal — vanilla JS (`frontend/`)

A no-framework / no-CDN ops console served by the gateway at `/`:

- Four views: **Overview**, **Transactions**, **Reconciliation**, **Copilot**.
- Data layer is isolated in `frontend/js/api.js`. `USE_MOCK_DATA = true` by default,
  so the console runs on fixture data from `frontend/data/mock-data.js`. When
  `USE_MOCK_DATA` is flipped to `false` and `API_KEY` is set, the same functions
  call the real gateway/RAG endpoints (`api.js:37`, `52`, `91`, `113`, `132`).
- The mock `postTransaction` even enforces the balance invariant locally, mirroring
  the real backend (`api.js:66-75`).

### 3.7 Streaming & storage

- **Kafka** (Confluent 7.6.0): `ledger.transaction.posted`, `ledger.transaction.reversed`,
  `reconciliation.exception.raised`, `reconciliation.batch.completed`.
- **PostgreSQL** (`pgvector/pgvector:pg16`, DB `ledger`, user `ledger_app`): source of
  truth for the ledger, plus the vector store and recon/settlement tables.
- **Redis 7**: gateway idempotency hot path (`idem:<key>`, 24h TTL) and rate-limit
  buckets (`ratelimit:<merchant>`) with a 10s short-lived idempotency lock.
- **Prometheus + Grafana** (`monitoring/`): scrape `:8000/metrics`; Grafana dashboard
  + alerting rules for throughput, p99 latency, idempotency replays, recon exception rate.

---

## 4. Idempotency — the two-tier design

The gateway enforces idempotency in front of *every* POST
(`gateway/app/idempotency.py`):

1. **Hash the body canonically** (`canonical_hash`): `json.dumps(sort_keys=True,
   separators=(",", ":"))` then SHA-256. Non-JSON bodies (multipart uploads, raw
   bytes) are hashed as raw bytes instead of failing.
2. **Redis fast path**: `GET idem:<key>`. If cached:
   - same `request_hash` -> return the cached JSON response (replay);
   - different hash -> `409 Conflict` (key reused with a different body).
3. **Postgres backstop**: if Redis misses, check `idempotency_records`
   (`expires_at > NOW()`). Found + same hash -> replay and repopulate Redis.
   This keeps correctness even when Redis is down (slower, but correct).
4. **Concurrency guard**: `SET idem:lock:<key> NX EX 10`. If the lock is held,
   return `409` ("already being processed"). This prevents double-posting when two
   retries arrive simultaneously (e.g. a client retry racing the original request).
5. On a successful response (<500), cache body + hash (Redis + Postgres,
   `ON CONFLICT DO UPDATE`, 24h TTL) and release the lock.

The **Rust core repeats the same check independently** (defense in depth), and the
`transactions.idempotency_key` column is `UNIQUE` as a final DB guard.

---

## 5. Data model

Applied at startup by the Rust core via `sqlx::migrate!` from `db/migrations/`:

| Table | Purpose |
|---|---|
| `accounts` | Master accounts (ASSET/LIABILITY/EQUITY/REVENUE/EXPENSE; ACTIVE/FROZEN/CLOSED) |
| `transactions` | Journal header (`idempotency_key UNIQUE`, `request_hash`, type, status POSTED/REVERSED, `reversal_of`) |
| `ledger_entries` | WORM double-entry rows (direction DEBIT/CREDIT, `amount_minor BIGINT`, currency) |
| `account_balances` | Financialized balance cache, kept in sync by trigger; verified by the 24h auditor |
| `outbox_events` | Transactional outbox (`topic`, `payload_json`, `published`) |
| `idempotency_records` | Durable idempotency backstop (Redis failover) |
| `rag_documents` | pgvector store (`source_type`, `source_ref`, content, `embedding VECTOR(1536)`, ivfflat index) |
| `reconciliation_exceptions` | Recon drift rows, written by the C++ engine |
| `reconciliation_batch_summary` | Per-batch matched/exception counts + status |
| `settlement_files` | Uploaded bank files + processing state machine |

Key DB-level invariants in `db/migrations/001_initial_schema.sql`:

- **`check_transaction_balanced()` deferred constraint trigger** — aborts the commit
  if debits != credits for the transaction. This is the DB failsafe behind the
  app-level check in the Rust core.
- **`update_account_balance()` trigger** — maintains `account_balances` on every
  `ledger_entries` insert (exists upsert).
- Money is always `BIGINT` minor units (paise), never floats.

---

## 6. Money flows, end to end

**A. Normal payment POST:**
```
curl -> gateway -> IdempotencyMiddleware -> /api/v1/transactions -> gRPC PostTransaction
     -> core validates balance -> SERIALIZABLE txn -> insert header+entries+idem+outbox
     -> commit -> reply -> gateway caches response
core outbox poller -> Kafka ledger.transaction.posted -> RAG indexer -> vector store
```

**B. Retry / replay:** the client re-sends the same key + body; the gateway replays
the cached response from Redis (or Postgres). No new transaction is created.

**C. Reversal:** `POST /transactions/{id}/reverse` -> core flips the entries, posts a
new `REVERSAL`, marks the original `REVERSED`, emits `ledger.transaction.reversed`.

**D. Payment-gateway webhook:** an authenticated `payment.captured` webhook posts a
debit to the merchant account and a credit to a suspense account
(`00000000-0000-0000-0000-000000000001`).

**E. Settlement + reconciliation:**
```
bank file upload (multipart) -> settlement_files(UPLOADED)
settlement processor (10s poll) -> PROCESSING
  -> export ledger rows (fixed-width) + write bank file to temp
  -> run C++ recon_engine -> exceptions + summary -> COMPLETED/FAILED
  -> publish reconciliation.exception.raised / reconciliation.batch.completed
RAG indexer indexes exceptions -> Copilot can answer "what happened to batch X?"
GET /api/v1/reconciliation/exceptions -> ops console / alerts
```

---

## 7. Invariants & consistency

1. **Balanced by construction** — app check in Rust + deferred constraint trigger in PG.
2. **Append-only ledger** — `ledger_entries` is write-once; corrections are reversals.
3. **Safely retryable** — Redis hot path + Postgres durable backstop + core re-check + `UNIQUE` key.
4. **Transactional outbox** — event publication commits atomically with the ledger write;
   the poller forwards to Kafka (at-least-once, `published` flag).
5. **Adversarial reconciliation** — the C++ engine never trusts the ledger's own view;
   three-way regard, exceptions surfaced, never auto-corrected.
6. **SERIALIZABLE isolation** for posting; gateway retries `ABORTED` serialization failures.
7. **Balance cache integrity** — materialized `account_balances` is audited every 24h
   against sums of `ledger_entries`.

Strong consistency is reserved for the ledger write path; everything downstream
(reporting, RAG index, dashboards, recon export) is eventually consistent via Kafka.
See `docs/06_CONSISTENCY_CONCURRENCY_MODEL.md` for the reasoning.

---

## 8. Running the system

```bash
cp .env.example .env          # then set API_KEYS and OPENAI_API_KEY
docker compose up -d --build  # core stack: postgres, redis, kafka, zookeeper, ledger-core, gateway, rag-copilot, rag-indexer
docker compose --profile batch up -d --build   # settlement processor + on-demand recon CLI
```

Verify:

```bash
curl http://localhost:8000/health     # gateway
curl http://localhost:8001/health     # rag copilot
curl http://localhost:8000/           # demo ops console (mock data)
curl http://localhost:8000/metrics    # prometheus
```

End-to-end smoke test (single-account demo):

```bash
curl -X POST http://localhost:8000/api/v1/accounts \
  -H "X-API-Key: sk_test_demo" -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"account_number":"MERCH-1001","account_type":"ASSET","owner_ref":"merchant_demo","currency":"INR"}'

# repeat for a LIABILITY account, then:
curl -X POST http://localhost:8000/api/v1/transactions \
  -H "X-API-Key: sk_test_demo" -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"transaction_type":"PAYMENT","reference_id":"order_1","entries":[
        {"account_id":"<UUID>","direction":"DEBIT","amount_minor":150000,"currency":"INR"},
        {"account_id":"<UUID>","direction":"CREDIT","amount_minor":150000,"currency":"INR"}]}'
```

Settlement round-trip:

```bash
python recon/scripts/make_bank_fixture.py \
  --db postgresql://ledger_app:dev_only@localhost:5432/ledger \
  --out /tmp/bank_settlement.txt --orphan 50000

curl -X POST http://localhost:8000/api/v1/settlements/upload \
  -H "X-API-Key: sk_test_demo" -H "Idempotency-Key: $(uuidgen)" \
  -F "batch_id=demo-batch-1" -F "file=@/tmp/bank_settlement.txt"

curl "http://localhost:8000/api/v1/settlements/demo-batch-1/status" -H "X-API-Key: sk_test_demo"
```

RAG question:

```bash
curl -X POST http://localhost:8001/api/v1/rag/ask -H "Content-Type: application/json" \
  -d '{"question":"How are reconciliation exceptions resolved?","scope":"compliance"}'
```

Standalone recon CLI (works on any fixed-width 43-column file):

```bash
docker compose --profile batch run --rm recon-engine \
  --ledger-export=/data/ledger_export.txt --bank-file=/data/bank_settlement.txt \
  --batch-id=batch_001 --tolerance=0
```

---

## 9. Tests & CI

CI (`.github/workflows/ci.yml`) runs, on push to `main` and on PRs:

- **gateway-tests**: `pip install -e ".[dev]"` then `pytest -v` (in `gateway/`).
- **rust-core**: install protoc, `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test`.
- **recon-engine**: install cmake/libpqxx/gtest, build (`-DENABLE_OPENMP=ON`), run `recon_tests`.
- **lint**: `pip install ruff`, `ruff check gateway/ rag/`, `ruff format --check gateway/ rag/`.

Local test commands (see `README.md` > Development):

```bash
cd gateway && pip install -e ".[dev]" && pytest
cd rag && pip install -e ".[dev]" && pytest
cd recon && cmake -B build -DBUILD_TESTS=ON -DENABLE_OPENMP=ON && cmake --build build && ./build/recon_tests
cd core && sqlx database create && sqlx migrate run --source ../db/migrations && cargo sqlx prepare -- --all-targets && cargo test
```

Ruff config: `gateway/pyproject.toml` pins `line-length = 120`; `rag/` uses the
ruff default (88). After any Python edit, run `ruff format gateway/ rag/` and
`ruff check gateway/ rag/` from the repo root.

---

## 10. Repository map

```
core/                 Rust ledger core (gRPC :50051) + outbox poller + auditor + sqlx cache
db/migrations/        SQL schema, applied by the core at startup (001..006)
gateway/              FastAPI gateway (:8000), idempotency/auth/rate-limit, settlement processor
rag/                  RAG copilot (:8001), Kafka indexer, policy corpus, eval harness
recon/                C++ reconciliation engine + fixture generator + benchmarks
settlement-processor/ Docker image for the batch settlement worker (gateway.app.settlement_processor)
frontend/             Vanilla-JS ops console (mock-data first, real-api ready)
monitoring/           Prometheus config + alerting rules + Grafana dashboard
load_test/            Concurrent posting harness against the gateway
docs/                 Design docs 00–07 + build checklist + design decisions
.github/workflows/    CI (gateway tests, rust-core, recon-engine, lint)
demo.py               Local demo driver
generate_data.py      Live data generator (drives transactions/exceptions)
```

---

## 11. Observability

- `GET /metrics` on the gateway exports Prometheus counters/histograms/gauges
  (`gateway/app/metrics.py`): HTTP request count/latency, active connections,
  idempotency replays (by source: redis/postgres), idempotency conflicts, rate-limit
  exceeded, gRPC calls/latency (by method/status).
- `monitoring/prometheus.yml` scrapes the gateway; Grafana dashboard shows throughput,
  p99 latency, replay rate, and recon exception rate. Alerting rules live in
  `monitoring/alerting_rules.yml`.
- `load_test/load_test.py` (concurrent posting) validates behavior under load.