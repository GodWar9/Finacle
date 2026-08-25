import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID
from datetime import datetime

from app.routes import accounts
from app.routes.accounts import CreateAccountRequest

class MockAsyncCM:
    def __init__(self, connection):
        self.conn = connection
    
    async def __aenter__(self):
        return self.conn
    
    async def __aexit__(self, *args):
        return None

@pytest.mark.asyncio
async def test_create_account():
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value={
        "account_id": uuid4(),
        "account_number": "ACC123",
        "account_type": "ASSET",
        "owner_ref": "merchant_1",
        "currency": "INR",
        "status": "ACTIVE",
        "created_at": datetime(2026, 1, 1, 0, 0, 0)
    })
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    with patch("app.routes.accounts.get_pg_pool", return_value=mock_pool):
        req = CreateAccountRequest(
            account_number="ACC123",
            account_type="ASSET",
            owner_ref="merchant_1",
            currency="INR"
        )
        
        result = await accounts.create_account(req, "merchant_1")
        
        assert result.account_number == "ACC123"
        assert result.account_type == "ASSET"
        assert result.owner_ref == "merchant_1"
        assert result.currency == "INR"
        assert result.status == "ACTIVE"

@pytest.mark.asyncio
async def test_create_account_duplicate():
    mock_pool = AsyncMock()
    import asyncpg
    mock_pool.fetchrow = AsyncMock(side_effect=asyncpg.UniqueViolationError("duplicate"))
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    with patch("app.routes.accounts.get_pg_pool", return_value=mock_pool):
        req = CreateAccountRequest(
            account_number="ACC123",
            account_type="ASSET",
            owner_ref="merchant_1",
            currency="INR"
        )
        
        with pytest.raises(Exception) as exc_info:
            await accounts.create_account(req, "merchant_1")
        
        assert exc_info.value.status_code == 409

@pytest.mark.asyncio
async def test_list_accounts():
    mock_pool = AsyncMock()
    account_id = uuid4()
    mock_pool.fetch = AsyncMock(return_value=[{
        "account_id": account_id,
        "account_number": "ACC123",
        "account_type": "ASSET",
        "owner_ref": "merchant_1",
        "currency": "INR",
        "status": "ACTIVE",
        "created_at": datetime(2026, 1, 1, 0, 0, 0)
    }])
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    with patch("app.routes.accounts.get_pg_pool", return_value=mock_pool):
        result = await accounts.list_accounts("merchant_1", None, 50, 0)
        
        assert result.total == 1
        assert len(result.accounts) == 1
        assert result.accounts[0].account_number == "ACC123"

@pytest.mark.asyncio
async def test_freeze_account():
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value={
        "account_id": uuid4(),
        "account_number": "ACC123",
        "account_type": "ASSET",
        "owner_ref": "merchant_1",
        "currency": "INR",
        "status": "FROZEN",
        "created_at": datetime(2026, 1, 1, 0, 0, 0)
    })
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    with patch("app.routes.accounts.get_pg_pool", return_value=mock_pool):
        result = await accounts.freeze_account(uuid4(), "merchant_1")
        
        assert result.status == "FROZEN"

@pytest.mark.asyncio
async def test_unfreeze_account():
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value={
        "account_id": uuid4(),
        "account_number": "ACC123",
        "account_type": "ASSET",
        "owner_ref": "merchant_1",
        "currency": "INR",
        "status": "ACTIVE",
        "created_at": datetime(2026, 1, 1, 0, 0, 0)
    })
    mock_pool.close = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCM(AsyncMock()))
    
    with patch("app.routes.accounts.get_pg_pool", return_value=mock_pool):
        result = await accounts.unfreeze_account(uuid4(), "merchant_1")
        
        assert result.status == "ACTIVE"