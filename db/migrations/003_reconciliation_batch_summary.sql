-- 003_reconciliation_batch_summary.sql
-- Reconciliation batch summary table

CREATE TABLE reconciliation_batch_summary (
    batch_id           UUID PRIMARY KEY,
    matched_count      BIGINT NOT NULL DEFAULT 0,
    exception_count    BIGINT NOT NULL DEFAULT 0,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'RUNNING'
                          CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    error_message      TEXT
);

CREATE INDEX idx_recon_batch_status ON reconciliation_batch_summary(status);