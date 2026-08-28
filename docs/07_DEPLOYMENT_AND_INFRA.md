# 07 — Deployment & Infra

**Owner:** whoever finishes their component first / shared
**Depends on:** all component docs (01-04)

---

## 1. Local dev — docker-compose (get this running Day 1, before any app code)

```yaml
# docker-compose.yml
version: "3.9"
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: ledger
      POSTGRES_USER: ledger_app
      POSTGRES_PASSWORD: dev_only
    ports: ["5432:5432"]
    volumes:
      - ./db/migrations:/docker-entrypoint-initdb.d

  redis:
    image: redis:7
    ports: ["6379:6379"]

  kafka:
    image: confluentinc/cp-kafka:7.6.0
    depends_on: [zookeeper]
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
    ports: ["9092:9092"]

  zookeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181

  ledger-core:      # Rust, Agent A
    build: ./core
    depends_on: [postgres, kafka]
    ports: ["50051:50051"]   # gRPC

  recon-engine:      # C++, Agent B — run on-demand, not a long-lived service
    build: ./recon
    depends_on: [postgres]
    profiles: ["batch"]      # `docker compose --profile batch run recon-engine ...`

  gateway:            # Python, Agent C
    build: ./gateway
    depends_on: [postgres, redis, ledger-core]
    ports: ["8000:8000"]

  rag-copilot:          # Python, Agent D
    build: ./rag
    depends_on: [postgres, kafka]
    ports: ["8001:8001"]
```

## 2. Suggested repo layout (one repo, four component directories — keeps ownership boundaries visible in the file tree itself)

```
ledger-core/
├── docs/                          <- this document set
├── core/                          <- Agent A (Rust)
│   ├── src/ledger/
│   ├── proto/ledger.proto
│   └── Cargo.toml
├── recon/                         <- Agent B (C++)
│   ├── src/
│   ├── include/recon/
│   └── CMakeLists.txt
├── gateway/                       <- Agent C (Python)
│   ├── app/
│   └── pyproject.toml
├── rag/                           <- Agent D (Python)
│   ├── app/
│   ├── indexer/
│   └── pyproject.toml
├── db/
│   └── migrations/                <- owned jointly, changes require sign-off per doc 05
└── docker-compose.yml
```

## 3. Observability (minimum viable, worth having for the demo)

- `tracing`/`opentelemetry` in Rust, `structlog` in Python, all exporting to a single Jaeger/Tempo instance — a trace of one `POST /transactions` call should visibly span gateway → gRPC → Postgres commit → outbox → Kafka → RAG indexer, which is a genuinely good thing to show live in a hackathon defense.
- Postgres: `pg_stat_statements` enabled from the start.
- A single Grafana dashboard: transaction throughput, p99 posting latency, idempotency replay rate, reconciliation exception rate.

## 4. Production-shaped (not required to actually deploy, but good to have designed for a defense/interview)

- Ledger core: horizontally scaled behind the gateway, stateless except for the DB connection pool — safe to scale because all correctness lives in Postgres transactions, not in-process state.
- Postgres: primary + read replica; balance reads *could* go to the replica if you accept slightly stale reads for `GetBalance` — worth explicitly deciding and stating whether you do or don't, rather than leaving it ambiguous.
- Kafka: 3-broker minimum for the "explain your replication factor" question.
- Secrets: never in docker-compose for anything beyond local dev — mention Vault/AWS Secrets Manager in the doc even if you don't wire it up.
