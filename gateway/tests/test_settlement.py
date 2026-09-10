import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime


class MockAsyncCtx:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, *a):
        return None


@pytest.mark.asyncio
async def test_settlement_processor_processes_pending():
    from app.settlement_processor import SettlementProcessor

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCtx(mock_conn))
    mock_conn.fetchrow = AsyncMock(return_value=None)

    mock_kafka = AsyncMock()

    processor = SettlementProcessor(mock_pool, mock_kafka, poll_interval=1)
    processor.running = False

    await processor.process_pending()
    mock_conn.fetchrow.assert_called_once()


@pytest.mark.asyncio
async def test_publish_recon_events():
    from app.settlement_processor import SettlementProcessor

    batch_id = str(uuid4())
    exception_id = uuid4()
    ledger_txn_id = uuid4()

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=MockAsyncCtx(mock_conn))

    mock_conn.fetchrow = AsyncMock(return_value={
        "matched_count": 100,
        "exception_count": 2,
    })
    mock_conn.fetch = AsyncMock(return_value=[
        {
            "exception_id": exception_id,
            "exception_type": "AMOUNT_MISMATCH",
            "ledger_transaction_id": ledger_txn_id,
        }
    ])

    mock_kafka = AsyncMock()

    processor = SettlementProcessor(mock_pool, mock_kafka)
    count = await processor.publish_recon_events(batch_id)

    assert count == 1
    assert mock_kafka.send_and_wait.call_count == 2

    first_call = mock_kafka.send_and_wait.call_args_list[0]
    assert first_call[0][0] == "reconciliation.exception.raised"

    second_call = mock_kafka.send_and_wait.call_args_list[1]
    assert second_call[0][0] == "reconciliation.batch.completed"
