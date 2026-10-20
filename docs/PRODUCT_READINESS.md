# Product readiness — 2026-10-16

The portal now has an authenticated live mode and an explicitly labelled synthetic
demo at `/?demo=1`. It is served by the gateway, so no public browser API key or
separate cross-origin bridge is needed. Account, transaction and exception response
envelopes are mapped correctly. Failed posting attempts reuse their key while the
draft is unchanged, and untrusted narratives and citation labels render as text.

## Start locally

Copy `.env.example` to `.env`. Set `API_KEYS`, a random `LEDGER_SERVICE_TOKEN` of at
least 32 characters, `PORTAL_PASSWORD` (at least 16), `PORTAL_SESSION_SECRET` (at
least 32), and `PORTAL_MERCHANT_ID`. Generate private random values; the examples
are not production secrets. Keep the token identical in the gateway and core.
Then run `docker compose up --build`. Browse `http://127.0.0.1:8000`.

Create accounts through `POST /api/v1/accounts` using the merchant's API key and
an `Idempotency-Key`. The authenticated merchant owns the account; `owner_ref`
remains the customer/business reference and does not grant access. The portal
loads accounts for the signed-in operator before enabling transaction entry.

RAG is optional. Set `OPENAI_API_KEY`, map a private key to the same merchant in
`RAG_API_KEYS`, and set that key as `RAG_SERVICE_KEY` in the gateway. Policy uploads
require a separate `RAG_ADMIN_KEY` of at least 32 characters. Missing setup produces
an explicit unavailable state. The indexer does not consume/acknowledge events
without a model key; restart it after configuring the key. Successful indexing
commits Kafka offsets; a processing error stops before acknowledging the event.

Webhooks require a private `WEBHOOK_SECRET` and `WEBHOOK_CLEARING_ACCOUNTS`, a JSON
mapping of merchant IDs to clearing account UUIDs owned by that same merchant.
Reversal requests require matching body and header idempotency keys.

## Existing databases

Apply migrations through core startup. Migration 008 introduces immutable merchant
ownership. Existing accounts intentionally remain unassigned and inaccessible
through tenant APIs until an operator verifies and assigns their ownership. Do not
derive ownership automatically from `owner_ref`, which was previously unrestricted.
Account reassignment after ownership is set is rejected by the database.

Migration 009 scopes retrieval documents. Shared policy documents remain readable;
unassigned historical transaction/exception embeddings are excluded. Reindex
operational history after verified account ownership is assigned. Avoid replaying
old financial client keys during migration: the new core retry namespace is scoped
by merchant and operation, while legacy keys used a different namespace.

The gateway restricts accounts, journal history, settlement status and reconciliation
exceptions to the merchant. The Rust RPC boundary independently checks every account
before posting, balances and reversals. RPC transport requires the private service
token. Retry cache keys include merchant, HTTP method, path and client key, and lock
release compares the owning token. Settlement exports contain only fully owned
transactions and aggregate debit totals instead of duplicating debit/credit lines.

## Test commands

```text
npm ci
npx playwright install chromium
npm run test:e2e
npm run test:e2e:ui
npm run test:e2e:report
```

Eight browser tests cover desktop/mobile demo posting, safe rendering, live API
envelopes, retry preservation, configuration and copilot recovery. Live browser
responses are controlled HTTP fixtures; this suite alone is not a complete ledger
deployment test. HTML reports, screenshots and traces are uploaded by CI.

From `gateway`, run `python -m pytest`. Set `TEST_DATABASE_URL` to a dedicated
PostgreSQL database ending in `_test` to include real tenant and settlement-export
queries. From `rag`, run `python -m pytest`; its database test needs pgvector.
Both CI jobs provide their required database rather than silently skipping it.

From `core`, run `cargo test` and `cargo clippy --all-targets -- -D warnings` with
`SQLX_OFFLINE=true` and a disposable PostgreSQL/pgvector test service. Protobuf is
bundled by the build dependency. The focused `rpc_ownership_and_retry_scopes` test
also runs on ordinary PostgreSQL and checks foreign accounts, retry collisions,
foreign reversals, and reversal balance restoration.

Local verification: eight browser tests, gateway tests including real PostgreSQL,
RAG unit/authentication tests, the focused real PostgreSQL RPC test, Python lint,
and Rust all-target Clippy. [CI at 7c9b6f5 passed](https://github.com/GodWar9/Finacle/actions/runs/38942470021),
including full Rust/pgvector tests, Linux C++ reconciliation tests, browser tests,
and the real gateway/core/PostgreSQL/Redis/Kafka container acceptance gate. The
latter verified durable retry replay after cache loss and restart, ledger outage
readiness, reversal replay, journal balance and Kafka recovery. The Rust service
now handles Docker SIGTERM gracefully. Provider-backed RAG, end-to-end settlement
files, hosted TLS, backups and target-environment recovery remain separate gates.

## Hosting boundary

The Compose file is a development topology with development database credentials.
For deployment, use HTTPS, private service networking, durable PostgreSQL/Redis,
private generated secrets, backups and ingress rate limits. The gateway portal
rate limiter is process-local; distributed ingress protection is needed across
replicas. Do not expose the ledger, RAG, database or broker directly to the Internet.
No hosted instance, financial production certification, model answer quality, or
paid infrastructure has been claimed or provisioned by this checkpoint.

The existing Prometheus scrape endpoint remains available without tenant credentials.
Keep /metrics on private ingress alongside the monitoring stack.

## Container acceptance and readiness

Run `python scripts/stack_smoke.py` with Docker Engine and Compose available.
`python scripts/stack_smoke.py --check` only validates configuration; it does not
prove runtime behavior. The acceptance run builds the real gateway/core images
and starts isolated PostgreSQL/pgvector, Redis and Kafka/ZooKeeper services. It
uses generated temporary credentials, a random loopback gateway port, and a unique
Compose project; it never accepts an existing deployment URL. Its own containers
and database volume are removed on completion. Allow time and disk space for a
Rust release build and container images.

Checks cover authenticated readiness and migrations, tenant account/transaction
isolation, balanced posting, duplicate and conflicting retry keys, database/cache
restart recovery, readiness during ledger outage, reversal replay, and delivery of
the committed reversal event after Kafka returns. Results and redacted container
logs are written under `test-results/stack/` and retained by CI for seven days.
RAG provider quality, settlement files and the browser portal remain separate
checks; this gate does not provision hosting or contact a model provider.

`/health` remains a lightweight liveness check. `/health/ready` now checks migrated
account schema, Redis and an authenticated read-only ledger RPC concurrently with
bounded timeouts. A missing service or invalid service token returns 503 with only
public status labels; connection strings and exception details are not exposed.

## Settlement worker concurrency (2026-10-20)

Workers now atomically claim the oldest uploaded batch with FOR UPDATE SKIP LOCKED
and mark it PROCESSING. The reconciliation subprocess runs in a thread so its
wait does not block the async event loop.

Validation: all 57 gateway tests passed against a dedicated local PostgreSQL
database, including simultaneous worker claims, terminal failure state, and
event-loop responsiveness. Gateway lint and changed-file formatting passed.
This does not establish exactly-once reconciliation or Kafka delivery across
worker crashes; full settlement-file container acceptance remains separate.
