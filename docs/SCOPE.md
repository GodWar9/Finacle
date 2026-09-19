# Finacle — Scope Judgment for v1.0 (2-weekend push)

> Decision sheet: classify everything standing between today and "v1.0 DoD met".
> Time frame: **2 weekends ≈ 18–24 focused hours**. Hours are honest estimates for
> a single developer who knows this codebase (the author). Each MUST is tied to a DoD item.

---

## MUST — blocks v1.0 (do first, in this order)

| Task | DoD | Est. | Why / evidence | Status |
|---|---|---|---|---|
| **M1. Fix CI green (rust-core + rag job)** — (a) drop unused `use tracing::error` in `service.rs:8`; (b) add a Postgres service + `DATABASE_URL` to the rust-core job so the 5 `#[sqlx::test]` tests actually run (they are the only DB-backed correctness tests you own); (c) add `pytest` step for `rag/` to the workflow (or a new job). Re-run `cargo clippy -- -D warnings` and `cargo test` locally first. | 6 | 1.5-2.5h | Reproduced: clippy exit 101 on main; `cargo test` 5/5 DB tests fail with "DATABASE_URL must be set" exactly as CI would. | **[DONE]** code (clippy ✅, postgres service + `DATABASE_URL` in ci.yml, SQLX_OFFLINE compile path kept, rag-tests job added). Execution still needs a first Actions run. |
| **M2. One local green verification pass, recorded** — run `cargo test`, `pytest` (gateway+rag), `ruff`, `cmake … recon_tests` on Linux (WSL/Docker/CI) and paste results into `docs/PROGRESS.md`. | 1,6 | 1-2h | Local Windows recon link is blocked by env (gmtime_r + libpq.a); use the Ubuntu path once. | **[STACK]** pytest (38+7 ✅), ruff ✅, clippy ✅, offline compile ✅ recorded in PROGRESS §2/§8. `cargo test` DB suites + recon_tests still need a Linux/CI run. |
| **M3. Repair `demo.py`** — add `X-API-Key` to every call, and create the two accounts once (mirror `generate_data.py._seed_accounts`) instead of fresh random UUIDs; optionally script the mid-transaction-failure beat (post then `kill -9` the gateway/core once, retry same key). | 3 | 1-1.5h | demo.py:24 sends only `X-Merchant-ID`; demo posts to nonexistent accounts (401/404/500 on fresh stack). | **[DONE]** rewrite adds auth + seeded accounts + mid-txn-failure beat + replay + reversal + recon-exception vote; E2E blocked by no Docker. |
| **M4. Run the stack once and capture the first performance numbers** — `docker compose up`, then `load_test/load_test.py` (record req/s + p99 + machine spec) and `recon/benchmark_recon.py` 1M rows (rows/s), save results in `docs/PROGRESS.md` §perf. | 2 | 2-3h | DoD-2 currently 0% evidenced. Numbers may be modest on a dev laptop — that's fine, they're honest numbers with method. | **OPEN** — still no measured numbers (needs Docker/stack). |
| **M5. Reconcile-vs-DoD demo scenario** — run demo transfer → replay → reversal → settlement upload → recon run → query exceptions, screenshot/curl-transcript each beat into PROGRESS.md. | 3 | 1h | The only thing making DoD-3 credible is a recorded run. | **OPEN** — scripted in demo.py, not yet executed. |
| **M6. Enforce WORM at DB + add immutability test** — uncomment/do properly: dedicated role with `REVOKE UPDATE, DELETE ON ledger_entries …` (or RLS `FORCE ROW LEVEL SECURITY`), + a Rust test asserting `UPDATE` / `DELETE` on an entry fails. | 1 | 1-2h | `001_initial_schema.sql:74-75` explicitly defers this; README claims append-only. | **[DONE]** chose triggers over ACLs (superusers bypass REVOKEs): `db/migrations/007_worm_enforcement.sql`; test `test_ledger_entries_are_append_only`. Not yet run against a DB. |
| **M7. Close the two correctness-test holes (cheap)** — (a) concurrent *same-key* idempotency test (hammer one key from 10 tasks; assert exactly one commit + all callers get a replay/conflict, never a double-post); (b) crash/replay test = post inside an unfinished tx, then re-run same key and assert single row (or document at-least-once outbox replay). | 1 | 1-2h | The gaps a fintech interviewer will probe first. | **[DONE]** `test_concurrent_same_key` + `test_crash_replay_no_double_post` added; unrun (need PG). |

**MUST subtotal: ≈ 9–14h** (fit in weekend one).

---

## SHOULD — strengthen v1.0, do if time remains (weekend two)

| Task | DoD | Est. | Note | Status |
|---|---|---|---|---|
| **S1. Wire RAG eval into CI** as a scheduled workflow running `rag/eval/evaluate.py` (needs `OPENAI_API_KEY` secret) and failing on low pass-rate. | 5 | 0.5-1h | HANDOVER §11 already lists this as an open item. | **[DONE]** `rag-eval.yml` nightly + dispatch; skips cleanly without the secret; gate = `--min-pass-rate 60` inside evaluate.py. |
| **S2. Rename shared top-level `app` package** (`gateway.app` / `rag_app`… ) or document the single-venv collision with a two-line gotcha section. | (hygiene) | 1h | Reproduced collision when installing both in one venv. | **OPEN** |
| **S3. Settlement-processor stale-batch watchdog** — reclaim rows stuck in `PROCESSING` (age > N × poll interval) back to `UPLOADED` or `FAILED`. | 3 | 1-2h | HANDOVER §11 + troubleshooting table call this out. | **[DONE]** `requeue_stale_processing()` (360s > engine 300s timeout) at top of `process_pending`. |
| **S4. `TIMING_MISMATCH` / settlement-date exception** in recon (bank expected-settle-date vs ledger `created_at`), with a matcher test. | 1 | 2-3h | DoD mentions timing; current enums cover amount/status/duplicate/missing only. | **OPEN** |
| **S5. Add one live gateway integration test** against real PG+Redis (or at least one gRPC→core test outside sqlx macros). | 1 | 1.5-2h | All current gateway "integration" tests are mocks. | **OPEN** |
| **S6. Fix small load-test anomalies**: declare `aiohttp` dep; make `--reuse-keys` real; relax "all 10 must succeed" or make it a distinct keyed test. | 2 | 0.5-1h | `load_test.py:68` and `:151`. | **[DONE]** `aiohttp` in dev extras; `--reuse-keys` now real; `--api-key` threaded. |

**SHOULD subtotal: ≈ 7–10h.**

---

## LATER — explicitly out of the 2-weekend window (post-v1.0)

- **L1. Idempotent Kafka consumers / dedup keys** in RAG indexer (at-least-once → at-most-once) — HANDOVER open item. 1-2d.
- **L2. Frontend live-data mode** end-to-end (`USE_MOCK_DATA=false` path, `frontend/js/api.js:17`). 1-2d.
- **L3. Recon COPY-based writes** (`pqxx::stream_to`) once scale requires it — DESIGN_DECISIONS already flags this trade-off. 0.5-1d.
- **L4. Fee/tax sub-engine** that posts its own balanced entries inside the same transaction. 2-4d.
- **L5. Webhook delivery retry/DLQ service** on top of the existing HMAC-signed endpoint. 1-2d.
- **L6. `reversal_of` semantics audit** — confirm intended direction (original→reversal, not self) and add a reversal test if it's inverted (also worth a one-liner either way *now*, folded into M7). 0.5d.

---

## CUT — do not build (for v1.0 or v2 as scoped)

- **C1. Multi-currency conversion engine.** Ledger is INR-only (`CHAR(3)` currency check); conversion/forex is a product decision, not an infrastructure gap.
- **C2. OAuth/JWT replacing X-API-Key.** Static per-merchant keys are fine for the demo; HMAC webhooks already cover server→server.
- **C3. Vertical sharding / tenant-scoped data model.** Single-writer Postgres is correct for v1; revisit only with a measured bottleneck.
- **C4. Real RBI/PSS compliance module.** Keep the RAG layer as the compliance surface (it's the interviewable part) rather than trying to encode regulations in code.
- **C5. Microservices further splitting** (the outbox/auditor are small enough to stay in one core binary).

---

## Suggested 2-weekend plan

- **Weekend 1:** M1 → M2 → M7 → M6 (CI + correctness). Target: *all CI jobs green, DB-backed Rust tests passing in CI, WORM enforced.* — **[DONE at code level 2026-09-19; the "first CI run" proof is the only thing left]**
- **Weekend 2:** M3 → M4 → M5 → S1/S3 (demo + numbers + RAG/ops hardening). Target: *demo transcript + measured numbers recorded in PROGRESS.md; eval wired.* — **[M3/S1/S3 done at code level; M4/M5 still need a real stack]**
- Buffer: S2, S6, S4 as fill. If forced to cut, drop S4 and S5 first (they don't gate "ship-DoD").

## What "v1.0 done" looks like after this plan

Every DoD item ≥ MET-except-2 with a recorded number, CI green on all four jobs, WORM + concurrent-same-key + crash/replay tests present, a real `demo.py` run transcript, and a `docs/PROGRESS.md` appendix with hardware + method + numbers.

**Remaining blockers after the 2026-09-19 push (all need an environment, not code):** push to GitHub and get the first Actions run (rubber-stamps M1/M2/M6/M7 + the 10 DB tests); run `docker compose up` + `demo.py` (M3/M5 + transcript); run `load_test` + `benchmark_recon` and record numbers (M4/DoD-2). Total remaining ≈ 5-8h of *execution*, not *building*.