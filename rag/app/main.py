from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
import structlog
import logging

from app.config import get_settings
from app.models.schemas import AskRequest, AskResponse, IngestPolicyRequest, IngestPolicyResponse, HealthResponse
from app.qa import answer_question
from app.db import get_pool, index_document
from app.embedding import embed_text, chunk_text

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logging.basicConfig(level=logging.INFO)

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(
    title="RAG Compliance & Reconciliation Copilot",
    version="0.1.0",
    lifespan=lifespan,
)

@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="healthy", service="rag-copilot")

@app.post("/api/v1/rag/ask", response_model=AskResponse)
async def ask_question(request: AskRequest):
    return await answer_question(request)

@app.post("/api/v1/rag/ingest-policy", response_model=IngestPolicyResponse, status_code=status.HTTP_201_CREATED)
async def ingest_policy(request: IngestPolicyRequest):
    chunks = chunk_text(request.content)
    
    pool = await get_pool()
    try:
        doc_ids = []
        for i, chunk in enumerate(chunks):
            embedding = await embed_text(chunk)
            source_ref = f"{request.source_ref}#chunk_{i}"
            doc_id = await index_document(pool, request.source_type, source_ref, chunk, embedding)
            doc_ids.append(doc_id)
        
        return IngestPolicyResponse(doc_id=doc_ids[0], chunks_indexed=len(chunks))
    finally:
        await pool.close()