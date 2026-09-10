# 05 — Database & Event Schema (FROZEN CONTRACT — build this first)

**Ownership:** Shared reference. No single agent owns this — it is the interface contract every other component codes against. Changes require updating this file and notifying all four component owners before merging.

---

## 1. PostgreSQL schema — the ledger (source of truth)

### 1.1 `accounts`

```sql
CREATE TABLE accounts (
    account_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_number   TEXT UNIQUE NOT NULL,
    account_type     TEXT NOT NULL CHECK (account_type IN
                        ('ASSET','LIABILITY','EQUITY','REVENUE','EXPENSE')),
    owner_ref        TEXT NOT NULL,        -- merchant_id / customer_id
    currency         CHAR(3) NOT NULL DEFAULT 'INR',
    status           TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE','FROZEN','CLOSED')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Balance is **never** stored as a mutable column on `accounts`. It is always *derived* by summing `ledger_entries` (see below). A materialized `account_balances` table provides O(1) reads but is always kept in sync via trigger — the auditor job (doc 01) verifies consistency between the two.

### 1.2 `transactions` (the journal header)

```sql
CREATE TABLE transactions (
    transaction_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key    TEXT UNIQUE NOT NULL,
    request_hash       TEXT NOT NULL,       -- sha256 of canonicalized request body
    transaction_type   TEXT NOT NULL,       -- 'PAYMENT','REFUND','REVERSAL','FEE','SETTLEMENT'
    reference_id       TEXT,                -- external PG/order reference
    status             TEXT NOT NULL DEFAULT 'POSTED'
                          CHECK (status IN ('POSTED','REVERSED')),
    reversal_of        UUID REFERENCES transactions(transaction_id),
    narrative          TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_txn_idempotency ON transactions(idempotency_key);
CREATE INDEX idx_txn_reference   ON transactions(reference_id);
```

### 1.3 `ledger_entries` (the append-only double-entry rows — WORM)

```sql
CREATE TABLE ledger_entries (
    entry_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id    UUID NOT NULL REFERENCES transactions(transaction_id),
    account_id        UUID NOT NULL REFERENCES accounts(account_id),
    direction          TEXT NOT NULL CHECK (direction IN ('DEBIT','CREDIT')),
    amount_minor       BIGINT NOT NULL CHECK (amount_minor > 0),  -- paise, never float
    currency            CHAR(3) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_entries_account ON ledger_entries(account_id, created_at);
CREATE INDEX idx_entries_txn     ON ledger_entries(transaction_id);

-- Enforced at the DB level with a trigger, not just app code (see 1.4)
REVOKE UPDATE, DELETE ON ledger_entries FROM ledger_app_role;  -- WORM enforcement
```

**Money is always `BIGINT` minor units (paise), never `FLOAT`/`NUMERIC` with implicit rounding assumptions.** This is non-negotiable in real ledgers.

### 1.4 Balance-invariant trigger

```sql
-- After every insert batch for a transaction, verify debits == credits.
CREATE OR REPLACE FUNCTION check_transaction_balanced() RETURNS TRIGGER AS $$
DECLARE
    debit_total BIGINT;
    credit_total BIGINT;
BEGIN
    SELECT COALESCE(SUM(amount_minor) FILTER (WHERE direction='DEBIT'), 0),
           COALESCE(SUM(amount_minor) FILTER (WHERE direction='CREDIT'), 0)
    INTO debit_total, credit_total
    FROM ledger_entries WHERE transaction_id = NEW.transaction_id;

    IF debit_total != credit_total THEN
        RAISE EXCEPTION 'Unbalanced transaction %: debit % != credit %',
            NEW.transaction_id, debit_total, credit_total;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_balanced_txn
AFTER INSERT ON ledger_entries
DEFERRABLE INITIALLY DEFERRED   -- checked at COMMIT, after all rows for the txn are in
FOR EACH ROW EXECUTE FUNCTION check_transaction_balanced();
```

This is the enforcement mechanism the Rust engine relies on as a **second, independent** check (defense in depth — app-level validation in Rust, hard DB constraint here).

### 1.5 `account_balances` (materialized balance cache — kept in sync by trigger)

```sql
CREATE TABLE account_balances (
    account_id        UUID PRIMARY KEY REFERENCES accounts(account_id),
    balance_minor     BIGINT NOT NULL DEFAULT 0,
    currency          CHAR(3) NOT NULL,
    as_of_transaction_id UUID,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Balance is also **derived** from `ledger_entries` for correctness auditing (see doc 01 auditor). This materialized table exists for O(1) reads — `GetBalance` never sums entries at query time. A trigger keeps it in sync on every insert to `ledger_entries`.

### 1.6 `outbox_events` (transactional outbox — see doc 01)

```sql
CREATE TABLE outbox_events (
    event_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id    UUID NOT NULL REFERENCES transactions(transaction_id),
    event_type        TEXT NOT NULL,       -- 'ledger.transaction.posted' | 'ledger.transaction.reversed'
    payload           JSONB NOT NULL,
    published         BOOLEAN NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_outbox_unpublished ON outbox_events(created_at) WHERE published = false;
```

The Rust outbox poller reads unpublished events with `FOR UPDATE SKIP LOCKED`, publishes to Kafka, then marks them `published = true`. This guarantees at-least-once delivery without coupling the ledger write path to Kafka availability.

### 1.7 `reconciliation_exceptions` (owned by the C++ reconciliation engine, written via its Python-callable output — see doc 02)

```sql
CREATE TABLE reconciliation_exceptions (
    exception_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id            UUID NOT NULL,
    exception_type       TEXT NOT NULL CHECK (exception_type IN
                            ('MISSING_IN_LEDGER','MISSING_IN_BANK_FILE',
                             'AMOUNT_MISMATCH','DUPLICATE','STATUS_MISMATCH')),
    ledger_transaction_id UUID REFERENCES transactions(transaction_id),
    bank_reference        TEXT,
    ledger_amount_minor    BIGINT,
    bank_amount_minor      BIGINT,
    resolved                BOOLEAN NOT NULL DEFAULT false,
    detected_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 1.6 Idempotency cache table (backstop for Redis — see doc 03)

```sql
CREATE TABLE idempotency_records (
    idempotency_key   TEXT PRIMARY KEY,
    request_hash       TEXT NOT NULL,
    response_body        JSONB NOT NULL,
    status_code           INT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at               TIMESTAMPTZ NOT NULL
);
```

---

## 2. Kafka event schema (the eventual-consistency backbone)

Topics (Avro/JSON Schema, `key = account_id` or `transaction_id` for partition locality):

| Topic | Producer | Consumers | Payload |
|---|---|---|---|
| `ledger.transaction.posted` | Rust core | Reporting, RAG indexer | `{transaction_id, entries[], narrative, posted_at}` |
| `ledger.transaction.reversed` | Rust core | Reporting, RAG indexer | `{transaction_id, reversal_of, entries[]}` |
| `reconciliation.batch.completed` | C++ engine (via Python shim) | Reporting, RAG indexer | `{batch_id, matched_count, exception_count}` |
| `reconciliation.exception.raised` | C++ engine | RAG indexer, alerting | `{exception_id, exception_type, transaction_id}` |

```json
// ledger.transaction.posted — canonical event shape
{
  "transaction_id": "uuid",
  "transaction_type": "PAYMENT",
  "reference_id": "order_9f2a...",
  "entries": [
    {"account_id": "uuid", "direction": "DEBIT",  "amount_minor": 150000, "currency": "INR"},
    {"account_id": "uuid", "direction": "CREDIT", "amount_minor": 150000, "currency": "INR"}
  ],
  "narrative": "Payment capture for order 9f2a...",
  "posted_at": "2026-08-24T10:15:00Z"
}
```

---

## 3. gRPC contract (Python gateway ↔ Rust core)

```protobuf
// ledger.proto — frozen interface, versioned. Both Python and Rust generate stubs from this file.
syntax = "proto3";
package ledger.v1;

message LedgerEntry {
  string account_id = 1;
  string direction = 2;      // "DEBIT" | "CREDIT"
  int64 amount_minor = 3;
  string currency = 4;
}

message PostTransactionRequest {
  string idempotency_key = 1;
  string transaction_type = 2;
  string reference_id = 3;
  repeated LedgerEntry entries = 4;
  string narrative = 5;
}

message PostTransactionResponse {
  string transaction_id = 1;
  string status = 2;
  int64 posted_at_unix_ms = 3;
}

message GetBalanceRequest { string account_id = 1; }
message GetBalanceResponse {
  string account_id = 1;
  int64 balance_minor = 2;
  string currency = 3;
  int64 as_of_unix_ms = 4;
}

service LedgerCore {
  rpc PostTransaction(PostTransactionRequest) returns (PostTransactionResponse);
  rpc GetBalance(GetBalanceRequest) returns (GetBalanceResponse);
  rpc ReverseTransaction(ReverseTransactionRequest) returns (PostTransactionResponse);
}

message ReverseTransactionRequest {
  string transaction_id = 1;
  string idempotency_key = 2;
  string reason = 3;
}
```

---

## 4. Vector DB schema (RAG copilot — see doc 04)

Using `pgvector` (co-located with the main Postgres for simplicity) or Qdrant if you want it decoupled:

```sql
CREATE TABLE rag_documents (
    doc_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type     TEXT NOT NULL CHECK (source_type IN
                       ('POLICY_DOC','REGULATORY_CIRCULAR','TXN_NARRATIVE','RECONCILIATION_EXCEPTION')),
    source_ref       TEXT,             -- e.g. transaction_id or file name
    content            TEXT NOT NULL,
    embedding            VECTOR(1536),   -- match your embedding model's dim
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_rag_embedding ON rag_documents USING ivfflat (embedding vector_cosine_ops);
```

---

**Anything downstream of this file (Rust, C++, Python) treats it as the single source of truth for shapes on the wire and at rest. Do not improvise field names.**
