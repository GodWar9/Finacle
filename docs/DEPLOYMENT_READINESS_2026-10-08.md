# Deployment status — 2026-10-08

Not ready for public production deployment yet.

Verified: 44 gateway tests (including six real HTTP-boundary regressions), seven RAG
unit tests, Python lint and formatting, Rust test compilation, four database-independent
Rust tests, Rust formatting and Clippy. Docker engine unavailable; no container,
Kafka-enabled build, ledger/pgvector integration or C++ reconciliation verification.

Fixed: auth-before-cache middleware ordering; middleware HTTP exceptions returning
proper JSON 4xx responses; portal static asset access; API_KEYS forwarding in Compose;
gRPC generation and pgvector image in CI. Existing unrelated working files preserved.

Required before a full Vercel frontend deployment:

- Provision backend, PostgreSQL/pgvector, Redis, Kafka and settlement services.
- Implement and test merchant authorization across reads/writes and isolate
  idempotency keys by tenant and operation, including the core ledger boundary.
- Authenticate RAG routes, especially policy ingestion.
- Replace the mock portal adapter with a tested live API bridge. Current list response
  objects and transaction details do not match the arrays/entries the UI expects.
- Use an authenticated server-side bridge; never embed shared API keys in public JS.
- Replace development credentials and restrict infrastructure ports.
- Run migrations, ledger concurrency/replay/reversal, recon and RAG end-to-end tests.

The existing frontend is a mock demo. Toggling USE_MOCK_DATA alone is insufficient.
No Vercel/backend hosting configuration or deployment connection was found locally.

Run each Python service's tests from its own directory; both services use an `app`
package name. CI follows this layout. Generated gateway stubs remain ignored files.
The shared audit report is in Tracker_intern/docs/DEPLOYMENT_READINESS_2026-10-08.md.
