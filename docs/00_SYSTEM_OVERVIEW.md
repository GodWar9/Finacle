# LEDGER-CORE — Double-Entry Core Banking Transaction Engine
## System Overview & Multi-Agent Build Map

**Project codename:** `ledger-core`
**Goal:** A production-shaped core-banking ledger — the same category of system that sits behind Razorpay/PayU/Stripe-style payment platforms and bank cores (Finacle, Temenos). Every rupee that moves is a balanced double-entry, every API call is safely retryable, and every settlement file is reconciled against the internal ledger with drift surfaced automatically.

This doc set is written so **4 agents (or 4 people) can build in parallel** with hard file-ownership boundaries and frozen interface contracts (gRPC/REST schemas, DB schema, event schema) defined up front. Nobody blocks on anybody else past Day 1.

---

## 1. Why this architecture (and not a toy CRUD app)

Real BFSI backends split along a very specific fault line:

- **The money-movement hot path** (posting a transaction, checking balance, enforcing double-entry invariants) needs to be **correct under concurrency** and **fast**. This is why real institutions don't write this in Python — they write it in C++ (legacy cores: Finacle, Flexcube) or increasingly Rust (modern fintechs: several EU neobanks, some of Stripe's internal systems). We use **Rust** here because it gives C++-level performance with compile-time elimination of an entire class of bugs (data races, use-after-free) that are unacceptable in a ledger.
- **Batch/file-shaped work** — parsing bank settlement files (fixed-width, CSV, sometimes literal COBOL-copybook-derived formats), running end-of-day reconciliation over millions of rows — is classic **C++** territory in real institutions because these jobs are throughput-bound, run on schedule (not request/response), and often need to interoperate with legacy mainframe file formats. We use C++ here deliberately so the project demonstrates that skill, not just Rust twice.
- **Everything else** — orchestration, the public API gateway, idempotency middleware, reporting, and the RAG compliance copilot — is **Python**, because this is genuinely how real fintech backend teams operate: a fast, safe core written in a systems language, wrapped and orchestrated by Python services that move fast and integrate with everything (LLM APIs, internal tools, dashboards).

This mirrors real org structure: a "Core Banking / Ledger Platform" team (Rust/C++) and a "Payments Platform / Product Engineering" team (Python) sitting on top of it.

---

## 2. High-level architecture

```
                         ┌─────────────────────────────────────────┐
                         │            CLIENT / MERCHANTS            │
                         └───────────────────┬───────────────────────┘
                                              │ HTTPS (Idempotency-Key header required)
                                              ▼
                         ┌─────────────────────────────────────────┐
                         │   API GATEWAY (Python / FastAPI)          │
                         │   - authn/authz                           │
                         │   - idempotency middleware (Redis)        │
                         │   - request validation (Pydantic)         │
                         └───────────────────┬───────────────────────┘
                                              │ gRPC
                                              ▼
                         ┌─────────────────────────────────────────┐
                         │   LEDGER CORE ENGINE (Rust)                │
                         │   - double-entry invariant enforcement     │
                         │   - account balance state machine          │
                         │   - Postgres as source of truth (ACID)     │
                         │   - emits domain events → Kafka            │
                         └───────┬───────────────────────┬─────────────┘
                                 │ writes                │ publishes
                                 ▼                        ▼
                    ┌─────────────────────┐    ┌───────────────────────┐
                    │ PostgreSQL           │    │ Kafka (event bus)      │
                    │ ledger_entries (WORM)│    │ ledger.transaction.*   │
                    │ accounts, txn_journal│    │ ledger.reconciliation.*│
                    └─────────────────────┘    └───────────┬───────────┘
                                                             │ consumes
                          ┌──────────────────────────────────┼───────────────────────┐
                          ▼                                  ▼                        ▼
           ┌───────────────────────────┐   ┌──────────────────────────┐  ┌────────────────────────┐
           │ RECONCILIATION ENGINE (C++)│   │ RAG COMPLIANCE COPILOT    │  │ REPORTING / PROJECTIONS │
           │ - ingests settlement files │   │ (Python)                  │  │ (Python, eventual        │
           │ - 3-way match: PG record / │   │ - embeds policy docs +    │  │  consistency read model)│
           │   bank statement / ledger  │   │   txn narratives          │  │                          │
           │ - drift/exception queue    │   │ - grounded Q&A over both  │  │                          │
           └───────────────────────────┘   └──────────────────────────┘  └────────────────────────┘
```

---

## 3. Service boundaries & ownership (for parallel/multi-agent build)

| # | Component | Language | Owner file(s) | Depends on |
|---|-----------|----------|----------------|------------|
| 1 | Ledger Core Engine | **Rust** | `01_LEDGER_CORE_ENGINE_RUST.md` | DB schema (frozen Day 1) |
| 2 | Reconciliation Engine | **C++** | `02_RECONCILIATION_ENGINE_CPP.md` | Event schema, DB schema |
| 3 | API Gateway + Idempotency | **Python** | `03_API_GATEWAY_AND_IDEMPOTENCY_PYTHON.md` | gRPC contract from #1 |
| 4 | RAG Compliance Copilot | **Python** | `04_RAG_COMPLIANCE_COPILOT.md` | Event schema, vector DB schema |
| — | DB + Event schema (frozen contract) | — | `05_DATABASE_AND_EVENT_SCHEMA.md` | Nothing — build this FIRST |
| — | Consistency/concurrency model | — | `06_CONSISTENCY_CONCURRENCY_MODEL.md` | Reference doc, no code ownership |
| — | Deployment/infra | — | `07_DEPLOYMENT_AND_INFRA.md` | All of the above |
| — | Build checklist | — | `BUILD_CHECKLIST.md` | — |

**The critical rule for parallel building:** `05_DATABASE_AND_EVENT_SCHEMA.md` is the contract everyone codes against. It must be written and frozen before agents 1–4 start, exactly like an OpenAPI spec or protobuf file would be in a real team. Nobody edits another agent's owned files — if you need a schema change, you propose it as a diff against `05_`.

---

## 4. Core invariants the whole system exists to protect

1. **Every transaction is balanced.** Sum of debits == sum of credits, always, enforced at the DB constraint level *and* in the Rust engine — never trust the caller.
2. **No transaction is ever mutated or deleted.** The ledger is append-only (WORM — write-once-read-many). Corrections are new reversing entries, never edits. This is standard in every real accounting system and is what makes an audit trail actually mean something.
3. **Every write is idempotent.** A payment request that gets safely retried (network timeout, client retry, load balancer retry) must never double-post. This is enforced via an idempotency key, not by "hoping the client doesn't retry."
4. **Reconciliation is a separate, adversarial process.** The reconciliation engine does not trust the ledger's own view of itself — it independently re-derives truth by matching three sources (payment gateway record, bank settlement file, internal ledger) and flags any mismatch instead of silently accepting it.

---

## 5. Real-world grounding (what this is modeled on)

- **Idempotency-key design**: modeled on Stripe's and Razorpay's public idempotency-key APIs — client generates a UUID, server caches the *first* response keyed on `(idempotency_key, request_hash)` for 24h, replays it on retry, and rejects a *different* payload reusing the same key.
- **Reconciliation drift handling**: modeled on how PG-to-bank settlement reconciliation actually works — T+1 or T+2 settlement files from the bank/network are matched against internal ledger entries; unmatched or amount-mismatched rows go into an exceptions queue for manual/automated resolution, not silently written off.
- **Eventual vs strong consistency split**: the ledger's own balance writes are strongly consistent (single Postgres transaction, serializable isolation on the account row). Everything downstream (reporting, RAG index, dashboards) is eventually consistent via Kafka — exactly how real fintechs avoid making every read path pay for the ledger's consistency guarantees.

See `06_CONSISTENCY_CONCURRENCY_MODEL.md` for the full treatment of this trade-off.
