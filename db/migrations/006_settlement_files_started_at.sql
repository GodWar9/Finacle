-- 006_settlement_files_started_at.sql
-- Track when the settlement processor begins working a batch.

ALTER TABLE settlement_files ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;