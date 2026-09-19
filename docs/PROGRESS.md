# Finacle — Weekly Status / Progress Report

> Generated: 2026-09-19 (audit #1). Read-only audit; evidence-only claims.
> Legend: MET / PARTIAL / MISSING / UNVERIFIED. Everything below links to a command or file + line.

---

## 1. Inventory (what exists)

**Repository:** `GodWar9/Finacle`, branch `main`, HEAD `5736788` ("Format Python sources with Ruff", 2026-09-19). ~23 commits; most delivery on 2026-09-08..10.

| Component | Where | Language | Role |
|---|---|---|---|
| Ledger Core | `core/` | Rust (tonic, sqlx) | Posting / balance / reversal, SERIALIZABLE, outbox |
| API Gateway | `gateway/` | Python (FastAPI) | REST, auth, rate-limit, 2-tier idempotency, static portal |
| Settlement Processor | `gateway/app/settlement_processor.py` (`settlement-processor/` is only a Dockerfile) | Python | Polls `settlement_files`, runs C++ recon, publishes Kafka |
| Reconciliation Engine | `recon/` | C++17 (libpqxx, OpenMP, GTest) | Fixed-width match, exceptions + batch summary |
| RAG Copilot + Indexer | `rag/` (`app/`, `indexer/kafka_consumer.py`) | Python | Grounded Q&A; Kafka consumer → pgvector |
| Schema | `db/migrations/001..006` | SQL | Frozen contract, owned by core at startup |
| Infra-as-code | `docker-compose.yml`, `monitoring/` (prometheus.yml, alerting_rules.yml, Grafana dashboard) | — | Full stack + batch profile |
| Demo / load | `demo.py`, `generate_data.py`, `load_test/load_test.py`, `recon/benchmark_recon.py` | Python | Scripted scenarios, load + 1M-row benchmark |
| Tests | `core` (9 Rust tests), `recon/tests/` (3 suites), `gateway/tests/` (8 files), `rag/tests/` (2 files) | — | — |
| Docs | `docs/00..07`, `BUILD_CHECKLIST.md`, `DESIGN_DECISIONS.md`, `HANDOVER_DOC.md`, `PROJECT_EXPLANATION.md`, `README.md` | — | Architecture, decisions, handover |

---

## 2. Verification run this week (commands actually executed 2026-09-19)

Environment: Windows 11, PowerShell; `cargo`/`rustc` 1.96.0; g++ 15.2.0 (MSYS2/UCRT64); Python 3.14.4 (venv); cmake 4.4.3 (pip); ninja (pip); PostgreSQL 18 local service (5432) + throwaway instance (5433, temp); `protoc` 25.3 (temp download). Docker: CLI present, **daemon not installed/running** → compose cannot be exercised here.

| Check | Command | Result | Evidence |
|---|---|---|---|
| Rust fmt | `cargo fmt --check` (core) | ✅ PASS | exit 0 |
| Rust clippy | `cargo clippy -- -D warnings` (core, default features — same as CI) | ✅ PASS after fix | 2026-09-19 removed unused `use tracing::error` (`service.rs`); only remaining output is a future-incompat warning for sqlx-postgres 0.7.4 (not an error) |
| Rust test compile (offline) | `cargo test --no-run`, `SQLX_OFFLINE=true`, no `DATABASE_URL` | ✅ PASS | new test SQL uses runtime `sqlx::query*` so the checked-in offline cache stays the compile source of truth |
| Rust tests (no DB) | `cargo test` core, `DATABASE_URL` unset | ❌ 4 pass / 10 fail | 10 `#[sqlx::test]` tests panic: "DATABASE_URL must be set" (3 domain + 1 poller tests pass; **10 DB-backed tests incl. 5 new ones are unrun locally — they need a live PG**) |
| Rust tests (temp PG) | `cargo test` core, `DATABASE_URL=...:5433` | ⛔ not runnable | throwaway PG crashes with Windows `0xC0000142` on child spawn (environment issue, 2nd attempt 2026-09-19) |
| Gateway pytest | `pytest -q` (gateway, venv, editable install) | ✅ 38 passed | — |
| RAG pytest | `pytest -q` (rag, venv, editable install) | ✅ 7 passed | — |
| Ruff lint | `ruff check gateway/ rag/ demo.py load_test/load_test.py` | ✅ PASS | "All checks passed!" |
| Ruff format | `ruff format --check gateway/ rag/ demo.py load_test/load_test.py` | ✅ PASS | "40 files already formatted" |
| Python syntax | `python -m py_compile` demo/load_test/settlement_processor | ✅ PASS | exit 0 |
| Workflow YAML | `yaml.safe_load` on both workflows | ✅ PASS | both parse (ci.yml, rag-eval.yml) |
| Recon build | cmake configure+compile (Ninja, MSYS2 g++, OpenMP, FetchContent libpqxx 7.9.2, GTest 1.14) | ⚠️ objects compile; link blocked | `db_writer.cpp:12` uses POSIX `gmtime_r` (not exposed by MinGW `time.h`); EDB `libpq.a` static archive unresolved `pg_* / __security_cookie` symbols on MinGW. Ubuntu/CI path uses `libpqxx-dev` and works. |
| Recon tests | `./build/recon_tests` | ⛔ not runnable here | needs the above link; suite exists (`test_matcher.cpp` 8 tests). CI runs it on ubuntu. |
| Docker stack | `docker compose up` | ⛔ `docker info`: no daemon ("failed to connect ... dockerDesktopLinuxEngine") | — |

**Environment caveats:** local Postgres 18 service on 5432 uses `scram-sha-256` everywhere with no known password and no trust entry; throwaway instance crashes when PG spawns child backends (`0xC0000142`, known sandbox DLL-init issue). Neither the DB-backed Rust tests, gateway live tests, nor the recon DB writer could be exercised against a real Postgres here.

---

## 3. v1.0 DoD scorecard

| # | DoD item | Score | Evidence |
|---|---|---|---|
| 1 | Correctness tests (balanced, immutability, atomicity, idempotency incl. concurrent, crash/replay, recon mismatch, hot-account concurrency) | **PARTIAL (~70%)** | 2026-09-19 added: concurrent same-key race (10 callers, exactly-1 row + balance, `service.rs:675-721`), crash/replay (phantom rollback then same-key retry → one row, `service.rs:724-781`), WORM immutability (UPDATE/DELETE on `ledger_entries` rejected by new migration `007` trigger, `service.rs:647-672`), reversal round-trip + same-key replay (`service.rs:563-644`). Still unexecuted — all are `#[sqlx::test]` needing a live PG (CI now provides one). Recon: 8 matcher tests; **no timing/settlement-date exception**. WORM now DB-enforced by `007_worm_enforcement.sql` triggers (not ACLs — superusers bypass REVOKEs; TRUNCATE deliberately not blocked so `sqlx::test` cleanup works). |
| 2 | Measured performance (postings/s, p99, hardware+method) | **MISSING (~5%)** | No numbers anywhere (grep docs for `rows/s|req/s|p99|postings` → only aspirational prose in `docs/`; `DESIGN_DECISIONS.md:29,42` even describe trade-offs "2-4x / faster than X" as claims). Harnesses exist (`load_test/load_test.py`, `recon/benchmark_recon.py`) but were never run & recorded in-repo. `load_test.py` load-tool bugs fixed 2026-09-19 (`--api-key`, real `--reuse-keys`) but still not measured. |
| 3 | One-command demo (`docker compose up`) covering transfer, idempotent retry, mid-transaction failure, recon run | **PARTIAL (~60%)** | `demo.py` fully rewritten 2026-09-19: `X-API-Key` on every call, step 0 seeds two real accounts via `POST /api/v1/accounts`, step 5 exact same-key/payload replay, step 6 mid-transaction failure (ghost account) → same-key retry → replay proof, step 7 settlement upload with 1 real-ref wrong-amount row (AMOUNT_MISMATCH candidate) + 2 ghost refs (MISSING_IN_LEDGER), step 8 poll + assert recon exceptions non-empty, step 9 reversal + balance, steps 10-11 RAG (tolerates missing key). **E2E still unverified** (no Docker daemon here). `generate_data.py` seeds accounts correctly. |
| 4 | Design doc (diagram, decisions/tradeoffs, known limitations) | **MET (~95%)** | `docs/00..07` + `DESIGN_DECISIONS.md` (SERIALIZABLE vs locks, outbox, tolerance, exec_params vs COPY) + `HANDOVER_DOC.md` (explicit "open items" §11) + `BUILD_CHECKLIST.md`. README diagrams the architecture (README.md:15-40). |
| 5 | RAG: clear job + small eval, or labelled experimental | **PARTIAL (~65%)** | Eval harness with 15 grounded questions (`rag/eval/evaluate.py`), zero-vector guard when `OPENAI_API_KEY` empty (`indexer/kafka_consumer.py:50-59`), copilot answers "no answer" rather than guessing. 2026-09-19: eval script now gates on `--min-pass-rate` (exits non-zero below threshold) and a **nightly `rag-eval` job** (`rag-eval.yml`) runs it against pgvector with a clean skip when the `OPENAI_API_KEY` secret is absent. Still needs a real key + indexed docs to ever see a pass %. |
| 6 | CI builds/tests/lints all languages | **PARTIAL (~65%)** | Workflow fixed 2026-09-19: clippy blocker removed; `rust-core` job gains a `postgres:16` service + `DATABASE_URL` so the 10 `#[sqlx::test]` suites run; compile stays offline (`SQLX_OFFLINE=true`) with cache as source of truth; new `rag-tests` pytest job; `rag-eval` nightly job; demo/load_test/settlement_processor all kept ruff-clean. **None of this has been executed by Actions yet** (private repo, no runner evidence) — green is pending a first CI run. |
| 7 | README a stranger can follow in 10 min | **MET (~90%)** | README.md 277 lines: arch ASCII, quick start, curl for accounts→transfer→replay→reverse, settlement fixture→upload→status, RAG ask, dev/test commands, env notes. Minor: doesn't mention `generate_data.py` for seeding before `demo.py`. |

---

## 4. DoD % complete (equal-weight DoD)

| DoD | % |
|---|---|
| 1 Correctness tests | 70 |
| 2 Measured perf | 5 |
| 3 One-command demo | 60 |
| 4 Design doc | 95 |
| 5 RAG job/eval | 65 |
| 6 CI green | 65 |
| 7 README | 90 |

**Overall v1.0 DoD ≈ 64%** (by item; updated 2026-09-19 after follow-up fixes). Reading: **closer than you think on architecture, docs, and code quality; further than you think on ship-readiness** — the two structural blockers to a green CI and a runnable demo were removed (code-level), but the DB-backed Rust tests and the full stack have *still never executed anywhere*, and there are still zero measured performance numbers. Remaining gaps are evidence/finishing, not architecture.

---

## 5. Notable findings (positive)

- Defense-in-depth on balancing: app check + deferred DB constraint trigger + materialized-balance trigger + scheduled auditor (`auditor.rs`) that rederives balances from `ledger_entries`.
- Idempotency story is genuinely two-tier (Redis hot path + Postgres durable backstop with hash-conflict → 409), documented rationale (`docs/03`, `DESIGN_DECISIONS.md`).
- Outbox poller is crash-safe by construction (`FOR UPDATE SKIP LOCKED` + mark-published-after-send; at-least-once) — and its non-kafka stub is unit-tested (`poller.rs:130-142`).
- gRPC server maps errors cleanly (aborted→retry, already_exists→409, not_found→404) and the gateway retries `ABORTED` up to 3× (`transactions.py:48-71`).
- Queries are compile-time checked (sqlx offline cache `core/.sqlx/`, `SQLX_OFFLINE` in Dockerfile) — real quality marker.
- Python lint config asymmetry (gateway 120 col vs rag default 88) is deliberate and documented (HANDOVER §5).

## 6. Notable findings (risks / defects)

1. **CI green is unverified** — the red blockers were removed 2026-09-19 (clippy, missing Postgres service, no rag job), but no GitHub Actions run has ever confirmed the jobs actually pass. The rust-core postgres service + the 10 `#[sqlx::test]` suites are the highest-risk unverified combination.
2. **`demo.py` was fixed at code level but never executed end-to-end** — auth + account seeding + mid-txn-failure + replay + reversal + recon-exception beats are all scripted; a fresh `docker compose up` is the only proof left.
3. **No measured performance anywhere** — DoD-2 unmet.
4. **WORM** is now DB-enforced by `007_worm_enforcement.sql` triggers with an immutability test (`service.rs:647-672`) — but neither has run against a real DB; a superuser doing a plain `DELETE` on `ledger_entries` is the manual check that still needs performing.
5. **`reversal_of` contract inverted** (original now points at the reversing transaction, `service.rs:262-268`) — fixed code-level and covered by `test_reversal_round_trip`, needs a DB to run.
6. **Shared top-level package `app`** across `gateway/` and `rag/` collides when both installed into one venv (reproduced) — fine in separate containers, a footgun locally.
7. **Gateway "integration" tests are all mocks** (`test_integration.py` uses AsyncMock/fakeredis-style fixtures; no live PG/Redis/gRPC).
8. **`load_test/load_test.py`** bugs fixed 2026-09-19 (`--api-key` now threaded through headers, `--reuse-keys` actually reuses keys, `aiohttp` declared in dev extras) — still never run. `test_concurrent_same_account` (gateway) asserts all 10 succeed, which conflicts with the core test's realistic "some may abort" expectation.

---

## 7. What I could NOT verify (UNVERIFIED)

- Full `docker compose up` / one-command demo / settlement E2E (no Docker daemon in this environment).
- Rust DB-backed tests against a real Postgres (sandbox PG crash + no credentials for local service).
- recon_tests execution and recon→DB write path (link blocked on Windows MinGW; CI is the oracle).
- Live RAG answer quality (no `OPENAI_API_KEY`; eval needs OpenAI).
- Whether GitHub Actions has ever run green (repo is private; `gh` unavailable).

> Diff note for next audit: re-run the table in §2; compare against this file; any row that flips pass/fail is the delta. No previous PROGRESS.md existed.

---

## 8. Follow-up session 2026-09-19 (after user approval of the SCOPE/NEXT-STEPS plan)

All changes below are **code-verified** (`cargo fmt/clippy` clean, offline compile green, ruff+py_compile clean, gateway 38 / rag 7 pytest pass, both workflow files parse as YAML). The DB-backed items are **not yet executed** — they need a live Postgres, which this sandbox cannot host.

| # | Change | Where | Verification available |
|---|---|---|---|
| 1 | Clippy unused import removed | `core/src/ledger/service.rs` (`use tracing::info;`) | `cargo clippy -- -D warnings` exit 0 |
| 2 | `reverse_transaction` rewritten: one atomic SERIALIZABLE tx (header+entries+idempotency+outbox+original UPDATE), idempotency replay before the already-reversed gate, `reversal_of` = reversing txn id | `service.rs:200-280` | new tests RRT/REP (unrun, need DB) |
| 3 | WORM at DB layer: append-only triggers | `db/migrations/007_worm_enforcement.sql` (new) | SQL parses; test WORM (unrun) |
| 4 | New DB tests: reversal round-trip, reversal replay, WORM immutability, 10-way same-key race, crash/replay | `service.rs` tests | offline compile green; run needs PG |
| 5 | rust-core CI: Postgres service + `DATABASE_URL`; compile stays offline via `.sqlx` cache (new test SQL uses runtime `sqlx::query*` to stay cache-free) | `.github/workflows/ci.yml` | YAML_OK; no Actions run yet |
| 6 | New `rag-tests` pytest job + nightly `rag-eval` job (pgvector, migrations apply, clean skip w/o key, `--min-pass-rate 60` gate) | `ci.yml`, `.github/workflows/rag-eval.yml` (new) | YAML_OK; needs key+index to pass |
| 7 | `demo.py` rewritten (X-API-Key, seeds accounts, replay/retry/reversal/mismatch beats, RAG tolerant) | `demo.py` | ruff+py_compile clean; E2E blocked |
| 8 | Settlement watchdog: stale `PROCESSING` rows requeued after 360s (engine timeout 300s) | `gateway/app/settlement_processor.py` | lint clean; `test_settlement_processor` suite passes (38) |
| 9 | `load_test.py` fixes: `--api-key`, real `--reuse-keys`, `aiohttp` in dev extras | `load_test/load_test.py`, `gateway/pyproject.toml` | ruff+py_compile clean |
| 10 | RAG eval gate moved into script | `rag/eval/evaluate.py` (`--min-pass-rate`) | ruff clean; local run needs key |