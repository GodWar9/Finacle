# Finacle deployment build plan

Updated 2026-10-09. Free-tier services only; a fully working app is the target.

Verified baseline: 44 gateway and seven RAG tests, Python lint/format, Rust test
compilation, four database-independent Rust tests, Rust formatting and Clippy.
See DEPLOYMENT_READINESS_2026-10-08.md for limitations.

1. Preserve the tested auth-before-idempotency ordering and HTTP error handling.
2. Enforce merchant ownership on every account/transaction read and write in both
   gateway and ledger. Scope idempotency by tenant and operation; test cross-tenant
   replay, mismatched payloads, concurrency and reversals against real pgvector.
3. Authenticate RAG policy ingestion and reads; restrict service exposure.
4. Replace the portal's mock adapter with a Vercel server-side authenticated API
   bridge and tested mappings for list envelopes and transaction entries.
5. Run the full PostgreSQL, Redis, Kafka, settlement and reconciliation stack in
   containers; prove migrations, outbox delivery, recovery and reconciliation.
6. Select free services only after confirming persistent storage and worker limits.
   No hosting account has been established; do not present an ephemeral demo as a
   durable ledger service.
7. Deploy, run authenticated browser acceptance checks, and document rollback,
   recovery and the architecture for an internship interview.

Zex is prioritized first, followed by LSTM; Finacle needs the most integration work.
