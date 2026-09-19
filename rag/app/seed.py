from pathlib import Path

import structlog

from app.config import get_settings
from app.db import get_document_by_ref, get_pool, index_document
from app.embedding import chunk_text, embed_text

logger = structlog.get_logger()
settings = get_settings()

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"


async def seed_policies() -> None:
    """Index the bundled policy documents (rag/policies/*.txt) into the
    knowledge base. Idempotent per source_ref, and only runs when an OpenAI
    API key is configured (embed_text otherwise degrades to zero vectors)."""
    if not settings.openai_api_key:
        logger.warning("policy_seed_skipped_no_api_key")
        return

    if not POLICIES_DIR.is_dir():
        logger.warning("policy_seed_skipped_missing_dir", dir=str(POLICIES_DIR))
        return

    pool = await get_pool()
    try:
        counts = {}
        for path in sorted(POLICIES_DIR.glob("*.txt")):
            source_ref = path.stem
            existing = await get_document_by_ref(pool, "POLICY_DOC", source_ref)
            if existing is not None:
                counts[source_ref] = "already-indexed"
                continue

            content = path.read_text(encoding="utf-8")
            chunks = chunk_text(content)
            for i, chunk in enumerate(chunks):
                embedding = await embed_text(chunk)
                await index_document(
                    pool,
                    "POLICY_DOC",
                    f"{source_ref}#chunk_{i}",
                    chunk,
                    embedding,
                )
            counts[source_ref] = len(chunks)

        logger.info("policy_seed_completed", counts=counts)
    finally:
        await pool.close()
