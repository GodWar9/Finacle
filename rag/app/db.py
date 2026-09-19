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
) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO rag_documents (source_type, source_ref, content, embedding)
               VALUES ($1, $2, $3, $4)
               RETURNING doc_id""",
            source_type,
            source_ref,
            content,
            embedding,
        )
        return row["doc_id"]


async def search_documents(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    source_types: list[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        if source_types:
            placeholders = ",".join([f"${i + 2}" for i in range(len(source_types))])
            query = f"""
                SELECT doc_id, source_type, source_ref, content,
                       1 - (embedding <=> $1) AS similarity
                FROM rag_documents
                WHERE source_type IN ({placeholders})
                ORDER BY embedding <=> $1
                LIMIT ${len(source_types) + 2}
            """
            params = [query_embedding] + source_types + [limit]
        else:
            query = """
                SELECT doc_id, source_type, source_ref, content,
                       1 - (embedding <=> $1) AS similarity
                FROM rag_documents
                ORDER BY embedding <=> $1
                LIMIT $2
            """
            params = [query_embedding, limit]

        rows = await conn.fetch(query, *params)
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
