# Finacle — Double-Entry Core Banking Transaction Engine

A production-shaped core-banking ledger: the same category of system that sits behind
Razorpay/PayU/Stripe-style platforms and bank cores. Every rupee moves as a balanced
double-entry, every API call is safely retryable, and every settlement file is reconciled
against the internal ledger with drift surfaced automatically — never silently written off.

**Polyglot by design.** The money-movement hot path is Rust (memory safety + `sqlx`
compile-checked SQL), batch reconciliation is C++ (throughput-bound, fixed-width
mainframe-format parsing), and the gateway + RAG copilot are Python (fast iteration for
business logic and LLM integration).

## Architecture

```
┌───────────────────────┐
│  Demo console / curl  │
└───────────┬───────────┘
            │ HTTP (X-API-Key · Idempotency-Key)
            ▼
  ┌─────────────────────┐   gRPC   ┌──────────────────┐        ┌──────────────┐
  │  API Gateway (8000) │────────▶ │  Ledger Core     │───────▶│ PostgreSQL   │
  │  FastAPI            │          │  Rust · 50051    │ sqlx   │ (pgvector)   │
  │  · auth/rate-limit  │          │  SERIALIZABLE    │ migrate│ source of    │
  │  · 2-tier idempot.  │          │  WORM entries    │        │ truth + outbox│
  │  · serves portal at / │          └────────┬─────────┘        └──────┬───────┘
  └─────────────────────┘                   │ outbox→Kafka           │
                                            ▼                        │
                                    ┌──────────────┐                 │
                                    │    Kafka     │◄────────────────┘
                                    └──────┬───────┘
          ┌───────────────────────────────┬┴───────────────┐
          ▼                               ▼                ▼
   ┌──────────────┐               ┌──────────────┐  ┌──────────────┐
   │ RAG Indexer  │               │ RAG Copilot  │  │ Settlement   │
   │ (Kafka cons) │ updated via   │ (8001) Q&A   │  │ Processor    │──► C++ recon engine
   │              │ shared DB     │ grounded in  │  │ (batch)      │──► exceptions + summary
   └──────────────┘               │ rag_documents │  └──────────────┘
                                  └──────────────┘
```

## Core Components

| Component | Language | Role | Endpoint |
|---|---|---|---|
| **Ledger Core** | Rust (tonic/sqlx) | Posting, balance, reversal; double-entry + WORM + outbox | `:50051` gRPC |
| **API Gateway** | Python (FastAPI) | Public REST API, auth, rate limiting, two-tier idempotency, static portal | `:8000` |
| **RAG Copilot** | Python (FastAPI) | Grounded Q&A over policies + live operational events | `:8001` |
| **RAG Indexer** | Python (aiokafka) | Consumes ledger events → vector index | internal |
| **Reconciliation Engine** | C++ (libpqxx) | Batch fixed-width matching, writes exceptions + summaries | batch CLI |
| **Settlement Processor** | Python | Picks up uploaded files, runs recon, tracks status | batch |
| **Stream + Store** | Kafka / PostgreSQL / Redis | Event bus, source of truth (pgvector), idempotency cache | — |

## Core Invariants

1. **Balanced by construction** — sum of debits == sum of credits, enforced by a deferred
   constraint trigger at the DB, not just in app code (`db/migrations/001_initial_schema.sql`).
2. **Append-only ledger** — `ledger_entries` is write-once; corrections are new transactions,
   never updates.
3. **Safely retryable** — every POST requires an `Idempotency-Key`; replays return the original
   response (Redis hot path + Postgres durable backstop). Reusing a key with a *different* body is a 409.
4. **Transactional outbox** — event publication and the ledger write commit atomically; a poller
   forwards `outbox_events` to Kafka (`ledger.transaction.posted` / `.reversed`).
5. **Adversarial reconciliation** — the C++ engine performs a three-way match across the payment
   gateway record, bank settlement file, and internal ledger; every mismatch is flagged as an
   exception and never auto-resolved.
6. **Concurrency-safe** — `SERIALIZABLE` isolation on posting; the gateway retries transient
   serialization failures.

## Quick Start

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env` — at minimum set:

```bash
# Comma-separated key:merchant_id pairs used for X-API-Key authentication
API_KEYS=dev-key-123:merchant_demo

# Required to activate RAG embeddings + answer generation
OPENAI_API_KEY=sk-...
```

> Without `OPENAI_API_KEY` the whole stack runs, but policy seeding and Kafka event indexing
> are skipped (a zero-vector guard) and `/api/v1/rag/ask` reports that no key is configured.

### 2. Start

```bash
# Core stack (postgres, redis, kafka, zookeeper, ledger-core, gateway, rag-copilot, rag-indexer)
docker compose up -d --build

# Batch workers: settlement processor (+ on-demand recon CLI)
docker compose --profile batch up -d --build
```

> First builds compile the Rust core (rdkafka), the C++ recon engine (OpenMP + libpqxx) and
> Python images — allow several minutes.

### 3. Verify

```bash
curl http://localhost:8000/health          # gateway
curl http://localhost:8001/health          # rag copilot
curl http://localhost:8000/                # demo console (static portal, mock data)
curl http://localhost:8000/metrics         # prometheus metrics
```

## Using the API

All endpoints live under `/api/v1`. **Auth:** send `X-API-Key: <key from API_KEYS>`.
**Writes:** every `POST`/multipart upload must also send an `Idempotency-Key` header.

### Create accounts

```bash
curl -X POST http://localhost:8000/api/v1/accounts \
  -H "X-API-Key: dev-key-123" \
  -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{"account_number":"MERCH-1001","account_type":"ASSET","owner_ref":"merchant_demo","currency":"INR"}'
# repeat for a second account with account_type "LIABILITY"; copy both account_id values
```

### Post a transaction

```bash
curl -X POST http://localhost:8000/api/v1/transactions \
  -H "X-API-Key: dev-key-123" \
  -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{
    "transaction_type": "PAYMENT",
    "reference_id": "order_1234",
    "narrative": "Payment for order_1234",
    "entries": [
      {"account_id": "<ACCOUNT_UUID_1>", "direction": "DEBIT",  "amount_minor": 150000, "currency": "INR"},
      {"account_id": "<ACCOUNT_UUID_2>", "direction": "CREDIT", "amount_minor": 150000, "currency": "INR"}
    ]
  }'
```

Amounts are **minor units** (150000 = ₹1,500.00). Re-send the same key+body and you get the
identical response (idempotent replay); a different body under the same key is a `409`.

### Read balance / history / reverse

```bash
curl "http://localhost:8000/api/v1/accounts/<ACCOUNT_UUID_1>/balance" -H "X-API-Key: dev-key-123"
curl "http://localhost:8000/api/v1/transactions?limit=10" -H "X-API-Key: dev-key-123"

curl -X POST "http://localhost:8000/api/v1/transactions/<TXN_ID>/reverse" \
  -H "X-API-Key: dev-key-123" \
  -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{"idempotency_key":"$(uuidgen)","reason":"Customer refund"}'
```

Reversals post mirrored entries, mark the original `REVERSED`, and publish
`ledger.transaction.reversed` — balances net back to zero.

## Settlement & Reconciliation

1. **Make a fixture** from real posted ledger rows:

   ```bash
   python recon/scripts/make_bank_fixture.py \
     --db postgresql://ledger_app:dev_only@localhost:5432/ledger \
     --out /tmp/bank_settlement.txt --orphan 50000
   ```

2. **Upload it** through the gateway (multipart; `Idempotency-Key` still required):

   ```bash
   curl -X POST http://localhost:8000/api/v1/settlements/upload \
     -H "X-API-Key: dev-key-123" \
     -H "Idempotency-Key: $(uuidgen)" \
     -F "batch_id=demo-batch-1" -F "file=@/tmp/bank_settlement.txt"
   ```

3. **Watch it process** (the settlement processor runs the C++ engine):

   ```bash
   curl "http://localhost:8000/api/v1/settlements/demo-batch-1/status" -H "X-API-Key: dev-key-123"
   ```

   Status flows `UPLOADED → PROCESSING → COMPLETED | FAILED`. Results land in
   `reconciliation_batch_summary` + `reconciliation_exceptions`, and unmatched/amount-mismatched
   rows become forwardable `reconciliation.exception.raised` events for the RAG indexer.

### Standalone recon CLI

Works on any fixed-width files (43 columns: reference 20 @0, amount 13 @20, currency 3 @33,
status 7 @36 — blank-padded, `POSTED` status required to match):

```bash
docker compose --profile batch run --rm recon-engine \
  --ledger-export=/data/ledger_export.txt \
  --bank-file=/data/bank_settlement.txt \
  --batch-id=batch_001 \
  --tolerance=0
```

Exception types: `MISSING_IN_LEDGER`, `MISSING_IN_BANK_FILE`, `AMOUNT_MISMATCH`,
`DUPLICATE`, `STATUS_MISMATCH`.

## RAG Copilot

Policy documents (`rag/policies/*.txt`) are seeded at startup when a key is present, and live
transactions/exceptions are indexed from Kafka. Ask with a scope to limit retrieval:

```bash
curl -X POST http://localhost:8001/api/v1/rag/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"How are reconciliation exceptions resolved?","scope":"compliance"}'
```

`scope=ops` → transaction/exception narratives; `scope=compliance` → policies/circulars.
Responses cite `[Source N]`; if the index has no answer it says so rather than guessing.

## Database Migrations

Owned and applied by **ledger-core** at startup via `sqlx::migrate!` (`db/migrations/`,
tracked in `_sqlx_migrations`). Standalone: `sqlx migrate run --source db/migrations`.
Feature highlights: vector store (`002_rag_documents.sql`), reconciliation summaries,
settlement file tracking with processing state.

## Development

### Tests

```bash
# Python: gateway + rag (dedicated venv recommended)
cd gateway && pip install -e ".[dev]" && pytest
cd rag && pip install -e ".[dev]" && pytest

# C++ (needs cmake + libpqxx)
cd recon && cmake -B build -DBUILD_TESTS=ON -DENABLE_OPENMP=ON && cmake --build build && ./build/recon_tests

# Rust (needs protoc + reachable Postgres; prepare the offline .sqlx cache first)
cd core
cargo install sqlx-cli --no-default-features --features postgres
sqlx database create && sqlx migrate run --source ../db/migrations
cargo sqlx prepare -- --all-targets
cargo test
```

### Local Rust notes

`build.rs` needs `protoc` (point `PROTOC` at a binary), and `sqlx` queries are
compile-time-checked via the versioned `core/.sqlx` offline cache. `cargo fmt --check` and
`cargo clippy -- -D warnings` should stay clean.

## Observability & Load

- `monitoring/` — Prometheus alerting rules + a Grafana dashboard (throughput, p99 latency,
  idempotency replay rate, reconciliation exception rate).
- `load_test/` — concurrent posting harness against the gateway.
- `gateway` exposes `/metrics`; the portal objects in the console show live-ish service status.

## Documentation

| Doc | Contents |
|---|---|
| `docs/00_SYSTEM_OVERVIEW.md` | Architecture + multi-agent build map |
| `docs/01_LEDGER_CORE_ENGINE_RUST.md` | Rust core spec |
| `docs/02_RECONCILIATION_ENGINE_CPP.md` | Recon engine spec |
| `docs/03_API_GATEWAY_AND_IDEMPOTENCY_PYTHON.md` | Gateway spec |
| `docs/04_RAG_COMPLIANCE_COPILOT.md` | RAG copilot spec |
| `docs/05_DATABASE_AND_EVENT_SCHEMA.md` | Frozen schema + event contracts |
| `docs/06_CONSISTENCY_CONCURRENCY_MODEL.md` | Consistency model reference |
| `docs/07_DEPLOYMENT_AND_INFRA.md` | Deployment + infrastructure |

## License

MIT