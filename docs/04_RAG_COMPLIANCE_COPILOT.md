# 04 — RAG Compliance & Reconciliation Copilot (Python)

**Owner:** Agent D
**Owns:** `rag/app/*.py`, `rag/indexer/*.py`, `rag/tests/*`
**Depends on:** Kafka event schema + `rag_documents` schema in `05_DATABASE_AND_EVENT_SCHEMA.md`
**Does not touch:** ledger core, recon engine, gateway internals (calls the gateway's read-only endpoints only)

---

## 1. Responsibility — what RAG is actually *for* here

This is the piece that makes the project stand out instead of being "yet another CRUD ledger." The RAG service answers grounded natural-language questions for two real audiences a BFSI backend team actually serves:

1. **Support/Ops:** *"Why did transaction ref `order_9f2a...` end up in the exception queue?"* — grounded in the actual `reconciliation_exceptions` row + the ledger entries for that transaction.
2. **Compliance:** *"What's our policy on reversing a settled transaction after 48 hours?"* — grounded in ingested internal policy docs and regulatory circulars (RBI-style), not the model's parametric memory, because compliance answers must be traceable to a source document.

This is a **retrieval-grounded QA system over two distinct corpora that are kept separate and cited separately** — policy/regulatory text vs. live operational data — which is itself a real design decision worth being able to explain in an interview.

## 2. Tech stack

- FastAPI service (separate process from the main gateway — this is a read-heavy, differently-scaled workload)
- `pgvector` (co-located, simplest) — swap for Qdrant/Weaviate if you want a dedicated vector store
- Embeddings: any consistent embedding model (e.g. `text-embedding-3-small`-class or an open model run locally) — the dimension must match `rag_documents.embedding` in doc 05
- No heavyweight framework required — a **hand-rolled retrieval pipeline** is preferable here (and more defensible in an interview than "I used LangChain and didn't look inside it")

## 3. Ingestion pipeline (two corpora, kept distinguishable)

```python
# rag/indexer/ingest_policy_docs.py
async def ingest_policy_document(path: str, source_type: str = "POLICY_DOC"):
    text = load_and_clean(path)                     # strip headers/footers, normalize whitespace
    chunks = chunk_text(text, max_tokens=400, overlap=50)  # semantic-ish chunking, not mid-sentence
    for chunk in chunks:
        embedding = await embed(chunk)
        await db.execute(
            """INSERT INTO rag_documents (source_type, source_ref, content, embedding)
               VALUES ($1, $2, $3, $4)""",
            source_type, path, chunk, embedding,
        )
```

```python
# rag/indexer/kafka_consumer.py — live operational data, indexed as it happens
async def consume_ledger_events():
    async for event in kafka_consumer.consume("ledger.transaction.posted",
                                                 "reconciliation.exception.raised"):
        if event.topic == "ledger.transaction.posted":
            narrative = render_transaction_narrative(event)  # human-readable summary
            await index_document(narrative, source_type="TXN_NARRATIVE",
                                  source_ref=event.payload["transaction_id"])
        elif event.topic == "reconciliation.exception.raised":
            narrative = render_exception_narrative(event)
            await index_document(narrative, source_type="RECONCILIATION_EXCEPTION",
                                  source_ref=event.payload["exception_id"])
```

This is why the RAG service subscribes to Kafka rather than polling Postgres directly — it stays eventually consistent with the ledger the same way the reporting layer does (see doc 06), and it demonstrates the event-driven pattern end to end instead of only in one place.

## 4. Retrieval + grounded answer generation

```python
# rag/app/qa.py
async def answer_question(question: str, scope: str | None = None) -> dict:
    query_embedding = await embed(question)

    # scope lets the caller (support tool vs compliance tool) restrict which
    # corpus is searchable — a support agent should never get compliance-only
    # regulatory interpretations mixed into an operational answer, and vice versa.
    source_filter = {
        "ops": ["TXN_NARRATIVE", "RECONCILIATION_EXCEPTION"],
        "compliance": ["POLICY_DOC", "REGULATORY_CIRCULAR"],
    }.get(scope)

    rows = await db.fetch(
        """SELECT doc_id, source_type, source_ref, content,
                  1 - (embedding <=> $1) AS similarity
           FROM rag_documents
           WHERE ($2::text[] IS NULL OR source_type = ANY($2))
           ORDER BY embedding <=> $1
           LIMIT 8""",
        query_embedding, source_filter,
    )

    context_block = "\n\n".join(
        f"[Source {i+1} | {r['source_type']} | {r['source_ref']}]\n{r['content']}"
        for i, r in enumerate(rows)
    )

    prompt = f"""You are a compliance/ops assistant for a ledger system.
Answer ONLY using the sources below. Cite sources as [Source N] inline.
If the sources don't contain the answer, say so explicitly — never guess.

{context_block}

Question: {question}
"""
    answer = await llm_complete(prompt)
    return {
        "answer": answer,
        "sources": [{"source_type": r["source_type"], "source_ref": r["source_ref"],
                      "similarity": r["similarity"]} for r in rows],
    }
```

The "cite or say you don't know" instruction plus returning raw `sources` alongside the answer is what makes this defensible as a compliance tool rather than a demo toy — an ungrounded hallucinated answer about a reversal policy is a real liability in this domain, so the system is designed to fail visibly (say "not found in sources") rather than silently.

## 5. Endpoints

```
POST /api/v1/rag/ask
  body: {"question": str, "scope": "ops" | "compliance"}
  returns: {"answer": str, "sources": [...]}

POST /api/v1/rag/ingest-policy   (admin-only, uploads a policy doc)
GET  /api/v1/rag/health
```

## 6. What Agent D ships

1. `rag_documents` ingestion pipeline for static policy docs (start with 3-5 real RBI circulars / made-up-but-realistic internal policy docs)
2. Kafka consumer indexing live transaction narratives + reconciliation exceptions
3. Retrieval + grounded-answer endpoint with scope filtering
4. Eval set: 15-20 hand-written Q&A pairs across both corpora, checked for correct source attribution (not just fluent-sounding answers) — this eval set is itself a good artifact to show in an interview
