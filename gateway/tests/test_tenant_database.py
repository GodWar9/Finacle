"""Real PostgreSQL regression suite; required in CI with a dedicated *_test database."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from app.routes import accounts, transaction_history


@pytest.fixture
async def tenant_pool(monkeypatch):
    connection = os.getenv("TEST_DATABASE_URL")
    if not connection:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated *_test PostgreSQL database")
    url = urlsplit(connection)
    if url.scheme not in ("postgres", "postgresql") or not url.path.endswith("_test"):
        raise RuntimeError("Refusing a non-test database")
    production = os.getenv("DATABASE_URL")
    if production and urlsplit(production).path == url.path:
        raise RuntimeError("Test database must differ from DATABASE_URL")
    schema = "tenant_test_" + uuid4().hex
    admin = await asyncpg.connect(connection)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = await asyncpg.create_pool(connection, server_settings={"search_path": schema + ",public"})
    try:
        migrations = Path(__file__).resolve().parents[2] / "db" / "migrations"
        async with pool.acquire() as conn:
            for name in ("001_initial_schema.sql", "004_settlement_files.sql", "008_tenant_ownership.sql"):
                await conn.execute((migrations / name).read_text())

        async def get_pool():
            return pool

        monkeypatch.setattr(accounts, "_get_pool", get_pool)
        monkeypatch.setattr(transaction_history, "_get_pool", get_pool)
        yield pool
    finally:
        await pool.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@pytest.mark.asyncio
async def test_account_reads_updates_and_ownership_are_isolated(tenant_pool):
    request = accounts.CreateAccountRequest(account_number="a-1", account_type="ASSET", owner_ref="customer")
    owned = await accounts.create_account(request, "merchant_a")
    request.account_number = "b-1"
    other = await accounts.create_account(request, "merchant_b")
    listing = await accounts.list_accounts("merchant_a", None, 50, 0)
    assert listing.total == 1 and listing.accounts[0].account_id == owned.account_id
    for operation in (accounts.get_account, accounts.freeze_account, accounts.unfreeze_account):
        with pytest.raises(HTTPException) as rejected:
            await operation(other.account_id, "merchant_a")
        assert rejected.value.status_code == 404
    assert (await accounts.freeze_account(owned.account_id, "merchant_a")).status == "FROZEN"
    assert (await accounts.unfreeze_account(owned.account_id, "merchant_a")).status == "ACTIVE"
    with pytest.raises(asyncpg.RaiseError):
        await tenant_pool.execute("UPDATE accounts SET merchant_id='merchant_b' WHERE account_id=$1", owned.account_id)


@pytest.mark.asyncio
async def test_history_paginates_whole_transactions_and_hides_foreign_or_mixed_entries(tenant_pool):
    ids = []
    for name, merchant in (("a-1", "merchant_a"), ("a-2", "merchant_a"), ("b-1", "merchant_b")):
        account = await accounts.create_account(
            accounts.CreateAccountRequest(account_number=name, account_type="ASSET", owner_ref="customer"), merchant
        )
        ids.append(account.account_id)
    transactions = []
    for pair in ((ids[0], ids[1]), (ids[2], ids[2]), (ids[0], ids[2])):
        transaction = uuid4()
        async with tenant_pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO transactions(transaction_id,idempotency_key,request_hash,transaction_type) "
                "VALUES($1,$2,'hash','PAYMENT')",
                transaction,
                str(transaction),
            )
            for account, direction in zip(pair, ("DEBIT", "CREDIT"), strict=True):
                await conn.execute(
                    "INSERT INTO ledger_entries(transaction_id,account_id,direction,amount_minor,currency) "
                    "VALUES($1,$2,$3,100,'INR')",
                    transaction,
                    account,
                    direction,
                )
        transactions.append(transaction)
    listing = await transaction_history.list_transactions("merchant_a", None, None, None, None, 1, 0)
    assert len(listing["transactions"]) == 1
    assert listing["transactions"][0]["transaction_id"] == transactions[0]
    assert len(listing["transactions"][0]["entries"]) == 2
    for transaction in transactions[1:]:
        with pytest.raises(HTTPException) as rejected:
            await transaction_history.get_transaction(transaction, "merchant_a")
        assert rejected.value.status_code == 404

    # The settlement export must contain one debit total for the owned transaction,
    # never two duplicate lines for its debit and credit or another tenant's data.
    from app.settlement_processor import SettlementProcessor

    batch = uuid4()
    await tenant_pool.execute(
        "INSERT INTO settlement_files(batch_id,filename,content,uploaded_by) VALUES($1,'test.txt',$2,'merchant_a')",
        batch,
        b"test",
    )
    export = Path(await SettlementProcessor(tenant_pool, None).generate_ledger_export(str(batch)))
    try:
        rows = export.read_text().splitlines()
        assert len(rows) == 1
        assert rows[0][:20].strip() == str(transactions[0])[:20]
        assert int(rows[0][20:33]) == 100
    finally:
        export.unlink()
