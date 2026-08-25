from fastapi import APIRouter, Header, HTTPException, status, Request, Depends
from uuid import UUID
import grpc
import structlog

from app.models.schemas import (
    PostTransactionRequest, PostTransactionResponse,
    GetBalanceResponse, ReverseTransactionRequest
)
from app.grpc_client import get_grpc_client
import ledger_pb2
from app.auth import get_current_merchant

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["transactions"])

@router.post("/transactions", response_model=PostTransactionResponse, status_code=status.HTTP_201_CREATED)
async def post_transaction(
    req: PostTransactionRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    merchant_id: str = Depends(get_current_merchant)
):
    client = await get_grpc_client()
    
    entries = [
        ledger_pb2.LedgerEntry(
            account_id=str(e.account_id),
            direction=e.direction,
            amount_minor=e.amount_minor,
            currency=e.currency,
        ) for e in req.entries
    ]
    
    grpc_req = ledger_pb2.PostTransactionRequest(
        idempotency_key=idempotency_key,
        transaction_type=req.transaction_type,
        reference_id=req.reference_id or "",
        entries=entries,
        narrative=req.narrative or "",
    )
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = await client.post_transaction(grpc_req)
            return PostTransactionResponse(
                transaction_id=UUID(resp.transaction_id),
                status=resp.status,
                posted_at_unix_ms=resp.posted_at_unix_ms,
            )
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.ABORTED and attempt < max_retries - 1:
                logger.warning("grpc_aborted_retry", attempt=attempt + 1, error=str(e), merchant_id=merchant_id)
                continue
            elif e.code() == grpc.StatusCode.ALREADY_EXISTS:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=e.details())
            elif e.code() == grpc.StatusCode.NOT_FOUND:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.details())
            elif e.code() == grpc.StatusCode.INVALID_ARGUMENT:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.details())
            else:
                logger.error("grpc_error", error=str(e), code=e.code(), merchant_id=merchant_id)
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"ledger core error: {e.details()}")
    
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="max retries exceeded")

@router.get("/accounts/{account_id}/balance", response_model=GetBalanceResponse)
async def get_balance(account_id: UUID, merchant_id: str = Depends(get_current_merchant)):
    client = await get_grpc_client()
    
    grpc_req = ledger_pb2.GetBalanceRequest(account_id=str(account_id))
    
    try:
        resp = await client.get_balance(grpc_req)
        return GetBalanceResponse(
            account_id=UUID(resp.account_id),
            balance_minor=resp.balance_minor,
            currency=resp.currency,
            as_of_unix_ms=resp.as_of_unix_ms,
        )
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.details())
        logger.error("grpc_error", error=str(e), code=e.code(), merchant_id=merchant_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"ledger core error: {e.details()}")

@router.post("/transactions/{transaction_id}/reverse", response_model=PostTransactionResponse)
async def reverse_transaction(
    transaction_id: UUID,
    req: ReverseTransactionRequest,
    merchant_id: str = Depends(get_current_merchant)
):
    client = await get_grpc_client()
    
    grpc_req = ledger_pb2.ReverseTransactionRequest(
        transaction_id=str(transaction_id),
        idempotency_key=req.idempotency_key,
        reason=req.reason,
    )
    
    try:
        resp = await client.reverse_transaction(grpc_req)
        return PostTransactionResponse(
            transaction_id=UUID(resp.transaction_id),
            status=resp.status,
            posted_at_unix_ms=resp.posted_at_unix_ms,
        )
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.ALREADY_EXISTS:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=e.details())
        elif e.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.details())
        logger.error("grpc_error", error=str(e), code=e.code(), merchant_id=merchant_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"ledger core error: {e.details()}")