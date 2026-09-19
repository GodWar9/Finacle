# Finacle — Next Steps (ordered task list)

> Working plan derived from `docs/PROGRESS.md` + `docs/SCOPE.md`. ~2 weekends, ~18-24h.
> Every item ends with a *verification* line so progress is provable, not claimed.
> Status legend: **[DONE]** = code landed + static checks green · **[DB]** = needs a live Postgres · **[STACK]** = needs Docker/live stack. Update PROGRESS.md as items flip.

---

## Phase 1 — Make CI tell the truth (Weekend 1)

### 1.1 Fix clippy failure — **[DONE]**
- Removed the unused `use tracing::error` import (`core/src/ledger/service.rs`).
- **Verify:** `cd core && cargo clippy -- -D warnings` exits 0 — ✅ verified locally 2026-09-19 (only remaining output: sqlx-postgres 0.7.4 future-incompat warning).

### 1.2 Make rust-core DB tests actually run in CI — **[DONE]** code, **[DB]** execution
- Added a `postgres:16` service to the `rust-core` job + `env: DATABASE_URL` (`.github/workflows/ci.yml`); compile stays offline (`SQLX_OFFLINE=true`, `.sqlx` cache as source of truth — new test SQL deliberately uses runtime `sqlx::query*` so the cache never needs regenerating).
- **Verify:** `cargo test` = 14 (4 unit + 10 DB-backed) green in Actions. ⛔ 10 `#[sqlx::test]` still cannot run locally (no reachable PG in this sandbox).

### 1.3 Add a rag pytest job to CI — **[DONE]**
- Added `rag-tests` job (`pip install -e ".[dev]" && pytest -v`, working-directory rag). Nightly eval job lives in `rag-eval.yml` (see 2.5).
- **Verify:** Actions rag job green (7 tests pass locally).

### 1.4 Add WORM enforcement + immutability test — **[DONE]** code, **[DB]** execution
- New `db/migrations/007_worm_enforcement.sql`: `BEFORE UPDATE/DELETE` trigger on `ledger_entries` + `BEFORE DELETE` on `transactions` (triggers, not ACLs — superusers bypass REVOKEs; TRUNCATE deliberately allowed for `sqlx::test` cleanup; transactions UPDATE allowed for the reversal status flip).
- New Rust test `test_ledger_entries_are_append_only` (`service.rs:647-672`).
- **Verify:** test green in CI; manual `psql DELETE FROM ledger_entries` fails.

### 1.5 Close correctness test holes — **[DONE]** code, **[DB]** execution
- `test_concurrent_same_key` (`service.rs:675-721`): 10 callers with one key → exactly 1 row, all successful callers agree on the txn id, balance exact.
- `test_crash_replay_no_double_post` (`service.rs:724-781`): phantom header+entries tx rolled back, same-key retry commits exactly once.
- **Verify:** both green in CI; no balance drift.

### 1.6 Decide and land the `reversal_of` semantics — **[DONE]** code, **[DB]** execution
- Contract fixed: the **original** row carries `reversal_of` = the reversing tx id; the reversal row is a normal POSTED tx (`service.rs:262-268`). Idempotency lookup moved before the already-reversed gate so a same-key retry replays the same reversal instead of erroring.
- Tests: `test_reversal_round_trip` (nets to zero, double reversal refused, flags correct) + `test_reversal_replay` (same key → same txn id).
- **Verify:** both green in CI.

---

## Phase 2 — Prove it runs (Weekend 2)

### 2.1 Repair `demo.py` — **[DONE]** code, **[STACK]** execution
- Rewritten: `X-API-Key` on every call; step 0 seeds the two accounts via `POST /api/v1/accounts`; step 5 exact same-key/payload replay; step 6 mid-transaction failure (ghost account) → same-key retry → replay proof; step 7 settlement upload (1 real-ref wrong amount + 2 ghost refs) → step 8 assert recon exceptions; step 9 reversal + balance; steps 10-11 RAG (tolerates missing key).
- **Verify:** on a fresh `docker compose up`, `python demo.py` walks all steps, zero `✘`, exceptions non-empty.

### 2.2 One full stack run + transcript — **[STACK]**
- `docker compose up -d --build` (core stack), then `--profile batch up -d --build`.
- Walk: accounts -> transfer -> replay -> reverse -> settlement upload -> status -> recon exceptions -> RAG ask.
- **Verify:** every beat has recorded output; save transcript + Grafana/Prometheus snapshot into `docs/PROGRESS.md`.

### 2.3 Capture the first performance numbers (DoD-2) — **[STACK]**
- **Gateway load:** `load_test/load_test.py --requests 1000 --concurrency 50 --account1 <id> --account2 <id> --api-key sk_test_demo:demo-merchant` -> record req/s, p50/p95/p99, failures.
- **Recon:** `recon/benchmark_recon.py --rows 1000000` (needs the recon binary built + reachable DB) -> record rows/s, elapsed, machine spec.
- **Verify:** numbers + hardware + method committed in PROGRESS.md §perf; mark DoD-2 MET with honest (likely modest) numbers.

### 2.4 Settlement processor watchdog (S3) — **[DONE]** code
- `settlement_processor.py` now requeues `PROCESSING` rows older than `REQUEUE_STALE_AFTER_SECONDS = 360` (engine timeout is 300s) back to `UPLOADED` at the top of `process_pending`.
- **Verify:** unittest suite green (38 passed); live test: manually set a row to `PROCESSING` with old `started_at`, confirm reclamation next poll.

### 2.5 RAG eval wired + zero-key degradation — **[DONE]** code, **[STACK]** execution
- New `.github/workflows/rag-eval.yml`: nightly `30 3 * * *` + dispatch; pgvector/pgvector:pg16 service; applies `db/migrations/*.sql`; **skips cleanly when `OPENAI_API_KEY` secret is absent**; runs `python eval/evaluate.py --min-pass-rate 60` (script exits non-zero below the threshold).
- **Verify:** job appears in Actions; local run with a real key prints pass/total.

### 2.6 Hygiene (buffer) — partially done
- `load_test.py` `--reuse-keys` fix + `aiohttp` in `gateway/pyproject.toml` dev extras — **[DONE]**.
- Rename/shield the shared `app` package across gateway and rag (single-venv collision) — **open**.
- Add `TIMING_MISMATCH` recon exception (S4) if you want the DoD word "timing" literally covered — **open**.

---

## Definition of done for `docs/PROGRESS.md` after this plan
- CI: 5 jobs green (gateway, rust-core, recon, lint, rag) **plus** nightly rag-eval (skips w/o key).
- `cargo test` = 14 DB-backed tests green, in CI.
- WORM + concurrent-same-key + crash/replay + reversal tests exist and pass **in CI**.
- `docs/PROGRESS.md`: verified numbers for load + 1M-row recon, with hardware/method.
- DoD table: no item below 80% except where explicitly recorded as CUT.

Numbers likely to be "modest" on a dev machine — record honestly (that is the DoD requirement).

### 2.4 Settlement processor watchdog (S3)
- In `settlement_processor.py:40-81`: before picking `UPLOADED`, requeue rows in `PROCESSING` older than N × poll interval (set `status='UPLOADED'` with a `requeue` log), so a crashed worker self-heals.
- **Verify:** simulate stuck `PROCESSING` row (manual UPDATE), processor reclaims within one interval.

### 2.5 RAG eval wired + zero-key degradation
- CI: scheduled job runs `python rag/eval/evaluate.py` with `OPENAI_API_KEY` secret; failing < threshold marks the job red.
- Local: run eval with the key and record pass-rate in PROGRESS.md.
- **Verify:** job appears in Actions; local run prints pass/total.

### 2.6 Hygiene (buffer) — do only if time remains
- Rename/shield the shared `app` package across gateway and rag (single-venv collision).
- Relax `load_test.py` `--reuse-keys` bug + add `aiohttp` to pyproject.
- Add `TIMING_MISMATCH` recon exception (S4) if you want the DoD word "timing" literally covered.

---

## Definition of done for `docs/PROGRESS.md` after this plan
- CI: 5 jobs green (gateway, rust-core, recon, lint, rag).
- `cargo test` = 9+ DB-backed tests green, in CI.
- WORM + concurrent-same-key + crash/replay tests exist and pass.
- `docs/PROGRESS.md`: verified numbers for load + 1M-row recon, with hardware/method.
- DoD table: no item below 80% except where explicitly recorded as CUT.