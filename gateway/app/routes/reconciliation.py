
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_current_merchant

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["reconciliation"])


async def _get_pool():
    from app.main import pg_pool
    return pg_pool


@router.get("/reconciliation/exceptions")
async def list_reconciliation_exceptions(
    merchant_id: str = Depends(get_current_merchant),
    batch_id: str | None = None,
    exception_type: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    """List reconciliation exceptions, optionally filtered by batch or type."""
    pool = await _get_pool()
    try:
        conditions = []
        params = []
        idx = 1

        if batch_id:
            conditions.append(f"batch_id = ${idx}")
            params.append(batch_id)
            idx += 1

        if exception_type:
            conditions.append(f"exception_type = ${idx}")
            params.append(exception_type)
            idx += 1

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT exception_id, batch_id, exception_type, ledger_transaction_id,
                   bank_reference, ledger_amount_minor, bank_amount_minor,
                   resolved, detected_at
            FROM reconciliation_exceptions
            {where}
            ORDER BY detected_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        params.extend([limit, offset])

        rows = await pool.fetch(query, *params)

        return {
            "exceptions": [
                {
                    "exception_id": str(r["exception_id"]),
                    "batch_id": r["batch_id"],
                    "exception_type": r["exception_type"],
                    "ledger_transaction_id": str(r["ledger_transaction_id"]) if r["ledger_transaction_id"] else None,
                    "bank_reference": r["bank_reference"],
                    "ledger_amount_minor": r["ledger_amount_minor"],
                    "bank_amount_minor": r["bank_amount_minor"],
                    "resolved": r["resolved"],
                    "detected_at": r["detected_at"].isoformat(),
                }
                for r in rows
            ],
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error("list_recon_exceptions_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
