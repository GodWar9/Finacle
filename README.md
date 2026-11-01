# Finacle — Double-Entry Core Banking Transaction Engine

A production-shaped core-banking ledger — the category of system behind payment platforms and bank cores. Every rupee moves as a balanced double entry, every write is safely retryable, and every settlement file is reconciled against the ledger with drift **surfaced, never silently written off**.

**Polyglot by design:** Rust for the money-movement hot path (memory safety + compile-checked `sqlx`), C++ for throughput-bound batch reconciliation (fixed-width mainframe formats), Python for the gateway and RAG compliance copilot.

See [product readiness & setup](docs/PRODUCT_READINESS.md) for portal auth, tenant migrations, Playwright and remaining deployment gates.

## Architecture

```
Demo console / curl
        │  HTTP (X-API-Key · Idempotency-Key)
        ▼
 API Gateway :8000 (FastAPI) ──gRPC──▶ Ledger Core :50051 (Rust) ──sqlx──▶ PostgreSQL (+pgvector)
  auth · rate-limit                     SERIALIZABLE · WORM entries        source of truth + outbox
  2-tier idempotency · portal                    │ outbox → Kafka                    │
                                                 ▼                                   │
                                              Kafka ◄────────────────────────────────┘
                              ┌──────────────────┼───────────────────┐
                              ▼                  ▼                   ▼
                        RAG Indexer        RAG Copilot :8001    Settlement Processor
                        (Kafka consumer)   grounded Q&A         └─▶ C++ recon engine
```

| Component | Language | Role | Endpoint |
|---|---|---|---|
| Ledger Core | Rust (tonic/sqlx) | Posting, balance, reversal; double-entry + WORM + outbox | `:50051` gRPC |
| API Gateway | Python (FastAPI) | REST API, auth, rate limiting, idempotency, static portal | `:8000` |
| RAG Copilot | Python (FastAPI) | Grounded Q&A over policies + live operational events | `:8001` |
| RAG Indexer | Python (aiokafka) | Ledger events → vector index | internal |
| Reconciliation Engine | C++ (libpqxx, OpenMP) | Batch three-way matching, exceptions + summaries | batch CLI |
| Settlement Processor | Python | Picks up uploads, runs recon, tracks status | batch |
| Stream + store | Kafka / PostgreSQL / Redis | Event bus, source of truth, idempotency cache | — |

## Core invariants

1. **Balanced by construction** — debits == credits, enforced by a deferred constraint trigger in the DB (`db/migrations/001_initial_schema.sql`), not just app code.
2. **Append-only** — `ledger_entries` is write-once; corrections are new transactions.
3. **Safely retryable** — every POST needs an `Idempotency-Key`; replays return the original response (Redis hot path + Postgres backstop). Same key + different body → `409`.
4. **Transactional outbox** — the ledger write and event publication commit atomically; a poller forwards to Kafka (`ledger.transaction.posted` / `.reversed`).
5. **Adversarial reconciliation** — three-way match across payment-gateway record, bank settlement file and internal ledger; every mismatch becomes an exception, never auto-resolved.
6. **Concurrency-safe** — `SERIALIZABLE` posting; the gateway retries transient serialization failures.

## Quick start

```bash
cp .env.example .env
# set API_KEYS=dev-key-123:merchant_demo
# set OPENAI_API_KEY=sk-...   (optional — enables RAG embeddings/answers)

docker compose up -d --build                      # core stack
docker compose --profile batch up -d --build      # settlement processor + recon CLI
```

First build compiles Rust (rdkafka) and C++ (OpenMP + libpqxx) — allow several minutes. Without `OPENAI_API_KEY` everything runs except policy seeding, Kafka event indexing and `/api/v1/rag/ask`.

```bash
curl localhost:8000/health      # gateway
curl localhost:8001/health      # RAG copilot
curl localhost:8000/            # demo console (mock data)
curl localhost:8000/metrics     # Prometheus
```

## API

All endpoints are under `/api/v1`, authenticated with `X-API-Key`. Every `POST`/upload also requires `Idempotency-Key`. Amounts are **minor units** (`150000` = ₹1,500.00).

```bash
# Create accounts (repeat for a LIABILITY account)
curl -X POST localhost:8000/api/v1/accounts \
  -H "X-API-Key: dev-key-123" -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{"account_number":"MERCH-1001","account_type":"ASSET","owner_ref":"merchant_demo","currency":"INR"}'

# Post a balanced transaction
curl -X POST localhost:8000/api/v1/transactions \
  -H "X-API-Key: dev-key-123" -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{"transaction_type":"PAYMENT","reference_id":"order_1234","narrative":"Payment for order_1234",
       "entries":[{"account_id":"<A1>","direction":"DEBIT","amount_minor":150000,"currency":"INR"},
                  {"account_id":"<A2>","direction":"CREDIT","amount_minor":150000,"currency":"INR"}]}'

# Balance / history / reverse
curl "localhost:8000/api/v1/accounts/<A1>/balance" -H "X-API-Key: dev-key-123"
curl "localhost:8000/api/v1/transactions?limit=10"  -H "X-API-Key: dev-key-123"
curl -X POST "localhost:8000/api/v1/transactions/<TXN>/reverse" \
  -H "X-API-Key: dev-key-123" -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{"idempotency_key":"'$(uuidgen)'","reason":"Customer refund"}'
```

Reversals post mirrored entries, mark the original `REVERSED` and publish `ledger.transaction.reversed`.

## Settlement & reconciliation

```bash
python recon/scripts/make_bank_fixture.py --db postgresql://ledger_app:dev_only@localhost:5432/ledger \
  --out /tmp/bank_settlement.txt --orphan 50000

curl -X POST localhost:8000/api/v1/settlements/upload \
  -H "X-API-Key: dev-key-123" -H "Idempotency-Key: $(uuidgen)" \
  -F "batch_id=demo-batch-1" -F "file=@/tmp/bank_settlement.txt"

curl localhost:8000/api/v1/settlements/demo-batch-1/status -H "X-API-Key: dev-key-123"
```

Status flows `UPLOADED → PROCESSING → COMPLETED | FAILED`. Results land in `reconciliation_batch_summary` and `reconciliation_exceptions`; mismatches emit `reconciliation.exception.raised` events for the RAG indexer.

**Standalone CLI** (fixed-width, 43 columns: reference 20@0, amount 13@20, currency 3@33, status 7@36; `POSTED` required to match):

```bash
docker compose --profile batch run --rm recon-engine \
  --ledger-export=/data/ledger_export.txt --bank-file=/data/bank_settlement.txt \
  --batch-id=batch_001 --tolerance=0
```

Exception types: `MISSING_IN_LEDGER`, `MISSING_IN_BANK_FILE`, `AMOUNT_MISMATCH`, `DUPLICATE`, `STATUS_MISMATCH`.

## RAG compliance copilot

Policies in `rag/policies/*.txt` are seeded at startup; live transactions and exceptions are indexed from Kafka. `scope=ops` → transaction/exception narratives; `scope=compliance` → policies/circulars. Answers cite `[Source N]`, and say so when the index has no answer rather than guessing.

```bash
curl -X POST localhost:8001/api/v1/rag/ask -H "Content-Type: application/json" \
  -d '{"question":"How are reconciliation exceptions resolved?","scope":"compliance"}'
```

## Development

```bash
# Python
cd gateway && pip install -e ".[dev]" && pytest
cd rag     && pip install -e ".[dev]" && pytest

# C++ (cmake + libpqxx)
cd recon && cmake -B build -DBUILD_TESTS=ON -DENABLE_OPENMP=ON && cmake --build build && ./build/recon_tests

# Rust (protoc + reachable Postgres; prepare the .sqlx offline cache first)
cd core
cargo install sqlx-cli --no-default-features --features postgres
sqlx database create && sqlx migrate run --source ../db/migrations
cargo sqlx prepare -- --all-targets
cargo test
```

Keep `cargo fmt --check` and `cargo clippy -- -D warnings` clean. Migrations are owned and applied by ledger-core at startup via `sqlx::migrate!`.

## Observability & load

`monitoring/` has Prometheus alert rules and a Grafana dashboard (throughput, p99 latency, idempotency replay rate, exception rate). `load_test/` is a concurrent-posting harness. The gateway exposes `/metrics`.

## Documentation

| Doc | Contents |
|---|---|
| `docs/00_SYSTEM_OVERVIEW.md` | Architecture + multi-agent build map |
| `docs/01_LEDGER_CORE_ENGINE_RUST.md` | Rust core spec |
| `docs/02_RECONCILIATION_ENGINE_CPP.md` | Recon engine spec |
| `docs/03_API_GATEWAY_AND_IDEMPOTENCY_PYTHON.md` | Gateway spec |
| `docs/04_RAG_COMPLIANCE_COPILOT.md` | RAG copilot spec |
| `docs/05_DATABASE_AND_EVENT_SCHEMA.md` | Frozen schema + event contracts |
| `docs/06_CONSISTENCY_CONCURRENCY_MODEL.md` | Consistency model |
| `docs/07_DEPLOYMENT_AND_INFRA.md` | Deployment + infra |
| `docs/PRODUCT_READINESS.md` | Portal auth, tenancy, Playwright, deployment gates |

## License

MIT
