-- 004_settlement_files.sql
-- Settlement files table for tracking uploaded settlement files

CREATE TABLE settlement_files (
    batch_id           UUID PRIMARY KEY,
    filename           TEXT NOT NULL,
    content            BYTEA NOT NULL,
    status             TEXT NOT NULL DEFAULT 'UPLOADED'
                          CHECK (status IN ('UPLOADED','PROCESSING','COMPLETED','FAILED')),
    processed_count    BIGINT NOT NULL DEFAULT 0,
    failed_count       BIGINT NOT NULL DEFAULT 0,
    uploaded_by        TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ
);

CREATE INDEX idx_settlement_files_status ON settlement_files(status);
CREATE INDEX idx_settlement_files_uploaded_by ON settlement_files(uploaded_by);
CREATE INDEX idx_settlement_files_created_at ON settlement_files(created_at);