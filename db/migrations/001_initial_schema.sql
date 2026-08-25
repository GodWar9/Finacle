-- 001_initial_schema.sql
-- Core ledger tables

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1.1 accounts
CREATE TABLE accounts (
    account_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_number   TEXT UNIQUE NOT NULL,
    account_type     TEXT NOT NULL CHECK (account_type IN
                        ('ASSET','LIABILITY','EQUITY','REVENUE','EXPENSE')),
    owner_ref        TEXT NOT NULL,
    currency         CHAR(3) NOT NULL DEFAULT 'INR',
    status           TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE','FROZEN','CLOSED')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 1.2 transactions (journal header)
CREATE TABLE transactions (
    transaction_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key    TEXT UNIQUE NOT NULL,
    request_hash       TEXT NOT NULL,
    transaction_type   TEXT NOT NULL,
    reference_id       TEXT,
    status             TEXT NOT NULL DEFAULT 'POSTED'
                          CHECK (status IN ('POSTED','REVERSED')),
    reversal_of        UUID REFERENCES transactions(transaction_id),
    narrative          TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_txn_idempotency ON transactions(idempotency_key);
CREATE INDEX idx_txn_reference   ON transactions(reference_id);

-- 1.3 ledger_entries (append-only double-entry rows - WORM)
CREATE TABLE ledger_entries (
    entry_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id    UUID NOT NULL REFERENCES transactions(transaction_id),
    account_id        UUID NOT NULL REFERENCES accounts(account_id),
    direction          TEXT NOT NULL CHECK (direction IN ('DEBIT','CREDIT')),
    amount_minor       BIGINT NOT NULL CHECK (amount_minor > 0),
    currency            CHAR(3) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_entries_account ON ledger_entries(account_id, created_at);
CREATE INDEX idx_entries_txn     ON ledger_entries(transaction_id);

-- 1.4 Balance-invariant trigger
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
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_transaction_balanced();

-- WORM enforcement - revoke UPDATE/DELETE on ledger_entries
-- Note: In production, this would be done via a role. For dev, we skip this.
-- REVOKE UPDATE, DELETE ON ledger_entries FROM ledger_app_role;

-- 1.5 reconciliation_exceptions
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
CREATE INDEX idx_recon_batch ON reconciliation_exceptions(batch_id);
CREATE INDEX idx_recon_type ON reconciliation_exceptions(exception_type);

-- 1.6 Idempotency cache table (backstop for Redis)
CREATE TABLE idempotency_records (
    idempotency_key   TEXT PRIMARY KEY,
    request_hash       TEXT NOT NULL,
    response_body        JSONB NOT NULL,
    status_code           INT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at               TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_idem_expires ON idempotency_records(expires_at);

-- 1.7 Outbox events table (transactional outbox pattern)
CREATE TABLE outbox_events (
    event_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic         TEXT NOT NULL,
    payload_json    JSONB NOT NULL,
    published        BOOLEAN NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_outbox_published ON outbox_events(published, created_at);

-- 1.8 Materialized account balances (for fast balance reads)
CREATE TABLE account_balances (
    account_id        UUID PRIMARY KEY REFERENCES accounts(account_id),
    balance_minor     BIGINT NOT NULL DEFAULT 0,
    currency          CHAR(3) NOT NULL,
    as_of_transaction_id UUID REFERENCES transactions(transaction_id),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Function to update account_balances in the same transaction
CREATE OR REPLACE FUNCTION update_account_balance()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE account_balances
        SET balance_minor = balance_minor + 
            CASE WHEN NEW.direction = 'DEBIT' THEN NEW.amount_minor ELSE -NEW.amount_minor END,
            as_of_transaction_id = NEW.transaction_id,
            updated_at = now()
        WHERE account_id = NEW.account_id;
        
        IF NOT FOUND THEN
            INSERT INTO account_balances (account_id, balance_minor, currency, as_of_transaction_id)
            VALUES (NEW.account_id, 
                CASE WHEN NEW.direction = 'DEBIT' THEN NEW.amount_minor ELSE -NEW.amount_minor END,
                NEW.currency, NEW.transaction_id);
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_account_balance
AFTER INSERT ON ledger_entries
FOR EACH ROW EXECUTE FUNCTION update_account_balance();