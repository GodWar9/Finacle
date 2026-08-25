import asyncio
import asyncpg
import structlog
from pathlib import Path
from typing import List
from uuid import UUID

from app.config import get_settings
from app.embedding import chunk_text, embed_text
from app.db import index_document, get_pool

logger = structlog.get_logger()
settings = get_settings()

def load_and_clean(filepath: str) -> str:
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
    
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith('---') and not line.startswith('==='):
            cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines)

async def ingest_policy_document(filepath: str, source_type: str = "POLICY_DOC") -> dict:
    text = load_and_clean(filepath)
    chunks = chunk_text(text)
    
    pool = await get_pool()
    doc_ids = []
    
    for i, chunk in enumerate(chunks):
        embedding = await embed_text(chunk)
        source_ref = f"{Path(filepath).name}#chunk_{i}"
        doc_id = await index_document(pool, source_type, source_ref, chunk, embedding)
        doc_ids.append(doc_id)
    
    await pool.close()
    
    return {
        "file": filepath,
        "chunks_indexed": len(chunks),
        "doc_ids": [str(d) for d in doc_ids]
    }

async def ingest_policy_directory(directory: str, source_type: str = "POLICY_DOC") -> List[dict]:
    path = Path(directory)
    results = []
    
    for file_path in path.glob("*.txt"):
        try:
            result = await ingest_policy_document(str(file_path), source_type)
            results.append(result)
            logger.info("ingested_policy_doc", file=file_path.name, chunks=result["chunks_indexed"])
        except Exception as e:
            logger.error("ingest_failed", file=file_path.name, error=str(e))
            results.append({"file": str(file_path), "error": str(e)})
    
    return results

if __name__ == "__main__":
    import sys
    directory = sys.argv[1] if len(sys.argv) > 1 else "./policies"
    asyncio.run(ingest_policy_directory(directory))