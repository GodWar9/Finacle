# BUILD CHECKLIST — Daily Milestones (10-Day Build)

Mirrors the day-by-day format so each agent can track their own lane and sync at the checkpoints marked **[SYNC]**.

## Day 1 — Contracts frozen [SYNC — everyone in the room]
- [ ] `05_DATABASE_AND_EVENT_SCHEMA.md` finalized: Postgres schema, Kafka event shapes, `ledger.proto`, `rag_documents` schema
- [ ] `docker-compose.yml` up and running for all four agents locally
- [ ] Repo skeleton per `07_DEPLOYMENT_AND_INFRA.md` §2 created, each agent's directory scaffolded

## Day 2
- [ ] **Agent A:** domain types (`LedgerEntry`, `TransactionRequest`) + balance-check unit tests, no I/O yet
- [ ] **Agent B:** `SettlementRecord`/`ReconciliationException` types + fixed-width parser skeleton
- [ ] **Agent C:** FastAPI skeleton, Pydantic models mirroring `ledger.proto`, health check
- [ ] **Agent D:** ingestion pipeline for 3-5 static policy docs into `rag_documents`

## Day 3
- [ ] **Agent A:** `sqlx` migrations applied; `PostTransaction` happy path against local Postgres
- [ ] **Agent B:** single-threaded matcher correctness against hand-built fixture files
- [ ] **Agent C:** idempotency middleware (Redis-only path) with replay + conflict tests
- [ ] **Agent D:** retrieval endpoint (no generation yet) — verify embedding similarity search returns sane results

## Day 4
- [ ] **Agent A:** idempotency lookup/replay integrated into `PostTransaction`
- [ ] **Agent B:** parallelize the matcher; benchmark on a synthetic 1M-row batch
- [ ] **Agent C:** Postgres fallback for idempotency; concurrent-same-key lock test
- [ ] **Agent D:** grounded-answer generation wired up with citation formatting

## Day 5 — [SYNC] gRPC integration checkpoint
- [ ] **Agent A + C together:** gateway successfully calls the real Rust core end-to-end (not mocked) for `PostTransaction` and `GetBalance`
- [ ] **Agent B:** `libpqxx` write path into `reconciliation_exceptions` with batched `COPY`
- [ ] **Agent D:** Kafka consumer skeleton for `ledger.transaction.posted` (can point at Agent A's test events)

## Day 6
- [ ] **Agent A:** outbox pattern implemented — transactional insert into `outbox_events`, poller publishing to Kafka
- [ ] **Agent B:** CLI entrypoint (`recon_engine --ledger-export=... --bank-file=...`) functional end-to-end
- [ ] **Agent C:** ABORTED-retry path (SERIALIZABLE conflict handling) implemented and tested
- [ ] **Agent D:** scope filtering (`ops` vs `compliance`) working; first pass at the 15-20 question eval set

## Day 7 — [SYNC] Full event-flow checkpoint
- [ ] A transaction posted through the gateway is visible, end-to-end, in the RAG index within a few seconds (gateway → core → outbox → Kafka → RAG indexer)
- [ ] A synthetic settlement file run through Agent B's engine against Agent A's ledger export produces the expected exceptions, and those exceptions show up in a RAG query

## Day 8
- [ ] **Agent A:** materialized `account_balances` + nightly balance-auditor job
- [ ] **Agent B:** rule-engine tolerance config (`amount_tolerance_minor`) made configurable, not hardcoded
- [ ] **Agent C:** rate limiting (token bucket) per merchant
- [ ] **Agent D:** finish eval set, fix any wrongly-grounded answers (check citations, not just fluency)

## Day 9 — Load/failure testing + docs polish
- [ ] Concurrent-posting load test against a single account — verify no lost updates, no double-posts under retry storms
- [ ] Kill Redis mid-test — verify idempotency still works via Postgres fallback
- [ ] Kill the outbox poller mid-run, restart it — verify no events are lost
- [ ] Observability: one Grafana dashboard showing throughput, p99 latency, idempotency replay rate, reconciliation exception rate

## Day 10 — Defense/demo prep
- [ ] Live demo script: post a transaction → show it balanced in Postgres → show the Kafka event → show it queryable via the RAG copilot → run a settlement file with a deliberate mismatch through recon → show the exception → ask the RAG copilot "why did this fail"
- [ ] Each agent can independently explain their component's design trade-offs (this is what actually gets evaluated in a real interview/defense, more than feature count)
