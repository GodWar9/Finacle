import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
from uuid import uuid4

from app.routes import transaction_history, settlements
from app.idempotency import canonical_hash
from app.auth import AuthMiddleware, verify_webhook_signature

@pytest.mark.asyncio
async def test_list_transactions():
    mock_pool = AsyncMock()
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    account_id = uuid4()
    txn_id = uuid4()
    conn.fetch.return_value = [
        {
            "transaction_id": txn_id,
            "idempotency_key": "idem_123",
            "transaction_type": "PAYMENT",
            "reference_id": "order_123",
            "status": "POSTED",
            "reversal_of": None,
            "narrative": "Test payment",
            "created_at": "2026-01-01T00:00:00",
            "entry_id": uuid4(),
            "account_id": account_id,
            "direction": "DEBIT",
            "amount_minor": 10000,
            "currency": "INR"
        }
    ]
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.transaction_history.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        result = await transaction_history.list_transactions("merchant_1", account_id=account_id)
        
        assert "transactions" in result
        assert len(result["transactions"]) == 1
        assert result["transactions"][0]["transaction_id"] == str(txn_id)

@pytest.mark.asyncio
async def test_get_transaction():
    mock_pool = AsyncMock()
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    txn_id = uuid4()
    account_id = uuid4()
    
    conn.fetchrow.return_value = {
        "transaction_id": txn_id,
        "idempotency_key": "idem_123",
        "transaction_type": "PAYMENT",
        "reference_id": "order_123",
        "status": "POSTED",
        "reversal_of": None,
        "narrative": "Test payment",
        "created_at": "2026-01-01T00:00:00"
    }
    
    conn.fetch.return_value = [
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
    ]
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.transaction_history.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        result = await transaction_history.get_transaction(txn_id, "merchant_1")
        
        assert result["transaction_id"] == str(txn_id)
        assert len(result["entries"]) == 2

@pytest.mark.asyncio
async def test_get_transaction_not_found():
    mock_pool = AsyncMock()
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    txn_id = uuid4()
    conn.fetchrow.return_value = None
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.transaction_history.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        with pytest.raises(Exception) as exc_info:
            await transaction_history.get_transaction(txn_id, "merchant_1")
        
        assert exc_info.value.status_code == 404

@pytest.mark.asyncio
async def test_upload_settlement_file():
    mock_pool = AsyncMock()
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    conn.execute.return_value = None
    
    mock_file = MagicMock()
    mock_file.filename = "settlement.txt"
    mock_file.read = AsyncMock(return_value=b"test content")
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.settlements.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        from fastapi import UploadFile
        import uuid
        
        result = await settlements.upload_settlement_file(
            mock_file, None, "merchant_1"
        )
        
        assert "batch_id" in result
        assert result["filename"] == "settlement.txt"
        assert result["status"] == "UPLOADED"

@pytest.mark.asyncio
async def test_get_settlement_status():
    mock_pool = AsyncMock()
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    batch_id = "test-batch-123"
    conn.fetchrow.return_value = {
        "batch_id": batch_id,
        "filename": "settlement.txt",
        "status": "UPLOADED",
        "processed_count": 0,
        "failed_count": 0,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": None
    }
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.settlements.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        result = await settlements.get_settlement_status(batch_id, "merchant_1")
        
        assert result["batch_id"] == batch_id
        assert result["status"] == "UPLOADED"

@pytest.mark.asyncio
async def test_canonical_hash():
    body = {"amount": 100, "currency": "INR"}
    hash1 = canonical_hash(body)
    hash2 = canonical_hash(body)
    assert hash1 == hash2
    assert len(hash1) == 64

@pytest.mark.asyncio
async def test_webhook_signature():
    payload = b'{"test": "data"}'
    secret = "test-secret"
    
    import hmac
    import hashlib
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    
    assert verify_webhook_signature(payload, expected, secret) == True
    assert verify_webhook_signature(payload, "invalid", secret) == False

@pytest.mark.asyncio
async def test_auth_middleware():
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
    middleware = AuthMiddleware(None)
    middleware.api_keys = {"test-key": "merchant_1"}
    
    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/transactions"
    mock_request.headers = {}
    
    mock_call_next = AsyncMock()
    
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await middleware.dispatch(mock_request, mock_call_next)
    
    assert exc_info.value.status_code == 401