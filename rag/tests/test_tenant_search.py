import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest
from app.db import _init_vector_codec, index_document, search_documents


@pytest.mark.asyncio
async def test_real_vector_search_excludes_foreign_and_unassigned_operational_data():
    connection = os.getenv("TEST_DATABASE_URL")
    if not connection:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated *_test pgvector database")
    url = urlsplit(connection)
    if url.scheme not in ("postgres", "postgresql") or not url.path.endswith("_test"):
        raise RuntimeError("Refusing a non-test database")
    if (
        os.getenv("DATABASE_URL")
        and urlsplit(os.environ["DATABASE_URL"]).path == url.path
    ):
        raise RuntimeError("Test database must differ from DATABASE_URL")
    schema = "rag_test_" + uuid4().hex
    admin = await asyncpg.connect(connection)
    await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = None
    try:
        await admin.execute(f'SET search_path TO "{schema}",public')
        migrations = Path(__file__).resolve().parents[2] / "db" / "migrations"
        for name in ("002_rag_documents.sql", "009_rag_tenant_ownership.sql"):
            await admin.execute((migrations / name).read_text())
        pool = await asyncpg.create_pool(
            connection,
            server_settings={"search_path": schema + ",public"},
            init=_init_vector_codec,
        )
        vector = [0.1] * 1536
        for kind, reference, merchant in (
            ("TXN_NARRATIVE", "a", "merchant_a"),
            ("TXN_NARRATIVE", "b", "merchant_b"),
            ("TXN_NARRATIVE", "unassigned", None),
            ("POLICY_DOC", "policy", None),
        ):
            await index_document(
                pool, kind, reference, reference, vector, merchant_id=merchant
            )
        docs = await search_documents(pool, vector, merchant_id="merchant_a")
        assert {doc["source_ref"] for doc in docs} == {"a", "policy"}
        docs = await search_documents(
            pool, vector, ["TXN_NARRATIVE"], merchant_id="merchant_b"
        )
        assert {doc["source_ref"] for doc in docs} == {"b"}
        docs = await search_documents(pool, vector)
        assert {doc["source_ref"] for doc in docs} == {"policy"}
    finally:
        if pool:
            await pool.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()
