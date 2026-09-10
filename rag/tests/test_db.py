from unittest.mock import AsyncMock, MagicMock

import pytest
from app.db import index_document, search_documents


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    
    # Create a proper async context manager using a simple class
    class AsyncCM:
        def __init__(self, connection):
            self.conn = connection
        
        async def __aenter__(self):
            return self.conn
        
        async def __aexit__(self, *args):
            return None
    
    pool.acquire = MagicMock(side_effect=lambda: AsyncCM(conn))
    return pool

@pytest.mark.asyncio
async def test_search_documents_with_filter(mock_pool):
    # Get the connection from the pool mock
    cm = mock_pool.acquire()
    conn = await cm.__aenter__()
    conn.fetch.return_value = [
        {"doc_id": "1", "source_type": "POLICY_DOC", "source_ref": "policy.txt", "content": "test content", "similarity": 0.9},
        {"doc_id": "2", "source_type": "POLICY_DOC", "source_ref": "policy.txt", "content": "test content 2", "similarity": 0.8},
    ]
    
    results = await search_documents(mock_pool, [0.1]*1536, ["POLICY_DOC"], 5)
    
    assert len(results) == 2
    assert results[0]["source_type"] == "POLICY_DOC"

@pytest.mark.asyncio
async def test_search_documents_without_filter(mock_pool):
    cm = mock_pool.acquire()
    conn = await cm.__aenter__()
    conn.fetch.return_value = [
        {"doc_id": "1", "source_type": "POLICY_DOC", "source_ref": "policy.txt", "content": "test content", "similarity": 0.9},
    ]
    
    results = await search_documents(mock_pool, [0.1]*1536, None, 5)
    
    assert len(results) == 1

@pytest.mark.asyncio
async def test_index_document(mock_pool):
    cm = mock_pool.acquire()
    conn = await cm.__aenter__()
    conn.fetchrow.return_value = {"doc_id": "test-uuid"}
    
    doc_id = await index_document(mock_pool, "POLICY_DOC", "policy.txt", "test content", [0.1]*1536)
    
    assert doc_id == "test-uuid"