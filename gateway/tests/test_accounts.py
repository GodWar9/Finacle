import pytest
from unittest.mock import AsyncMock, MagicMock

from app.routes import accounts

@pytest.mark.asyncio
async def test_create_account():
    mock_pool = AsyncMock()
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    from uuid import uuid4
    account_id = uuid4()
    conn.fetchrow.return_value = {
        "account_id": account_id,
        "account_number": "ACC123",
        "account_type": "ASSET",
        "owner_ref": "merchant_1",
        "currency": "INR",
        "status": "ACTIVE",
        "created_at": "2026-01-01T00:00:00"
    }
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.accounts.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        from fastapi import Request
        request = MagicMock()
        request.state = MagicMock()
        request.state.merchant_id = "merchant_1"
        
        from app.routes.accounts import CreateAccountRequest
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
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    import asyncpg
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("duplicate")
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.accounts.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        from app.routes.accounts import CreateAccountRequest
        from fastapi import HTTPException
        
        req = CreateAccountRequest(
            account_number="ACC123",
            account_type="ASSET",
            owner_ref="merchant_1",
            currency="INR"
        )
        
        with pytest.raises(HTTPException) as exc_info:
            await accounts.create_account(req, "merchant_1")
        
        assert exc_info.value.status_code == 409

@pytest.mark.asyncio
async def test_list_accounts():
    mock_pool = AsyncMock()
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    from uuid import uuid4
    account_id = uuid4()
    conn.fetch.return_value = [
        {
            "account_id": account_id,
            "account_number": "ACC123",
            "account_type": "ASSET",
            "owner_ref": "merchant_1",
            "currency": "INR",
            "status": "ACTIVE",
            "created_at": "2026-01-01T00:00:00"
        }
    ]
    conn.fetchval.return_value = 1
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.accounts.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        result = await accounts.list_accounts("merchant_1", None, 50, 0)
        
        assert result.total == 1
        assert len(result.accounts) == 1
        assert result.accounts[0].account_number == "ACC123"

@pytest.mark.asyncio
async def test_freeze_account():
    mock_pool = AsyncMock()
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    from uuid import uuid4
    account_id = uuid4()
    conn.fetchrow.return_value = {
        "account_id": account_id,
        "account_number": "ACC123",
        "account_type": "ASSET",
        "owner_ref": "merchant_1",
        "currency": "INR",
        "status": "FROZEN",
        "created_at": "2026-01-01T00:00:00"
    }
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.accounts.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        from uuid import UUID
        result = await accounts.freeze_account(UUID(str(account_id)), "merchant_1")
        
        assert result.status == "FROZEN"

@pytest.mark.asyncio
async def test_unfreeze_account():
    mock_pool = AsyncMock()
    conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None)
    ))
    
    from uuid import uuid4
    account_id = uuid4()
    conn.fetchrow.return_value = {
        "account_id": account_id,
        "account_number": "ACC123",
        "account_type": "ASSET",
        "owner_ref": "merchant_1",
        "currency": "INR",
        "status": "ACTIVE",
        "created_at": "2026-01-01T00:00:00"
    }
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.routes.accounts.get_pg_pool", AsyncMock(return_value=mock_pool))
        
        from uuid import UUID
        result = await accounts.unfreeze_account(UUID(str(account_id)), "merchant_1")
        
        assert result.status == "ACTIVE"