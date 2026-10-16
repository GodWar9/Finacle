from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import get_current_merchant

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["accounts"])


async def _get_pool():
    from app.main import pg_pool

    return pg_pool


class CreateAccountRequest(BaseModel):
    account_number: str = Field(min_length=1, max_length=50)
    account_type: str = Field(pattern="^(ASSET|LIABILITY|EQUITY|REVENUE|EXPENSE)$")
    owner_ref: str
    currency: str = Field(default="INR", min_length=3, max_length=3)


class AccountResponse(BaseModel):
    account_id: UUID
    account_number: str
    account_type: str
    owner_ref: str
    currency: str
    status: str
    created_at: str


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
    total: int


ACCT_COLS = "account_id, account_number, account_type, owner_ref, currency, status, created_at"


@router.post("/accounts", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(req: CreateAccountRequest, merchant_id: str = Depends(get_current_merchant)):
    pool = await _get_pool()
    try:
        row = await pool.fetchrow(
            f"INSERT INTO accounts (account_number, account_type, owner_ref, currency, merchant_id) "
            f"VALUES ($1, $2, $3, $4, $5) RETURNING {ACCT_COLS}",
            req.account_number,
            req.account_type,
            req.owner_ref,
            req.currency,
            merchant_id,
        )
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create account")
        return AccountResponse(
            account_id=row["account_id"],
            account_number=row["account_number"],
            account_type=row["account_type"],
            owner_ref=row["owner_ref"],
            currency=row["currency"],
            status=row["status"],
            created_at=row["created_at"].isoformat(),
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account number already exists")


@router.get("/accounts/{account_id}", response_model=AccountResponse)
async def get_account(account_id: UUID, merchant_id: str = Depends(get_current_merchant)):
    pool = await _get_pool()
    row = await pool.fetchrow(
        f"SELECT {ACCT_COLS} FROM accounts WHERE account_id = $1 AND merchant_id = $2",
        account_id,
        merchant_id,
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return AccountResponse(
        account_id=row["account_id"],
        account_number=row["account_number"],
        account_type=row["account_type"],
        owner_ref=row["owner_ref"],
        currency=row["currency"],
        status=row["status"],
        created_at=row["created_at"].isoformat(),
    )


@router.get("/accounts", response_model=AccountListResponse)
async def list_accounts(
    merchant_id: str = Depends(get_current_merchant),
    owner_ref: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    pool = await _get_pool()
    if owner_ref:
        rows = await pool.fetch(
            f"SELECT {ACCT_COLS} FROM accounts WHERE merchant_id = $1 AND owner_ref = $2 "
            "ORDER BY created_at, account_id LIMIT $3 OFFSET $4",
            merchant_id,
            owner_ref,
            limit,
            offset,
        )
        total = await pool.fetchval(
            "SELECT COUNT(*) FROM accounts WHERE merchant_id = $1 AND owner_ref = $2",
            merchant_id,
            owner_ref,
        )
    else:
        rows = await pool.fetch(
            f"SELECT {ACCT_COLS} FROM accounts WHERE merchant_id = $1 "
            "ORDER BY created_at, account_id LIMIT $2 OFFSET $3",
            merchant_id,
            limit,
            offset,
        )
        total = await pool.fetchval("SELECT COUNT(*) FROM accounts WHERE merchant_id = $1", merchant_id)

    accounts = [
        AccountResponse(
            account_id=row["account_id"],
            account_number=row["account_number"],
            account_type=row["account_type"],
            owner_ref=row["owner_ref"],
            currency=row["currency"],
            status=row["status"],
            created_at=row["created_at"].isoformat(),
        )
        for row in rows
    ]
    return AccountListResponse(accounts=accounts, total=total or 0)


@router.post("/accounts/{account_id}/freeze", response_model=AccountResponse)
async def freeze_account(account_id: UUID, merchant_id: str = Depends(get_current_merchant)):
    pool = await _get_pool()
    row = await pool.fetchrow(
        f"UPDATE accounts SET status = 'FROZEN' WHERE account_id = $1 AND merchant_id = $2 RETURNING {ACCT_COLS}",
        account_id,
        merchant_id,
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return AccountResponse(
        account_id=row["account_id"],
        account_number=row["account_number"],
        account_type=row["account_type"],
        owner_ref=row["owner_ref"],
        currency=row["currency"],
        status=row["status"],
        created_at=row["created_at"].isoformat(),
    )


@router.post("/accounts/{account_id}/unfreeze", response_model=AccountResponse)
async def unfreeze_account(account_id: UUID, merchant_id: str = Depends(get_current_merchant)):
    pool = await _get_pool()
    row = await pool.fetchrow(
        f"UPDATE accounts SET status = 'ACTIVE' WHERE account_id = $1 AND merchant_id = $2 "
        f"AND status = 'FROZEN' RETURNING {ACCT_COLS}",
        account_id,
        merchant_id,
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found or not frozen")
    return AccountResponse(
        account_id=row["account_id"],
        account_number=row["account_number"],
        account_type=row["account_type"],
        owner_ref=row["owner_ref"],
        currency=row["currency"],
        status=row["status"],
        created_at=row["created_at"].isoformat(),
    )
