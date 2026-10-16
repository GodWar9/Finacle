-- Unassigned operational documents remain inaccessible to merchant queries.
ALTER TABLE rag_documents ADD COLUMN merchant_id TEXT;
CREATE INDEX idx_rag_documents_merchant ON rag_documents(merchant_id);
