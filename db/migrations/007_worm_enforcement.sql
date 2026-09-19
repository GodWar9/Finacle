-- 007_worm_enforcement.sql
-- Database-layer WORM (write-once-read-many) enforcement.
--
-- The ledger journal (ledger_entries) is append-only: rows may be inserted and
-- read but never updated or deleted. Transaction headers may only receive the
-- legitimate status flip performed by an atomic reversal, so DELETE (and only
-- DELETE) is blocked on them.
--
-- BEFORE triggers are used instead of role ACLs so the guarantee holds for
-- every connection role, including superusers, which bypass ACL checks.

CREATE OR REPLACE FUNCTION enforce_append_only() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'table %.% is append-only: % not permitted',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_entries_worm
BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH STATEMENT EXECUTE FUNCTION enforce_append_only();

CREATE TRIGGER trg_transactions_worm
BEFORE DELETE ON transactions
FOR EACH STATEMENT EXECUTE FUNCTION enforce_append_only();