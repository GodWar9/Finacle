import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime

from app.routes import reconciliation


@pytest.mark.asyncio
async def test_list_reconciliation_exceptions():
    mock_pool = AsyncMock()
    exception_id = uuid4()
    mock_pool.fetch = AsyncMock(return_value=[
        {
            "exception_id": exception_id,
            "batch_id": "batch-001",
            "exception_type": "AMOUNT_MISMATCH",
            "ledger_transaction_id": uuid4(),
            "bank_reference": "ref-123",
            "ledger_amount_minor": 10000,
            "bank_amount_minor": 9900,
            "resolved": False,
            "detected_at": datetime(2026, 1, 1, 0, 0, 0),
        }
    ])

    with patch("app.routes.reconciliation._get_pool", return_value=mock_pool):
        result = await reconciliation.list_reconciliation_exceptions("merchant_1")

        assert len(result["exceptions"]) == 1
        assert result["exceptions"][0]["exception_type"] == "AMOUNT_MISMATCH"
        assert result["exceptions"][0]["batch_id"] == "batch-001"
        assert result["exceptions"][0]["ledger_amount_minor"] == 10000


@pytest.mark.asyncio
async def test_list_reconciliation_exceptions_empty():
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    with patch("app.routes.reconciliation._get_pool", return_value=mock_pool):
        result = await reconciliation.list_reconciliation_exceptions("merchant_1")

        assert result["exceptions"] == []


@pytest.mark.asyncio
async def test_list_reconciliation_exceptions_with_filters():
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    with patch("app.routes.reconciliation._get_pool", return_value=mock_pool):
        result = await reconciliation.list_reconciliation_exceptions(
            "merchant_1", batch_id="batch-002", exception_type="MISSING_IN_LEDGER"
        )

        call_args = mock_pool.fetch.call_args
        query = call_args[0][0]
        assert "batch_id" in query
        assert "exception_type" in query
