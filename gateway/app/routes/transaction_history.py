from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import get_current_merchant

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["transactions"])


async def _get_pool():
    from app.main import pg_pool
    return pg_pool


@router.get("/transactions", response_model=dict)
async def list_transactions(
    merchant_id: str = Depends(get_current_merchant),
    account_id: UUID | None = None,
    transaction_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
):
    pool = await _get_pool()
    conditions = []
    params = []
    param_idx = 1

    if account_id:
        conditions.append(f"le.account_id = ${param_idx}")
        params.append(account_id)
        param_idx += 1

    if transaction_type:
        conditions.append(f"t.transaction_type = ${param_idx}")
        params.append(transaction_type)
        param_idx += 1

    if start_date:
        conditions.append(f"t.created_at >= ${param_idx}")
        params.append(start_date)
        param_idx += 1

    if end_date:
        conditions.append(f"t.created_at <= ${param_idx}")
        params.append(end_date)
        param_idx += 1

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    query = f"""
        SELECT t.transaction_id, t.idempotency_key, t.transaction_type, t.reference_id,
               t.status, t.reversal_of, t.narrative, t.created_at,
               le.entry_id, le.account_id, le.direction, le.amount_minor, le.currency
        FROM transactions t
        JOIN ledger_entries le ON le.transaction_id = t.transaction_id
        {where_clause}
        ORDER BY t.created_at DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    params.extend([limit, offset])

    rows = await pool.fetch(query, *params)

    transactions = {}
    for row in rows:
        txn_id = row["transaction_id"]
        if txn_id not in transactions:
            transactions[txn_id] = {
                "transaction_id": txn_id,
                "idempotency_key": row["idempotency_key"],
                "transaction_type": row["transaction_type"],
                "reference_id": row["reference_id"],
                "status": row["status"],
                "reversal_of": row["reversal_of"],
                "narrative": row["narrative"],
                "created_at": row["created_at"].isoformat(),
                "entries": [],
            }
        transactions[txn_id]["entries"].append({
            "entry_id": row["entry_id"],
            "account_id": row["account_id"],
            "direction": row["direction"],
            "amount_minor": row["amount_minor"],
            "currency": row["currency"],
        })

    return {
        "transactions": list(transactions.values()),
        "limit": limit,
        "offset": offset,
    }


@router.get("/transactions/{transaction_id}", response_model=dict)
async def get_transaction(
    transaction_id: UUID,
    merchant_id: str = Depends(get_current_merchant),
):
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT transaction_id, idempotency_key, transaction_type, "
        "reference_id, status, reversal_of, narrative, created_at "
        "FROM transactions WHERE transaction_id = $1",
        transaction_id,
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    entries = await pool.fetch(
        "SELECT entry_id, account_id, direction, amount_minor, "
        "currency FROM ledger_entries WHERE transaction_id = $1",
        transaction_id,
    )

    return {
        "transaction_id": row["transaction_id"],
        "idempotency_key": row["idempotency_key"],
        "transaction_type": row["transaction_type"],
        "reference_id": row["reference_id"],
        "status": row["status"],
        "reversal_of": row["reversal_of"],
        "narrative": row["narrative"],
        "created_at": row["created_at"].isoformat(),
        "entries": [
            {
                "entry_id": e["entry_id"],
                "account_id": e["account_id"],
                "direction": e["direction"],
                "amount_minor": e["amount_minor"],
                "currency": e["currency"],
            }
            for e in entries
        ],
    }
