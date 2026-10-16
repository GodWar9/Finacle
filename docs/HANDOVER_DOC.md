# Finacle — Handover Document

> Handover/onboarding doc for whoever takes over this repository next. Covers
> how to get productive fast, load the code, run it, ship changes, and what to
> watch out for. Companion to `docs/PROJECT_EXPLANATION.md` (the "how it works"
> reference) and the design docs `docs/00..07`.

---

## 1. Handover at a glance

| Item | Detail |
|---|---|
| **What it is** | Python/FastAPI + Rust + C++ polyglot double-entry core-banking ledger with idempotency, Kafka eventing, C++ batch reconciliation, and a RAG compliance copilot |
| **Primary language split** | Rust = money-movement core (`core/`), C++ = recon engine (`recon/`), Python = gateway, settlement worker, RAG (`gateway/`, `rag/`) |
| **Data stores** | PostgreSQL 16 + pgvector (source of truth), Redis 7 (idempotency/rate-limit), Kafka (event bus) |
| **CI** | GitHub Actions (`.github/workflows/ci.yml`): 4 jobs — gateway-tests, rust-core, recon-engine, lint |
| **Current default branch** | `main`, push + PR triggers |
| **Demo portal** | `frontend/` (vanilla JS), served by the gateway at `/`, **mock-data mode** by default (`USE_MOCK_DATA = true`) |

---

## 2. Repository layout

```
Finacle/
├── .github/workflows/ci.yml   CI: gateway tests, Rust core, C++ recon, ruff lint
├── core/                      Rust ledger core (tonic gRPC, sqlx, SERIALIZABLE)
│   ├── proto/ledger.proto     frozen gRPC contract (shared with gateway)
│   ├── src/{main,lib}.rs + grpc_server/ + ledger/{domain,service,idempotency,outbox,poller,auditor}.rs
│   └── .sqlx/                 offline compile-checked query cache (regenerate with cargo sqlx prepare)
├── db/migrations/             001..006 SQL schema (owned & applied by ledger-core at startup)
├── gateway/                   FastAPI gateway (:8000) + settlement processor + tests
├── rag/                       RAG copilot (:8001) + Kafka indexer + policies + eval + tests
├── recon/                     C++ recon engine (libpqxx, OpenMP) + fixtures + benchmarks + tests
├── settlement-processor/      Docker image for the batch settlement worker
├── frontend/                  static ops console (mock-data-first)
├── monitoring/                prometheus.yml, alerting_rules.yml, Grafana dashboard
├── load_test/                 concurrent posting harness
├── docs/                      00..07 design docs, BUILD_CHECKLIST, DESIGN_DECISIONS,
│                              PROJECT_EXPLANATION.md, HANDOVER_DOC.md (this file)
├── demo.py / generate_data.py local demo / live-data generators
├── docker-compose.yml         full stack + batch profile
└── .env.example               env template (copy to .env)
```

> **Note:** a nested `Finacle/` directory is a stale uncopied snapshot and is
> git-ignored (`.gitignore` line `33`). Ignore it; do not edit it.

---

## 3. Environment setup (first day)

1. **Clone + env:**
   ```bash
   git clone <repo> && cd Finacle
   cp .env.example .env
   ```
   In `.env`, at minimum set `API_KEYS=sk_test_demo:demo-merchant` (or your own
   `key:merchant_id` pairs) and, to activate the RAG stack, `OPENAI_API_KEY`.

2. **Toolchain required:**
   - Docker + `docker compose` (v2)
   - Python 3.11+ (gateway & rag) — or just rely on the containers
   - Rust stable (core) + `protoc` (point `PROTOC` at a binary if needed) + `cargo install sqlx-cli --no-default-features --features postgres`
   - CMake + `libpqxx` (recon) for local C++ builds/testing
   - `pip install ruff` for the Python lint/format the CI enforces

3. **Heads-up on paths:** `sqlx::migrate!("../db/migrations")` embeds a relative
   path — the core Dockerfile builds from the repo root so this resolves. When
   building Rust locally, build from `core/` so `../db/migrations` is correct.

---

## 4. Running

### Full stack
```bash
docker compose up -d --build                # postgres, redis, kafka, zookeeper, ledger-core, gateway, rag-copilot, rag-indexer
docker compose --profile batch up -d --build # settlement-processor (+ you can run recon-engine on demand)
```
First build is heavy (Rust core, C++ recon, three Python images) — allow minutes.

### Verify
```bash
curl http://localhost:8000/health    # {"status":"healthy","service":"gateway"}
curl http://localhost:8001/health    # {"status":"healthy","service":"rag-copilot"}
curl http://localhost:8000/          # demo console (mock data)
```

### Quick sanity: post a transaction
```bash
curl -X POST http://localhost:8000/api/v1/accounts \
  -H "X-API-Key: sk_test_demo" -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"account_number":"ACC-1","account_type":"ASSET","owner_ref":"demo","currency":"INR"}'
# create a LIABILITY account, then:
curl -X POST http://localhost:8000/api/v1/transactions \
  -H "X-API-Key: sk_test_demo" -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"transaction_type":"PAYMENT","entries":[
    {"account_id":"<id>","direction":"DEBIT","amount_minor":10000,"currency":"INR"},
    {"account_id":"<id>","direction":"CREDIT","amount_minor":10000,"currency":"INR"}]}'
```

---

## 5. Day-to-day workflows

### Shipping a Python change (gateway/rag)
The CI lint job runs `ruff check gateway/ rag/` and `ruff format --check gateway/ rag/`
from the repo root. Keep both green:

```bash
ruff format gateway/ rag/
ruff check gateway/ rag/
ruff format --check gateway/ rag/
```

- `gateway/pyproject.toml` sets `line-length = 120`; `rag/` inherits ruff defaults (88).
  That asymmetry is intentional — don't "fix" it by copying the gateway config.
- Run the Python tests per package: `cd gateway && pip install -e ".[dev]" && pytest`;
  `cd rag && pip install -e ".[dev]" && pytest`. The gateway suite uses mocks
  (fakeredis/AsyncMock) so it runs offline.

### Shipping a Rust change (core)
```bash
cd core
cargo fmt --check        # CI enforces
cargo clippy -- -D warnings
# if you touched any SQL query:
sqlx database create && sqlx migrate run --source ../db/migrations
cargo sqlx prepare -- --all-targets   # refreshes core/.sqlx cache — commit it
cargo test
```
CI installs `protoc` via apt; locally ensure `protoc` is on PATH (`PROTOC` env var if
not). Accredited `.sqlx` cache: the poller intentionally uses runtime-checked queries
behind the `kafka` feature so the committed cache stays feature-agnostic
(`core/src/ledger/poller.rs:54-57`).

### Shipping a C++ change (recon)
```bash
cd recon
cmake -B build -DCMAKE_BUILD_TYPE=Release -DENABLE_OPENMP=ON -DBUILD_TESTS=ON
cmake --build build
./build/recon_tests
```
CI builds with `-DENABLE_OPENMP=ON` and runs `recon_tests`. Keep `cmake --clean-first`
handy if the build cache acts up.

### Committing
- Inspect `git status` / `git diff` before committing; stage only intended files.
- Match the repo's conventional-style messages, e.g. `fix:`, `feat:`, `ci:`, `docs:`,
  `ops:`, `refactor:`. Recent history is a good template (`git log --oneline`).
- Never commit `.env` (git-ignored), secrets, or updated `*_pb2*` stubs unless intentional.

---

## 6. What the demo portal does (and doesn't)

- `frontend/` is static and **mock-data-first**: `USE_MOCK_DATA = true`
  (`frontend/js/api.js:17`). It renders fixture data from `data/mock-data.js`, so the
  console works with no backend running.
- To point it at the real stack, flip `USE_MOCK_DATA = false`, set `API_KEY`, and the
  existing fetch paths hit gateway `:8000` / rag `:8001`.
- All network access is funneled through `api.js`; view renderers
  (`js/render-*.js`) never fetch directly — keep it that way.
- The frontend is copied into the gateway image as `gateway/static`
  (`gateway/Dockerfile:22`) and mounted at `/` by `gateway/app/main.py:94-96`. Locally
  `gateway/static` doesn't exist, so the portal only appears in containers.

---

## 7. Known gotchas & decisions to respect

1. **Rust `reverse_transaction` "already reversed"** — reversing an already-reversed
   tx returns a 409-ish Internal error rather than a reversal of the reversal
   (`core/src/ledger/service.rs:178-182`). By design; don't "fix" unless changing the product contract.
2. **Missing `OPENAI_API_KEY`** degrades gracefully but the RAG indexer **skips**
   indexing by design (zero vectors would pollute search) (`rag/indexer/kafka_consumer.py:50-59`).
3. **Gateway middleware resolve clients lazily** from `app.main` globals at request
   time — they're registered before lifespan creates the pool/Redis. Tests rely on this
   (`gateway/tests/test_integration.py` regression test).
4. **Idempotency-Key is mandatory on every POST** — including multipart uploads
   (`gateway/app/idempotency.py:55-59`). A missing header = `400`.
5. **Webhooks** sign the *raw* body with HMAC-SHA256 (`webhook_secret`); signature is
   verified before JSON parsing (`gateway/app/routes/webhooks.py`). Default secret is
   dev-only — change in production.
6. **Line endings**: git may warn about CRLF on Windows checkouts; harmless, but avoid
   reformatting whole files for EOL reasons in commits.
7. **Outbox → Kafka is at-least-once**: consumers (RAG indexer) must tolerate replays.
   settlement exceptions are indexed by `exception_id`/`source_ref`, so re-indexing is
   idempotent at the DB (get-doc-by-ref guard only applies to policy seeding).
8. **Do not hand-write ruff formatting fixes** for just a few files; run the formatter
   across both directories so CI's `--check` stays satisfied.

---

## 8. Debugging / troubleshooting quick refs

| Symptom | Look here |
|---|---|
| Gateway 502 "ledger core error" | Is `ledger-core` up on `:50051`? `docker compose logs ledger-core`; check `LEDGER_CORE_ADDR` |
| `cargo test` fails on SQL | `DATABASE_URL` unreachable, or `.sqlx` cache stale → run `cargo sqlx prepare` |
| No RAG answers / "not configured" | `OPENAI_API_KEY` empty in the `rag-copilot`/`rag-indexer` env |
| Settlement stuck `PROCESSING` | Settlement processor crashed mid-batch; the status stays PROCESSING (no watchdog) — reset the row in DB or re-upload with a new batch_id |
| Reconciliation exceptions not in Copilot | Indexer down, or no API key (indexing skipped); check `docker compose logs rag-indexer` |
| Idempotent replay returns 409 | Key reused with a *different* body (client bug) or two in-flight requests — check the lock (`lock:idem:...`) TTL |
| Portal shows stale/no data | `USE_MOCK_DATA` still true; static files not refreshed in the image → rebuild gateway |
| Kafka events missing | `ledger-core` built **without** the `kafka` feature (outbox poller disabled); check the core image build flags |
| Lint job red after merge | Run `ruff format gateway/ rag/ && ruff check gateway/ rag/` and commit the result |

---

## 9. Learning path (recommended reading order)

1. `docs/PROJECT_EXPLANATION.md` — end-to-end mental model of the whole system.
2. `docs/06_CONSISTENCY_CONCURRENCY_MODEL.md` — *why* idempotency/outbox/reconciliation
   work the way they do (read this before touching middleware or the core service).
3. `docs/05_DATABASE_AND_EVENT_SCHEMA.md` — the frozen contract every component codes against.
4. `docs/01_LEDGER_CORE_ENGINE_RUST.md` → `docs/03_API_GATEWAY_AND_IDEMPOTENCY_PYTHON.md`
   → `docs/02_RECONCILIATION_ENGINE_CPP.md` → `docs/04_RAG_COMPLIANCE_COPILOT.md` in
   dependency order.
5. `docs/07_DEPLOYMENT_AND_INFRA.md` + `docs/BUILD_CHECKLIST.md` when touching infra.

---

## 10. Onboarding checklist

- [ ] Read `docs/PROJECT_EXPLANATION.md` and `docs/06_CONSISTENCY_CONCURRENCY_MODEL.md`.
- [ ] `cp .env.example .env`; set `API_KEYS`; optionally `OPENAI_API_KEY`.
- [ ] `docker compose up -d --build`; verify `/health` on `:8000` / `:8001`.
- [ ] Post an account + transaction through curl (section 4), then re-send the same
      `Idempotency-Key` to see the replay return the same body.
- [ ] Run the settlement round-trip (fixture script → upload → status → exceptions).
- [ ] Confirm CI expectations locally: `ruff check`/`format --check`, `pytest` in
      gateway & rag, `cargo fmt --check`/`clippy`, `cmake --build` + `recon_tests`.
- [ ] Skim `gateway/tests/` and `rag/tests/` to learn the mock patterns used.
- [ ] Review `.env.example` keys vs `docker-compose.yml` service environments.

---

## 11. Open items / where to pick up next

- The settlement processor has no crash-recovery watchdog for rows left in
  `PROCESSING`; a future improvement is a stale-batch reclaim.
- `frontend` still ships in mock-data mode — wiring it to live backends end-to-end is a
  natural next milestone.
- Eval harness exists (`rag/eval/evaluate.py`) but no CI job runs it; wiring it in as a
  scheduled job is a candidate task.
- Outbox consumers assume at-least-once delivery; adding idempotent consumer IDs/dedup
  keys would harden the RAG indexer against replays.
- The core's `kafka` feature must be enabled at build time for eventing to work — worth
  an explicit CI assertion or docs note when someone changes the Dockerfile.