# 01 — Ledger Core Engine (Rust)

**Owner:** Agent A
**Owns:** `core/src/ledger/*.rs`, `core/src/grpc_server.rs`, `core/tests/*`
**Depends on:** `05_DATABASE_AND_EVENT_SCHEMA.md` (frozen), gRPC contract in same file
**Does not touch:** reconciliation engine, gateway, RAG service

---

## 1. Responsibility

This is the money-movement hot path. It is the only component allowed to write to `ledger_entries` and `transactions`. It:

1. Accepts a `PostTransactionRequest` over gRPC.
2. Validates the request is balanced (sum debits == sum credits) **before** touching the DB.
3. Opens a single Postgres transaction at `SERIALIZABLE` isolation, inserts the `transactions` row + all `ledger_entries` rows, commits.
4. On successful commit, publishes a `ledger.transaction.posted` event to Kafka (outbox pattern — see §4, never a direct dual-write).
5. Serves `GetBalance` by summing entries (with an optional materialized-balance read-optimization, see §5).

## 2. Tech stack

- **Rust** stable, `tokio` async runtime
- `tonic` for gRPC server
- `sqlx` (compile-time checked queries) against Postgres, `SERIALIZABLE` isolation for the write path
- `rust_decimal` is explicitly **not** used for the ledger amount — amounts are `i64` minor units end to end
- `tracing` + `tracing-opentelemetry` for structured logs/traces

## 3. Core domain types

```rust
// core/src/ledger/domain.rs

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction { Debit, Credit }

#[derive(Debug, Clone)]
pub struct LedgerEntry {
    pub account_id: Uuid,
    pub direction: Direction,
    pub amount_minor: i64,   // always positive; direction carries the sign semantics
    pub currency: String,
}

#[derive(Debug, Clone)]
pub struct TransactionRequest {
    pub idempotency_key: String,
    pub transaction_type: TransactionType,
    pub reference_id: Option<String>,
    pub entries: Vec<LedgerEntry>,
    pub narrative: Option<String>,
}

#[derive(thiserror::Error, Debug)]
pub enum LedgerError {
    #[error("unbalanced transaction: debit {debit} != credit {credit}")]
    Unbalanced { debit: i64, credit: i64 },
    #[error("account {0} not found or not ACTIVE")]
    InvalidAccount(Uuid),
    #[error("idempotency key {0} already used with a different payload")]
    IdempotencyConflict(String),
    #[error("db error: {0}")]
    Db(#[from] sqlx::Error),
}
```

## 4. The posting algorithm (the part that has to be exactly right)

```rust
// core/src/ledger/service.rs

pub async fn post_transaction(
    pool: &PgPool,
    req: TransactionRequest,
) -> Result<PostedTransaction, LedgerError> {

    // 1. In-process balance check — fail fast before any I/O.
    let debit_total: i64 = req.entries.iter()
        .filter(|e| e.direction == Direction::Debit)
        .map(|e| e.amount_minor).sum();
    let credit_total: i64 = req.entries.iter()
        .filter(|e| e.direction == Direction::Credit)
        .map(|e| e.amount_minor).sum();
    if debit_total != credit_total {
        return Err(LedgerError::Unbalanced { debit: debit_total, credit: credit_total });
    }

    // 2. Idempotency short-circuit — check BEFORE opening the write transaction.
    //    request_hash = sha256(canonical_json(req)) computed by the caller (gateway) and
    //    passed through; the core engine re-verifies it matches what's cached.
    if let Some(cached) = idempotency::lookup(pool, &req.idempotency_key).await? {
        if cached.request_hash != req.request_hash() {
            return Err(LedgerError::IdempotencyConflict(req.idempotency_key.clone()));
        }
        return Ok(cached.response); // safe replay, no double post
    }

    // 3. Single SERIALIZABLE transaction: insert header, insert entries, insert
    //    idempotency record, all-or-nothing. The DB's deferred CONSTRAINT TRIGGER
    //    (see 05_DATABASE_AND_EVENT_SCHEMA.md §1.4) is the second, independent
    //    balance check — defense in depth against a bug in the check above.
    let mut tx = pool.begin().await?;
    sqlx::query!("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE").execute(&mut *tx).await?;

    let txn_id = insert_transaction_header(&mut tx, &req).await?;
    for entry in &req.entries {
        insert_ledger_entry(&mut tx, txn_id, entry).await?;
    }
    insert_idempotency_record(&mut tx, &req, txn_id).await?;
    insert_outbox_event(&mut tx, txn_id, &req).await?;  // see §4a — outbox pattern

    tx.commit().await?;  // deferred trigger fires here; rolls back the whole txn if unbalanced

    Ok(PostedTransaction { transaction_id: txn_id, /* ... */ })
}
```

### 4a. Outbox pattern (never dual-write to Postgres + Kafka directly)

A classic distributed-systems bug: writing to Postgres and then separately publishing to Kafka is **not atomic** — the process can crash between the two, silently dropping the event. Real systems (this one included) use the **transactional outbox pattern**:

1. In the *same* Postgres transaction as the ledger write, insert a row into an `outbox_events` table.
2. A separate lightweight poller (or Debezium/CDC) reads `outbox_events` and publishes to Kafka, marking rows as sent.

```sql
CREATE TABLE outbox_events (
    event_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic         TEXT NOT NULL,
    payload_json    JSONB NOT NULL,
    published        BOOLEAN NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

This is the difference between a toy project and one that survives the "what if Kafka is down for 30 seconds during a commit" interview question.

## 5. Balance reads — the derive-vs-cache trade-off

Naively summing `ledger_entries` for every `GetBalance` call is correct but doesn't scale to accounts with millions of entries. The real-world fix:

- Maintain a **materialized `account_balances` table** (`account_id`, `balance_minor`, `as_of_transaction_id`), updated in the *same* transaction as the entry insert.
- `GetBalance` reads the materialized value directly (fast path).
- A nightly (or on-demand) **balance auditor job** re-derives the balance from `ledger_entries` from scratch and asserts it matches the materialized value — if it doesn't, that's a P1 alert, not a silent correction. This mirrors how real bank cores catch balance-cache drift.

## 6. Concurrency model for a single account

Two transactions hitting the same account concurrently must not race. Approach:

- `SELECT ... FOR UPDATE` on the `account_balances` row inside the write transaction (pessimistic lock, held only for the duration of the commit — milliseconds).
- At `SERIALIZABLE` isolation, Postgres will abort one of two conflicting concurrent transactions with a serialization failure; the gateway (doc 03) retries with backoff, safely, because the whole call is idempotent.

## 7. What Agent A ships (Day-by-day, mirrors `BUILD_CHECKLIST.md`)

1. Domain types + balance-check unit tests (no I/O)
2. `sqlx` migrations for `accounts`, `transactions`, `ledger_entries`, `outbox_events`
3. `PostTransaction` happy path against local Postgres
4. Idempotency lookup/replay path + conflict path
5. Outbox poller → Kafka publisher
6. `GetBalance` fast path + nightly auditor job
7. Load test: concurrent posts to the same account, verify no lost updates and no double-posts under retry storms
