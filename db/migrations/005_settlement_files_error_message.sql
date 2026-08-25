-- 005_settlement_files_error_message.sql
-- Add error_message column used by the settlement processor on failure

ALTER TABLE settlement_files ADD COLUMN IF NOT EXISTS error_message TEXT;
