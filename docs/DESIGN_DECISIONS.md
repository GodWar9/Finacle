# Design Decisions & Trade-offs

Each agent's key architectural choices and why.

---

## Agent A — Ledger Core (Rust)

### Why Rust
Financial ledgers are safety-critical. Rust's ownership model eliminates data races and use-after-free at compile time — bugs that would be silent memory corruption in C/C++ or GC pauses in Go/Java. The performance ceiling is C-like without the undefined behavior surface.

### SERIALIZABLE isolation (not READ COMMITTED)
`SERIALIZABLE` prevents write skew and phantoms without application-level locks. The alternative (pessimistic locking) would serialize all balance checks even for different accounts, killing throughput. `SERIALIZABLE` + retry on `40001` gives us optimistic concurrency — only conflicting transactions retry, not all transactions.

### Why an outbox table, not direct Kafka produce
Direct Kafka writes inside the transaction would couple the ledger to Kafka availability. If Kafka is down, the ledger is down. The outbox pattern writes events transactionally to Postgres (guaranteed durable), then an asynchronous poller publishes to Kafka. This means:
- The ledger never blocks on Kafka
- Events are never lost (outbox survives crashes)
- The poller can be restarted safely (`FOR UPDATE SKIP LOCKED`)

### Materialized `account_balances` + trigger
A trigger maintains the materialized balance on every insert, so `GetBalance` is an O(1) read instead of `SUM(amount)` over all entries. The trade-off is write amplification — every transaction touches `account_balances` too — but the balance table is small and hot, so this is cheap. The nightly auditor job catches any drift.

---

## Agent B — Reconciliation Engine (C++)

### Why C++, not Rust or Python
Reconciliation is batch/throughput-bound, not request/response. It processes millions of rows against fixed-width bank files — classic data-pipeline work where C++ with OpenMP delivers 2-4x throughput over single-threaded Rust (no async runtime overhead) and 10-50x over Python. The code is stateless and short-lived (runs as a CLI, not a daemon), so the memory-safety risk of C++ is contained.

### Three-way match, not trust-the-ledger
A common shortcut: trust the ledger's own balances and just diff against the bank file. This misses the case where the ledger is *internally consistent* but *externally incomplete* (e.g., a webhook that never arrived). The three-way match (gateway record → bank file → ledger) catches omissions on any side.

### CLI entrypoint, not a long-lived service
The engine runs per-settlement-file, not as a persistent service. This matches how real reconciliation works (scheduled batch jobs, not real-time streams). It also means:
- No idle resource consumption
- No connection pool management
- Easy to test and debug
- Runs anywhere (local, CI, Docker batch profile)

### Batched DB writes via `exec_params` (not COPY)
The current implementation uses individual `exec_params` per row. `pqxx::stream_to` (COPY protocol) would be faster for very large batches, but `exec_params` gives us better error handling per-row and is sufficient for the 100K-row scale we're targeting. The benchmark script can measure whether COPY is worth the complexity.

---

## Agent C — API Gateway (Python/FastAPI)

### Why Python for the gateway, not Rust
The gateway is I/O-bound (network calls to Redis, Postgres, gRPC), not CPU-bound. Python with async/await handles I/O concurrency well, and FastAPI's Pydantic validation catches bad input before it hits the Rust core. The gRPC call to the core is where latency-sensitive work happens — the gateway is just routing and middleware.

### Token-bucket rate limiting (not fixed-window)
Fixed-window counters allow 2x burst at window boundaries (e.g., 100 requests at T=0.999s + 100 at T=1.001s = 200 in 2 seconds). Token buckets enforce a smooth sustained rate. Implemented as a Lua script in Redis for atomicity — no race conditions between refill and consume.

### Idempotency: Redis hot path + Postgres durable backstop
Redis gives sub-millisecond idempotency lookups for the fast path. If Redis is down, the Postgres fallback ensures idempotency still works (slower, but correct). This is better than either alone:
- Redis-only: lost on crash
- Postgres-only: slower on every request

### Why gRPC to the core, not HTTP
The gateway and core are tightly coupled (same request/response shapes). gRPC gives us:
- Binary serialization (smaller payloads, faster parsing)
- Strong typing via protobuf (compile-time contract enforcement)
- Streaming support if needed later
- HTTP/2 multiplexing

---

## Agent D — RAG Compliance Copilot (Python)

### Why a separate RAG service, not embedded in the gateway
The RAG service has different scaling characteristics (CPU/GPU for embeddings, vector DB queries) and different availability requirements (can be down without affecting transactions). Keeping it separate means:
- Independent scaling (embeddings are expensive)
- Independent failure domain
- Can be rebuilt/re-indexed without restarting the gateway

### Two corpora, kept distinguishable
Policy documents (static, regulatory) and operational data (live transactions, reconciliation exceptions) are indexed separately with different `source_type` values. This allows scope-filtered queries (`scope=ops` vs `scope=compliance`) and prevents policy documents from drowning in operational noise.

### Seeding at startup (not on-demand ingestion)
Policy documents are seeded idempotently at service startup, not ingested via API calls. This means:
- No separate "ingest" step in the demo
- Always up-to-date with the policy files in the repo
- Idempotent — safe to restart

### OpenAI embeddings with all-zero fallback
When `OPENAI_API_KEY` is not set, the service uses all-zero vectors. This ensures the service starts and responds even without an API key (useful for demos and testing), but the similarity search results will be meaningless. The code explicitly documents this caveat.

---

## Cross-cutting decisions

### Event-driven architecture (Kafka)
Every component communicates through Kafka events, not direct HTTP calls. This means:
- Components can be restarted independently
- Events are replayable (new consumers catch up)
- The system is auditable (events are the source of truth)

### Docker Compose for local dev
All services run in Docker Compose for reproducibility. The trade-off is slower iteration (rebuild containers), but the alternative (install Postgres, Redis, Kafka, Rust toolchain, C++ toolchain locally) is worse. The `batch` profile keeps the settlement processor and recon engine off by default.

### No ORMs, raw SQL everywhere
ORMs add abstraction overhead and make it harder to reason about query performance. For a financial system where every query matters, raw SQL (with `sqlx` compile-time checking in Rust, `asyncpg` in Python) gives full control.
