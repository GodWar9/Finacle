-- Legacy rows deliberately remain unassigned until an operator verifies ownership.
ALTER TABLE accounts ADD COLUMN merchant_id TEXT;
CREATE INDEX idx_accounts_merchant ON accounts(merchant_id, account_id);

-- Tenant ownership cannot be reassigned after assignment; journal history follows it.
CREATE FUNCTION guard_account_merchant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.merchant_id IS NOT NULL AND NEW.merchant_id IS DISTINCT FROM OLD.merchant_id THEN
    RAISE EXCEPTION 'Account merchant ownership is immutable';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER trg_account_merchant BEFORE UPDATE ON accounts
FOR EACH ROW EXECUTE FUNCTION guard_account_merchant();
