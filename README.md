# Ledger Core - Double-Entry Core Banking Transaction Engine

A production-shaped core banking ledger system built with Rust, C++, and Python demonstrating real-world financial infrastructure patterns.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Gateway   │────▶│  Ledger     │────▶│ PostgreSQL  │
│  (Python)   │ gRPC│  Core (Rust)│     │  (Source)   │
└─────────────┘     └─────────────┘     └─────────────┘
                           │                    │
                           ▼                    ▼
                    ┌─────────────┐     ┌─────────────┐
                    │    Kafka    │     │  Outbox     │
                    │  (Events)   │     │  (Pattern)  │
                    └─────────────┘     └─────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
       ┌────────────┐ ┌──────────┐ ┌──────────┐
       │Recon Engine│ │RAG Copilot│ │Reporting │
       │  (C++)     │ │ (Python) │ │ (Python) │
       └────────────┘ └──────────┘ └──────────┘
```

## Components

| Component | Language | Purpose |
|-----------|----------|---------|
| **Ledger Core** | Rust | Money-movement hot path, double-entry enforcement, gRPC API |
| **Reconciliation Engine** | C++ | Batch three-way matching (ledger, gateway, bank files) |
| **API Gateway** | Python/FastAPI | Public REST API, idempotency, rate limiting, auth |
| **RAG Copilot** | Python | Grounded Q&A over policies + live operational data |

## Core Invariants

1. **Every transaction is balanced** - Sum of debits == sum of credits (enforced at DB + app level)
2. **Append-only ledger** - No UPDATE/DELETE on ledger_entries (WORM)
3. **Idempotent writes** - Idempotency-Key required for all POST, replay-safe
4. **Adversarial reconciliation** - Three-way match, exceptions never auto-resolved

## Quick Start

```bash
# Start all services
docker compose up -d

# Check health
curl http://localhost:8000/health
curl http://localhost:8001/health

# Post a transaction (requires Idempotency-Key)
curl -X POST http://localhost:8000/api/v1/transactions \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_type": "PAYMENT",
    "reference_id": "order_123",
    "entries": [
      {"account_id": "<ACCOUNT_UUID_1>", "direction": "DEBIT", "amount_minor": 10000, "currency": "INR"},
      {"account_id": "<ACCOUNT_UUID_2>", "direction": "CREDIT", "amount_minor": 10000, "currency": "INR"}
    ]
  }'
```

## Development

### Prerequisites
- Docker & Docker Compose
- Rust 1.78+ (for local ledger-core development)
- C++20 compiler + CMake (for local recon-engine development)
- Python 3.11+ (for gateway/rag development)

### Database Migrations
Migrations run automatically on container startup via `docker-entrypoint-initdb.d`

### Running Tests
```bash
# Rust tests
cd core && cargo test

# C++ tests
cd recon && cmake -B build -DBUILD_TESTS=ON && cmake --build build && ./build/recon_tests

# Python tests
cd gateway && pytest
cd rag && pytest
```

## Key Design Decisions

### Rust for Ledger Core
- Compile-time memory safety eliminates data races
- `sqlx` compile-checked SQL queries
- `SERIALIZABLE` isolation for concurrent balance updates
- Transactional outbox pattern for reliable event publishing

### C++ for Reconciliation
- Fixed-width file parsing (mainframe formats)
- CPU-bound parallel matching with OpenMP/std::execution
- `libpqxx` for high-throughput COPY operations

### Python for Gateway & RAG
- Fast iteration for business logic
- Rich ecosystem (FastAPI, Pydantic, OpenAI, pgvector)
- Natural fit for LLM integration

### Two-Tier Idempotency
- Redis: Sub-millisecond replay for hot path
- PostgreSQL: Durable backstop for Redis restarts

### Event-Driven Architecture
- Outbox pattern (never dual-write)
- Kafka for eventual consistency downstream
- Separate read models for reporting/RAG

## API Examples

### Post Transaction
```bash
curl -X POST http://localhost:8000/api/v1/transactions \
  -H "Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_type": "PAYMENT",
    "reference_id": "order_9f2a",
    "entries": [
      {"account_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "direction": "DEBIT", "amount_minor": 150000, "currency": "INR"},
      {"account_id": "ffffffff-gggg-hhhh-iiii-jjjjjjjjjjjj", "direction": "CREDIT", "amount_minor": 150000, "currency": "INR"}
    ],
    "narrative": "Payment for order_9f2a"
  }'
```

### Get Balance
```bash
curl http://localhost:8000/api/v1/accounts/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/balance
```

### Reverse Transaction
```bash
curl -X POST http://localhost:8000/api/v1/transactions/<TXN_ID>/reverse \
  -H "Idempotency-Key: 550e8400-e29b-41d4-a716-446655440001" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Customer refund request"}'
```

### Ask RAG Copilot
```bash
curl -X POST http://localhost:8001/api/v1/rag/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the reversal window for settled transactions?", "scope": "compliance"}'
```

## Reconciliation Engine Usage

```bash
# Run reconciliation (inside container)
docker compose --profile batch run --rm recon-engine \
  --ledger-export=/data/ledger_export.txt \
  --bank-file=/data/bank_settlement.txt \
  --batch-id=batch_001 \
  --tolerance=0
```

## Documentation

- `docs/00_SYSTEM_OVERVIEW.md` - System architecture and multi-agent build map
- `docs/01_LEDGER_CORE_ENGINE_RUST.md` - Ledger core specification
- `docs/02_RECONCILIATION_ENGINE_CPP.md` - Reconciliation engine specification
- `docs/03_API_GATEWAY_AND_IDEMPOTENCY_PYTHON.md` - Gateway specification
- `docs/04_RAG_COMPLIANCE_COPILOT.md` - RAG copilot specification
- `docs/05_DATABASE_AND_EVENT_SCHEMA.md` - Frozen schema contracts
- `docs/06_CONSISTENCY_CONCURRENCY_MODEL.md` - Consistency model reference
- `docs/07_DEPLOYMENT_AND_INFRA.md` - Deployment and infrastructure
- `docs/BUILD_CHECKLIST.md` - Daily build milestones

## License

MIT