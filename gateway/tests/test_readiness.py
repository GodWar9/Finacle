import asyncio
from unittest.mock import AsyncMock

import grpc
import pytest
from fastapi import HTTPException

from app.grpc_client import LedgerGrpcClient
from app.routes import health


@pytest.mark.asyncio
async def test_readiness_requires_all_dependencies_without_disclosing_errors(monkeypatch):
    for name in ("_database_ready", "_redis_ready", "_ledger_ready"):
        monkeypatch.setattr(health, name, AsyncMock())
    assert (await health.readiness_check())["checks"] == {"database": "ok", "redis": "ok", "ledger": "ok"}
    health._ledger_ready.side_effect = RuntimeError("private-token-and-hostname")
    with pytest.raises(HTTPException) as failure:
        await health.readiness_check()
    assert failure.value.status_code == 503
    assert failure.value.detail == {
        "status": "not ready",
        "checks": {"database": "ok", "redis": "ok", "ledger": "unavailable"},
    }


@pytest.mark.asyncio
async def test_database_probe_closes_connection_even_when_schema_is_missing(monkeypatch):
    connection = AsyncMock()
    connection.execute.side_effect = RuntimeError("missing migration")
    monkeypatch.setattr(health.asyncpg, "connect", AsyncMock(return_value=connection))
    assert await health._probe(health._database_ready) == "unavailable"
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_is_bounded(monkeypatch):
    async def timeout(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout)
    assert await health._probe(AsyncMock()) == "unavailable"


class RpcFailure(grpc.RpcError):
    def __init__(self, code):
        self.status = code

    def code(self):
        return self.status


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.UNAVAILABLE])
async def test_ledger_readiness_rejects_wrong_token_or_unavailable_service(status):
    client = LedgerGrpcClient("unused")
    client.get_balance = AsyncMock(side_effect=RpcFailure(status))
    with pytest.raises(grpc.RpcError):
        await client.check_ready()
    client.get_balance.side_effect = RpcFailure(grpc.StatusCode.NOT_FOUND)
    await client.check_ready()
