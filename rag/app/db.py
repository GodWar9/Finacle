from typing import Any
from uuid import UUID

import asyncpg

from app.config import get_settings

settings = get_settings()


async def _init_vector_codec(conn) -> None:
    """Teach asyncpg how to encode/decode pgvector `vector` columns."""
    from pgvector.asyncpg import register_vector

    await register_vector(conn)


async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        init=_init_vector_codec,
    )


async def index_document(
    pool: asyncpg.Pool,
    source_type: str,
    source_ref: str,
    content: str,
    embedding: list[float],
    *,
    merchant_id: str | None = None,
) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO rag_documents (source_type, source_ref, content, embedding, merchant_id)
               VALUES ($1, $2, $3, $4, $5)
               RETURNING doc_id""",
            source_type,
            source_ref,
            content,
            embedding,
            merchant_id,
        )
        return row["doc_id"]


async def search_documents(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    source_types: list[str] | None = None,
    limit: int = 8,
    *,
    merchant_id: str | None = None,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT doc_id, source_type, source_ref, content,
                      1 - (embedding <=> $1) AS similarity
               FROM rag_documents
               WHERE (merchant_id = $2 OR (merchant_id IS NULL AND source_type IN ('POLICY_DOC','REGULATORY_CIRCULAR')))
                 AND ($3::text[] IS NULL OR source_type = ANY($3::text[]))
               ORDER BY embedding <=> $1 LIMIT $4""",
            query_embedding,
            merchant_id,
            source_types,
            limit,
        )
        return [dict(row) for row in rows]


async def get_document_by_ref(
    pool: asyncpg.Pool, source_type: str, source_ref: str
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM rag_documents WHERE source_type = $1 AND source_ref = $2",
            source_type,
            source_ref,
        )
        return dict(row) if row else None
