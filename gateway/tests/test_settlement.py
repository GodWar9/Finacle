import asyncio
import subprocess
import threading
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


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
async def test_reconciliation_subprocess_does_not_block_event_loop(monkeypatch, tmp_path):
    from app.settlement_processor import SettlementProcessor

    ledger = tmp_path / "ledger.txt"
    ledger.write_text("synthetic")
    conn = AsyncMock()
    conn.fetchrow.return_value = {"exception_count": 0}
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=MockAsyncCtx(conn))
    processor = SettlementProcessor(pool)
    processor.generate_ledger_export = AsyncMock(return_value=str(ledger))
    loop_progress = threading.Event()

    def engine(command, **kwargs):
        assert loop_progress.wait(timeout=2), "Reconciliation blocked the event loop"
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    async def advance_loop():
        await asyncio.sleep(0)
        loop_progress.set()

    monkeypatch.setattr(subprocess, "run", engine)
    result, _ = await asyncio.gather(processor.run_reconciliation(str(uuid4()), b"synthetic"), advance_loop())
    assert result == 0
    assert not ledger.exists()
