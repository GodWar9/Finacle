-- 002_rag_documents.sql
-- RAG copilot vector store (using pgvector)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE rag_documents (
    doc_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type     TEXT NOT NULL CHECK (source_type IN
                       ('POLICY_DOC','REGULATORY_CIRCULAR','TXN_NARRATIVE','RECONCILIATION_EXCEPTION')),
    source_ref       TEXT,
    content            TEXT NOT NULL,
    embedding            VECTOR(1536),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_rag_embedding ON rag_documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_rag_source_type ON rag_documents(source_type);
CREATE INDEX idx_rag_source_ref ON rag_documents(source_ref);