import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.db import search_documents, index_document, get_pool

@pytest.fixture
def mock_pool():
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire = AsyncMock()
    pool.acquire.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.__aexit__ = AsyncMock(return_value=None)
    return pool

@pytest.mark.asyncio
async def test_search_documents_with_filter(mock_pool):
    mock_pool.acquire().__aenter__().fetch.return_value = [
        {"doc_id": "1", "source_type": "POLICY_DOC", "source_ref": "policy.txt", "content": "test content", "similarity": 0.9},
        {"doc_id": "2", "source_type": "POLICY_DOC", "source_ref": "policy.txt", "content": "test content 2", "similarity": 0.8},
    ]
    
    results = await search_documents(mock_pool, [0.1]*1536, ["POLICY_DOC"], 5)
    
    assert len(results) == 2
    assert results[0]["source_type"] == "POLICY_DOC"
    mock_pool.acquire().__aenter__().fetch.assert_called_once()

@pytest.mark.asyncio
async def test_search_documents_without_filter(mock_pool):
    mock_pool.acquire().__aenter__().fetch.return_value = [
        {"doc_id": "1", "source_type": "POLICY_DOC", "source_ref": "policy.txt", "content": "test content", "similarity": 0.9},
    ]
    
    results = await search_documents(mock_pool, [0.1]*1536, None, 5)
    
    assert len(results) == 1
    mock_pool.acquire().__aenter__().fetch.assert_called_once()

@pytest.mark.asyncio
async def test_index_document(mock_pool):
    mock_pool.acquire().__aenter__().fetchrow.return_value = {"doc_id": "test-uuid"}
    
    doc_id = await index_document(mock_pool, "POLICY_DOC", "policy.txt", "test content", [0.1]*1536)
    
    assert doc_id == "test-uuid"
    mock_pool.acquire().__aenter__().fetchrow.assert_called_once()