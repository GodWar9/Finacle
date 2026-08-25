import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID
from datetime import datetime

from app.routes import transaction_history, settlements

class MockAsyncCM:
    def __init__(self, connection):
        self.conn = connection
    
    async def __aenter__(self):
        return self.conn
    
    async def __aexit__(self, *args):
        return None

@pytest.mark.asyncio
async def test_list_transactions():
    mock_pool = AsyncMock()
    account_id = uuid4()
    txn_id = uuid4()
    mock_pool.fetch = AsyncMock(return_value=[
        {
            "transaction_id": txn_id,
            "idempotency_key": "idem_123",
            "transaction_type": "PAYMENT",
            "reference_id": "order_123",
            "status": "POSTED",
            "reversal_of": None,
            "narrative": "Test payment",
            "created_at": datetime(2026, 1, 1, 0, 0, 0),
            "entry_id": uuid4(),
            "account_id": account_id,
            "direction": "DEBIT",
            "amount_minor": 10000,
            "currency": "INR"
        }
    ])
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    with patch("app.routes.transaction_history.get_pg_pool", return_value=mock_pool):
        result = await transaction_history.list_transactions("merchant_1", account_id=account_id)
        
        assert "transactions" in result
        assert len(result["transactions"]) == 1
        assert result["transactions"][0]["transaction_id"] == txn_id

@pytest.mark.asyncio
async def test_get_transaction():
    mock_pool = AsyncMock()
    txn_id = uuid4()
    account_id = uuid4()
    
    mock_pool.fetchrow = AsyncMock(return_value={
        "transaction_id": txn_id,
        "idempotency_key": "idem_123",
        "transaction_type": "PAYMENT",
        "reference_id": "order_123",
        "status": "POSTED",
        "reversal_of": None,
        "narrative": "Test payment",
        "created_at": datetime(2026, 1, 1, 0, 0, 0)
    })
    
    mock_pool.fetch = AsyncMock(return_value=[
        {
            "entry_id": uuid4(),
            "account_id": account_id,
            "direction": "DEBIT",
            "amount_minor": 10000,
            "currency": "INR"
        },
        {
            "entry_id": uuid4(),
            "account_id": account_id,
            "direction": "CREDIT",
            "amount_minor": 10000,
            "currency": "INR"
        }
    ])
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    with patch("app.routes.transaction_history.get_pg_pool", return_value=mock_pool):
        result = await transaction_history.get_transaction(txn_id, "merchant_1")
        
        assert result["transaction_id"] == txn_id
        assert len(result["entries"]) == 2

@pytest.mark.asyncio
async def test_get_transaction_not_found():
    mock_pool = AsyncMock()
    txn_id = uuid4()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    with patch("app.routes.transaction_history.get_pg_pool", return_value=mock_pool):
        with pytest.raises(Exception) as exc_info:
            await transaction_history.get_transaction(txn_id, "merchant_1")
        
        assert exc_info.value.status_code == 404

@pytest.mark.asyncio
async def test_upload_settlement_file():
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    mock_file = MagicMock()
    mock_file.filename = "settlement.txt"
    mock_file.read = AsyncMock(return_value=b"test content")
    
    with patch("app.routes.settlements.get_pg_pool", return_value=mock_pool):
        result = await settlements.upload_settlement_file(
            mock_file, None, "merchant_1"
        )
        
        assert "batch_id" in result
        assert result["filename"] == "settlement.txt"
        assert result["status"] == "UPLOADED"

@pytest.mark.asyncio
async def test_get_settlement_status():
    mock_pool = AsyncMock()
    batch_id = "test-batch-123"
    mock_pool.fetchrow = AsyncMock(return_value={
        "batch_id": batch_id,
        "filename": "settlement.txt",
        "status": "UPLOADED",
        "processed_count": 0,
        "failed_count": 0,
        "created_at": datetime(2026, 1, 1, 0, 0, 0),
        "completed_at": None
    })
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    with patch("app.routes.settlements.get_pg_pool", return_value=mock_pool):
        result = await settlements.get_settlement_status(batch_id, "merchant_1")
        
        assert result["batch_id"] == batch_id
        assert result["status"] == "UPLOADED"

@pytest.mark.asyncio
async def test_canonical_hash():
    from app.idempotency import canonical_hash
    body = {"amount": 100, "currency": "INR"}
    hash1 = canonical_hash(body)
    hash2 = canonical_hash(body)
    assert hash1 == hash2
    assert len(hash1) == 64

@pytest.mark.asyncio
async def test_webhook_signature():
    from app.auth import verify_webhook_signature
    payload = b'{"test": "data"}'
    secret = "test-secret"
    
    import hmac
    import hashlib
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    
    assert verify_webhook_signature(payload, expected, secret) == True
    assert verify_webhook_signature(payload, "invalid", secret) == False

@pytest.mark.asyncio
async def test_auth_middleware():
    from app.auth import AuthMiddleware
    middleware = AuthMiddleware(None)
    middleware.api_keys = {"test-key": "merchant_1"}
    
    mock_request = MagicMock()
    mock_request.url.path = "/health"
    mock_request.headers = {}
    
    mock_call_next = AsyncMock(return_value=MagicMock())
    
    response = await middleware.dispatch(mock_request, mock_call_next)
    mock_call_next.assert_called_once()

@pytest.mark.asyncio
async def test_auth_middleware_rejects_missing_key():
    from app.auth import AuthMiddleware
    from fastapi import HTTPException
    middleware = AuthMiddleware(None)
    middleware.api_keys = {"test-key": "merchant_1"}
    
    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/transactions"
    mock_request.headers = {}
    
    mock_call_next = AsyncMock()
    
    with pytest.raises(HTTPException) as exc_info:
        await middleware.dispatch(mock_request, mock_call_next)
    
    assert exc_info.value.status_code == 401