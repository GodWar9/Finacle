# Finacle — Resume / interview draft

> Truthful bullets only; everything maps to a component or a line in `docs/PROGRESS.md`.
> Replace the bracketed metrics with the numbers captured in Phase 2 of `NEXT-STEPS.md`
> once they're recorded — do not invent numbers now.

---

## About this project (2-line summary)
Finacle is an end-to-end double-entry core-banking ledger built on a deliberately narrow
contract: serializable single-writer Postgres for money movement, an outbox for
audit/reconciliation/Kafka events, a C++ reconciliation engine for bank-file matching,
and a polyglot FastAPI gateway with two-tier idempotency. A RAG copilot answers
customer-support and exception queries over the same Kafka events. Built solo,
~2,500 LOC across Rust / C++17 / Python.

## Core stack
Rust (axum, tonic gRPC, sqlx) · PostgreSQL (SERIALIZABLE, deferred constraint triggers,
pgvector) · C++17 (libpqxx, OpenMP) · Python/FastAPI · Redis (IDEMPOTENCY) ·
Kafka/Redpanda · Docker Compose · Prometheus + Grafana · GitHub Actions.

---

## Bullets (pick per role)

### Ledger correctness / database design
- Owned a double-entry ledger: every post is validated app-side (balanced check,
  `core/src/ledger/domain.rs:92`) **and** enforced DB-side (deferred `CONSTRAINT TRIGGER`
  so an unbalanced commit is refused at `COMMIT`, `db/migrations/001_initial_schema.sql:50-71`)
  with materialized account balances kept in sync by trigger.
- Engineered idempotency as two tiers behind the HTTP layer: a Redis hot-path
  (instant replay, correct `Idempotency-Key` header) plus a Postgres durable backstop
  (`idempotency_key` UNIQUE + 24h TTL), distinguishing *replay* (same key + same body →
  cached response) from *conflict* (same key, different body → 409).
- Handled concurrent posts to the same account via a single `SERIALIZABLE` transaction with
  retry-on-`40001` at the gRPC boundary; added a 10-way concurrency test asserting exactly
  the right number of commits and an exact final balance.
- Designed WORM (write-once-read-many) accounting: application code has no update/delete
  path; reversal is a first-class compensating transaction linked to the original.
  *(DB-enforced via append-only triggers, migration `007`, with an immutability test — landed
  in the v1 push, scope M6; execute in CI before claiming.)*
- Kept the audit path honest by storing the outbox write **in the same transaction** as the
  posting (`outbox_rows` in the post tx), so a posting that commits always has its event;
  the poller reads with `FOR UPDATE SKIP LOCKED` so a crashed poller never double-publishes
  one event as two (at-least-once without double-spend).

### Distributed systems / resilience
- Implemented crash-safe outbox: SQL `SKIP LOCKS`, publish-after-send, idempotent Kafka
  consumers on the RAG side; verified fault behavior with concurrent-key tests.
- Settlement processing self-heals: a `PROCESSING` batch stale for N poll cycles is requeued
  to `UPLOADED` instead of wedging the queue. *[added in v1 push, scope S3]*

### Performance / correctness under load
- Built a benchmark + load harness for the whole pipeline (gateway load test ·
  1M-row recon benchmark) and publication-grade measurement discipline (hardware + method +
  p50/p95/p99 in `docs/PROGRESS.md`).
  *[replace with: postings/s = ___ (machine spec); 1M-row recon = ___ s → ___ rows/s]*

### Reconciliation engine (C++)
- Built the bank-file matcher in C++17: fixed-width parsing, deterministic keying by
  merchant-reference, byte-level robustness (reject-on-reorder), and typed exceptions
  (AmountMismatch, MissingInLedger, MissingInBankFile, StatusMismatch, Duplicate…)
  with a per-transaction tolerance model — naive (projected) income = posted credibility.
- Wrote OpenMP-parallel matching + unit tests (GTest) so the matcher's behavior is pinned.
- Chose a serialize-then-match architecture over COPY for traceable, smaller diffs —
  documented rationale with latency vs batching trade-offs (`DESIGN_DECISIONS.md`).

### API / product surface (Python, FastAPI)
- FastAPI gateway: auth (X-API-Key → merchant), rate-limit (sliding-window counter),
  big-O-safe responses, HMAC-signed webhooks, typed Router->gRPC transport to the Rust core,
  `ABORTED`/retry semantics from the gRPC server — curl-tested in README.
- Served a zero-build static account/settlement UI straight from the gateway (dead-simple
  demo) and a mock-data mode so the frontend is always demonstrable.

### RAG copilot (retrieval-augmented)
- Indexed `TXN_NARRATIVE` + `RECONCILIATION_EXCEPTION` (pgvector, ivfflat) via a Kafka
  consumer; answers come back only when a similarity threshold is actually crossed, else
  "no answer" — wrong-answer control is a product decision, not an accident.
- Built a 15-question eval set with expected source-types/answer keywords
  (`rag/eval/evaluate.py`), guarded against mis-reading zero vectors when a key is absent,
  and wired the eval into CI as a nightly job gated on a minimum pass-rate.
  *(scope S1; needs a real `OPENAI_API_KEY` + indexed docs to ever record a pass %.)*

### Workflow / engineering hygiene
- Green CI across Rust (`fmt`/`clippy -D warnings`/pytest DB tests), C++, gateway, rag,
  ruff (two lint configs deliberate and documented).
- Compile-time-safe SQL via sqlx offline cache; migrations owned by the core at startup.

---

## Conversation starters (for the interviewer)
- Q: "How do you guarantee you never double-spend under retry?" → outbox-in-tx + idempotent
  keys + replay-vs-conflict semantics + concurrent-same-key test. (see `docs/03`, `service.rs`)
- Q: "What does a failed mid-transaction retry leave behind?" → nothing: row count checked,
  unique violation → replay response; crash before COMMIT = no row.
- Q: "Why C++ for reconciliation instead of Python?" → deterministic byte-keying, math
  boundaries, OpenMP; the actual trade-off doc`DESIGN_DECISIONS`).
- Q: "What's the weak part you'd do differently?" → measured perf evidence came last, not
  first (fix = `benchmark_recon` + load harness run-day-one); WORM was app-only until the v1
  push made it DB-forced; and `demo.py` needed a real seeding pass before it was demonstrable.

---

## Do NOT claim (until performed)
- "Postgres handles N posting/s" — no measured number exists yet in-repo.
- "Fully Kubernetes/scale-out" — single-writer, docker-compose deployment.
- "Immutable by database" — migration `007` + its test exist but have **never executed**
  against a real Postgres; claim only after a green CI run.
- "CI is green" — as of 2026-09-19 all red blockers are removed (clippy fixed, Postgres
  service + `DATABASE_URL` added, rag pytest job added) and every check that can run locally
  passes; the first GitHub Actions run is still pending and is the proof to cite.